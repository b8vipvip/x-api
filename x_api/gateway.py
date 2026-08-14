from __future__ import annotations

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any, AsyncIterator, Iterable

import httpx

from .store import ProviderStore


@dataclass
class GatewayResult:
    ok: bool
    content: str = ""
    provider_id: int | None = None
    provider_name: str = ""
    model: str = ""
    protocol: str = ""
    latency_ms: int = 0
    error: str = ""
    attempts: list[dict[str, Any]] | None = None


def model_candidates(provider: dict[str, Any], requested_model: str = "", *, vision: bool = False) -> list[str]:
    pool: list[str] = []
    if requested_model:
        pool.append(requested_model.strip())
    if vision:
        pool.append(str(provider.get("main_vision_model") or ""))
        pool.extend(provider.get("backup_vision_models") or [])
    else:
        pool.append(str(provider.get("main_text_model") or ""))
        pool.extend(provider.get("backup_text_models") or [])
    return list(dict.fromkeys(x.strip() for x in pool if str(x).strip()))


def protocol_candidates(provider: dict[str, Any], model: str, *, streaming: bool = False) -> list[str]:
    configured = [x for x in provider.get("protocol_order", []) if x in {"chat", "responses", "legacy"}]
    caps = provider.get("model_capabilities") or {}
    model_caps = caps.get(model) if isinstance(caps, dict) else None
    if isinstance(model_caps, dict):
        successful = [x for x in configured if model_caps.get(x)]
        remainder = [x for x in configured if x not in successful]
        configured = successful + remainder
    configured = configured or ["chat", "responses", "legacy"]
    if streaming:
        # Chat Completions SSE is the portable baseline used by chat2api/FDEX.
        configured = [x for x in configured if x == "chat"] or ["chat"]
    return configured


def _headers(api_key: str, *, stream: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
        "User-Agent": "X-API/0.1",
    }
    return headers


def _url(base_url: str, protocol: str) -> str:
    base = base_url.rstrip("/")
    if protocol == "responses":
        return f"{base}/responses"
    if protocol == "legacy":
        return f"{base}/completions"
    return f"{base}/chat/completions"


