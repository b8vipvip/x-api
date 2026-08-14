from __future__ import annotations

import re
from time import perf_counter
from typing import Any

import httpx

from .gateway import _extract_content, _headers, _payload, _url
from .store import ProviderStore, normalize_base_url


def api_roots(base_url: str) -> list[str]:
    base = normalize_base_url(base_url)
    if not base:
        return []
    roots: list[str] = []
    if base.endswith("/v1"):
        roots.extend([base, base[:-3].rstrip("/")])
    else:
        roots.extend([base + "/v1", base])
    return list(dict.fromkeys(x for x in roots if x))


def _extract_models(data: Any) -> list[str]:
    items: Any = None
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            items = data["data"]
        elif isinstance(data.get("models"), list):
            items = data["models"]
    elif isinstance(data, list):
        items = data
    models: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, str):
                models.append(item)
            elif isinstance(item, dict):
                value = item.get("id") or item.get("name") or item.get("model")
                if value:
                    models.append(str(value))
    return list(dict.fromkeys(x.strip() for x in models if x.strip()))


async def discover_models(provider: dict[str, Any]) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    api_key = provider.get("api_key", "")
    timeout = float(provider.get("timeout_seconds") or 60)
    for root in api_roots(provider["base_url"]):
        url = root.rstrip("/") + "/models"
        started = perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url, headers=_headers(api_key))
            latency = int((perf_counter() - started) * 1000)
            item = {"url": url, "status": response.status_code, "latency_ms": latency}
            if response.is_success:
                try:
                    models = _extract_models(response.json())
                except ValueError:
                    models = []
                item["models"] = models
                attempts.append(item)
                if models:
                    return {"ok": True, "models": models, "attempts": attempts, "latency_ms": latency}
            else:
                item["error"] = response.text[:250]
            attempts.append(item)
        except httpx.HTTPError as exc:
            attempts.append({"url": url, "error": str(exc)[:250]})
    return {"ok": False, "models": [], "attempts": attempts, "latency_ms": 0}


async def _probe_protocol(provider: dict[str, Any], model: str, protocol: str) -> dict[str, Any]:
    marker = "XAPI_OK_7F3A"
    messages = [{"role": "user", "content": f"Reply with exactly {marker}"}]
    started = perf_counter()
    try:
        timeout = float(provider.get("timeout_seconds") or 60)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                _url(provider["base_url"], protocol),
                headers=_headers(provider.get("api_key", "")),
                json=_payload(protocol, model, messages, 24, 0.0, stream=False),
            )
        latency = int((perf_counter() - started) * 1000)
        if not response.is_success:
            return {"ok": False, "protocol": protocol, "model": model, "status": response.status_code, "latency_ms": latency, "error": response.text[:300]}
        try:
            content = _extract_content(protocol, response.json())
        except ValueError:
            content = ""
        return {
            "ok": bool(content),
            "round_trip": marker.lower() in content.lower() if content else False,
            "protocol": protocol,
            "model": model,
            "status": response.status_code,
            "latency_ms": latency,
            "preview": content[:180],
            "error": "" if content else "HTTP 成功但未解析到回答正文",
        }
    except httpx.TimeoutException:
        return {"ok": False, "protocol": protocol, "model": model, "latency_ms": int((perf_counter() - started) * 1000), "error": "请求超时"}
    except httpx.HTTPError as exc:
        return {"ok": False, "protocol": protocol, "model": model, "latency_ms": int((perf_counter() - started) * 1000), "error": str(exc)[:300]}


def _version_key(model: str) -> tuple[int, ...]:
    values = [int(x) for x in re.findall(r"\d+", model)]
    return tuple(values[:6]) if values else (0,)


async def probe_provider(store: ProviderStore, provider_id: int, *, mode: str = "ordinary", auto_apply: bool = True) -> dict[str, Any]:
    provider = store.get_provider(provider_id, include_secret=True)
    started = perf_counter()
    discovery = await discover_models(provider) if mode == "deep" else {"ok": False, "models": [], "attempts": []}
    configured = [provider.get("main_text_model", ""), *(provider.get("backup_text_models") or [])]
    discovered = discovery.get("models") or []
    if mode == "deep" and discovered:
        models = discovered
    else:
        models = [x for x in configured if x]
    models = list(dict.fromkeys(models))
    if mode != "deep":
        models = models[:1]
    if not models:
        store.record_probe(provider_id, ok=False, status="失败：没有可测试文本模型", latency_ms=0)
        return {"ok": False, "mode": mode, "error": "没有可测试文本模型", "discovery": discovery, "models": []}

    results: list[dict[str, Any]] = []
    capabilities: dict[str, Any] = dict(provider.get("model_capabilities") or {})
    protocols = provider.get("protocol_order") or ["chat", "responses", "legacy"]
    for model in models:
        model_caps = dict(capabilities.get(model) or {})
        for protocol in protocols:
            result = await _probe_protocol(provider, model, protocol)
            results.append(result)
            model_caps[protocol] = bool(result["ok"])
            if mode != "deep" and result["ok"]:
                break
        capabilities[model] = model_caps

    usable = [model for model in models if any(bool(capabilities.get(model, {}).get(p)) for p in protocols)]
    elapsed = int((perf_counter() - started) * 1000)
    successful_protocols: list[str] = []
    for protocol in protocols:
        if any(bool(capabilities.get(model, {}).get(protocol)) for model in usable):
            successful_protocols.append(protocol)
    successful_protocols.extend(p for p in protocols if p not in successful_protocols)

    main = provider.get("main_text_model", "")
    backups = provider.get("backup_text_models") or []
    if mode == "deep" and auto_apply and usable:
        if main not in usable:
            main = sorted(usable, key=_version_key, reverse=True)[0]
        backups = [x for x in sorted(usable, key=_version_key, reverse=True) if x != main]

    ok = bool(usable)
    status = f"可用：{len(usable)} 个文本模型" if ok else "失败：未找到可用文本模型"
    store.record_probe(
        provider_id,
        ok=ok,
        status=status,
        latency_ms=elapsed,
        capabilities=capabilities,
        main_text_model=main,
        backup_text_models=backups,
        protocol_order=successful_protocols,
    )
    return {
        "ok": ok,
        "mode": mode,
        "provider_id": provider_id,
        "provider_name": provider["name"],
        "latency_ms": elapsed,
        "discovery": discovery,
        "results": results,
        "usable_models": usable,
        "main_text_model": main,
        "backup_text_models": backups,
        "protocol_order": successful_protocols,
    }