def _flatten_prompt(messages: Iterable[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in messages:
        role = str(item.get("role") or "user")
        content = item.get("content")
        if isinstance(content, str):
            lines.append(f"{role}: {content}")
    return "\n".join(lines) + "\nassistant:"


def _payload(protocol: str, model: str, messages: list[dict[str, Any]], max_tokens: int, temperature: float, *, stream: bool) -> dict[str, Any]:
    if protocol == "responses":
        return {
            "model": model,
            "input": messages,
            "max_output_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
    if protocol == "legacy":
        return {
            "model": model,
            "prompt": _flatten_prompt(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
    return {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }


def _extract_content(protocol: str, data: dict[str, Any]) -> str:
    if protocol == "responses":
        value = data.get("output_text")
        if isinstance(value, str):
            return value.strip()
        parts: list[str] = []
        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            parts.append(part["text"])
        return "".join(parts).strip()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    if protocol == "legacy":
        value = choices[0].get("text")
        return value.strip() if isinstance(value, str) else ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    value = message.get("content")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(str(x.get("text") or x.get("content") or "") for x in value if isinstance(x, dict)).strip()
    return ""


def _safe_error(response: httpx.Response) -> str:
    text = response.text.replace("\r", " ").replace("\n", " ").strip()
    return text[:400] or response.reason_phrase


async def complete(
    store: ProviderStore,
    messages: list[dict[str, Any]],
    *,
    requested_model: str = "",
    max_tokens: int = 1200,
    temperature: float = 0.5,
) -> GatewayResult:
    attempts: list[dict[str, Any]] = []
    for provider in store.list_providers(include_secret=True, enabled_only=True):
        if not provider.get("api_key"):
            attempts.append({"provider": provider["name"], "error": "API Key 未配置"})
            continue
        models = model_candidates(provider, requested_model)
        if not models:
            attempts.append({"provider": provider["name"], "error": "没有可用文本模型"})
            continue
        for model in models:
            for protocol in protocol_candidates(provider, model):
                started = perf_counter()
                try:
                    timeout = httpx.Timeout(float(provider.get("timeout_seconds") or 60), connect=15.0)
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                        response = await client.post(
                            _url(provider["base_url"], protocol),
                            headers=_headers(provider["api_key"]),
                            json=_payload(protocol, model, messages, max_tokens, temperature, stream=False),
                        )
                    latency = int((perf_counter() - started) * 1000)
                    if not response.is_success:
                        attempts.append({"provider": provider["name"], "model": model, "protocol": protocol, "status": response.status_code, "latency_ms": latency, "error": _safe_error(response)})
                        continue
                    try:
                        content = _extract_content(protocol, response.json())
                    except (ValueError, TypeError):
                        content = ""
                    if not content:
                        attempts.append({"provider": provider["name"], "model": model, "protocol": protocol, "status": response.status_code, "latency_ms": latency, "error": "响应中没有可识别正文"})
                        continue
                    store.record_probe(provider["id"], ok=True, status=f"可用：{model} / {protocol}", latency_ms=latency)
                    return GatewayResult(True, content, provider["id"], provider["name"], model, protocol, latency, attempts=attempts)
                except httpx.HTTPError as exc:
                    latency = int((perf_counter() - started) * 1000)
                    attempts.append({"provider": provider["name"], "model": model, "protocol": protocol, "latency_ms": latency, "error": str(exc)[:300]})
    error = "；".join(f"{x.get('provider','?')} {x.get('model','')} {x.get('protocol','')}：{x.get('error') or x.get('status')}" for x in attempts[-8:])
    return GatewayResult(False, error=error or "没有已启用且完整配置的供应商", attempts=attempts)


async def stream_chat(
    store: ProviderStore,
    messages: list[dict[str, Any]],
    *,
    requested_model: str = "",
    max_tokens: int = 1200,
    temperature: float = 0.5,
) -> AsyncIterator[dict[str, Any]]:
    """Yield normalized lifecycle events and raw upstream SSE payloads.

    Failover is allowed only before the first content-bearing upstream event. Once
    a provider starts producing answer content, a later network failure is emitted
    as an error instead of silently switching and duplicating a partial answer.
    """
    attempts: list[dict[str, Any]] = []
    for provider in store.list_providers(include_secret=True, enabled_only=True):
        if not provider.get("api_key"):
            continue
        for model in model_candidates(provider, requested_model):
            started = perf_counter()
            answer_started = False
            try:
                timeout_seconds = float(provider.get("timeout_seconds") or 60)
                timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    async with client.stream(
                        "POST",
                        _url(provider["base_url"], "chat"),
                        headers=_headers(provider["api_key"], stream=True),
                        json=_payload("chat", model, messages, max_tokens, temperature, stream=True),
                    ) as response:
                        if response.status_code >= 400:
                            raw = (await response.aread()).decode("utf-8", errors="replace")[:400]
                            attempts.append({"provider": provider["name"], "model": model, "status": response.status_code, "error": raw})
                            continue
                        content_type = response.headers.get("content-type", "").lower()
                        if "text/event-stream" not in content_type:
                            raw = (await response.aread()).decode("utf-8", errors="replace")
                            try:
                                content = _extract_content("chat", json.loads(raw))
                            except (ValueError, TypeError):
                                content = ""
                            if not content:
                                attempts.append({"provider": provider["name"], "model": model, "error": "上游未返回 SSE 或可识别 JSON"})
                                continue
                            answer_started = True
                            yield {"type": "fallback_json", "content": content, "provider": provider["name"], "provider_id": provider["id"], "model": model}
                        else:
                            async for line in response.aiter_lines():
                                stripped = line.strip()
                                if not stripped or stripped.startswith(":") or stripped.startswith("event:"):
                                    continue
                                if not stripped.startswith("data:"):
                                    continue
                                raw = stripped[5:].strip()
                                if raw == "[DONE]":
                                    break
                                try:
                                    data = json.loads(raw)
                                except ValueError:
                                    continue
                                if isinstance(data, dict):
                                    choices = data.get("choices")
                                    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                                        delta = choices[0].get("delta")
                                        if isinstance(delta, dict) and delta.get("content"):
                                            answer_started = True
                                    yield {"type": "upstream", "data": data, "provider": provider["name"], "provider_id": provider["id"], "model": model}
                latency = int((perf_counter() - started) * 1000)
                store.record_probe(provider["id"], ok=True, status=f"可用：{model} / chat", latency_ms=latency)
                yield {"type": "done", "provider": provider["name"], "provider_id": provider["id"], "model": model, "latency_ms": latency}
                return
            except httpx.HTTPError as exc:
                attempts.append({"provider": provider["name"], "model": model, "error": str(exc)[:300]})
                if answer_started:
                    yield {"type": "error", "message": f"{provider['name']} 流式连接中断：{str(exc)[:250]}"}
                    return
                continue
    detail = "；".join(f"{x.get('provider')} {x.get('model','')}：{x.get('error') or x.get('status')}" for x in attempts[-6:])
    yield {"type": "error", "message": detail or "没有可用的 AI 供应商"}
