from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterable

import httpx

from .store import (
    ProviderStore,
    audio_model_candidates,
    image_model_candidates,
    normalize_base_url,
    text_model_candidates,
)

TASK_AUTO = "auto"
TASK_TEXT = "text"
TASK_VISION = "vision"
TASK_IMAGE = "image_generation"
TASK_AUDIO = "audio"
TASKS = {TASK_AUTO, TASK_TEXT, TASK_VISION, TASK_IMAGE, TASK_AUDIO}

_IMAGE_INTENT = re.compile(
    r"(?:生成|画|绘制|创建|制作|做一张|设计).{0,12}(?:图片|图像|照片|海报|头像|插画|壁纸|logo|图标)|"
    r"(?:图片|图像|照片|海报|头像|插画|壁纸|logo|图标).{0,12}(?:生成|画|绘制|创建|制作|设计)|"
    r"\b(?:generate|create|draw|make|design)\b.{0,24}\b(?:image|picture|photo|poster|avatar|illustration|wallpaper|logo|icon)\b",
    re.IGNORECASE,
)
_AUDIO_INTENT = re.compile(
    r"(?:语音对话|语音聊天|语音回复|用语音|说给我听|读给我听|朗读|念一下|语音播报|声音回复)|"
    r"\b(?:voice\s*chat|voice\s*reply|speak\s*(?:it|this|to me)?|read\s*aloud)\b",
    re.IGNORECASE,
)
_TEXT_DISCUSSION_HINT = re.compile(r"(?:代码|教程|怎么|如何|原理|接口|API|文档|实现|开发|示例)", re.IGNORECASE)


@dataclass
class MediaResult:
    kind: str
    url: str = ""
    data_base64: str = ""
    mime_type: str = ""
    transcript: str = ""
    revised_prompt: str = ""


@dataclass
class CapabilityResult:
    ok: bool
    task: str
    content: str = ""
    provider_id: int | None = None
    provider_name: str = ""
    model: str = ""
    latency_ms: int = 0
    media: list[MediaResult] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    fallback_from: str = ""


def detect_task(
    prompt: str,
    *,
    requested_task: str = TASK_AUTO,
    has_images: bool = False,
    has_audio: bool = False,
) -> tuple[str, bool]:
    requested = (requested_task or TASK_AUTO).strip().lower()
    if requested not in TASKS:
        requested = TASK_AUTO
    if requested != TASK_AUTO:
        return requested, True
    if has_audio:
        return TASK_AUDIO, False
    if has_images:
        return TASK_VISION, False
    text = (prompt or "").strip()
    if _IMAGE_INTENT.search(text) and not _TEXT_DISCUSSION_HINT.search(text):
        return TASK_IMAGE, False
    if _AUDIO_INTENT.search(text):
        return TASK_AUDIO, False
    return TASK_TEXT, False


def api_roots(base_url: str) -> list[str]:
    base = normalize_base_url(base_url)
    if base.endswith("/v1"):
        roots = [base, base[:-3].rstrip("/")]
    else:
        roots = [base + "/v1", base]
    return list(dict.fromkeys(x for x in roots if x))


def _headers(api_key: str, accept: str = "application/json") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": accept,
        "User-Agent": "X-API-Multimodal/0.2",
    }


def build_chat_messages(
    system: str | None,
    prompt: str,
    *,
    images: list[dict[str, str]] | None = None,
    audio: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system and system.strip():
        messages.append({"role": "system", "content": system.strip()})
    if not images and not audio:
        messages.append({"role": "user", "content": prompt})
        return messages
    content: list[dict[str, Any]] = []
    if prompt.strip():
        content.append({"type": "text", "text": prompt})
    for image in images or []:
        url = str(image.get("url") or "").strip()
        if url:
            detail = str(image.get("detail") or "auto").lower()
            if detail not in {"auto", "low", "high"}:
                detail = "auto"
            content.append({"type": "image_url", "image_url": {"url": url, "detail": detail}})
    if audio:
        raw = str(audio.get("data") or "").strip()
        fmt = str(audio.get("format") or "wav").lower()
        if raw:
            content.append({"type": "input_audio", "input_audio": {"data": raw, "format": fmt}})
    messages.append({"role": "user", "content": content})
    return messages


def _extract_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    value = message.get("content")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or item.get("content") or "")
            for item in value
            if isinstance(item, dict)
        ).strip()
    return ""


def _providers(store: ProviderStore, providers: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return list(providers) if providers is not None else store.list_providers(include_secret=True, enabled_only=True)


async def route_text_or_vision(
    store: ProviderStore,
    *,
    system: str | None,
    prompt: str,
    images: list[dict[str, str]] | None = None,
    max_tokens: int = 1200,
    temperature: float = 0.5,
    providers: Iterable[dict[str, Any]] | None = None,
) -> CapabilityResult:
    started = perf_counter()
    attempts: list[dict[str, Any]] = []
    vision = bool(images)
    for provider in _providers(store, providers):
        api_key = str(provider.get("api_key") or "")
        models = text_model_candidates(provider, vision=vision)
        if not api_key or not models:
            continue
        for model in models:
            payload = {
                "model": model,
                "messages": build_chat_messages(system, prompt, images=images),
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            for root in api_roots(provider["base_url"]):
                url = root.rstrip("/") + "/chat/completions"
                one = perf_counter()
                try:
                    timeout_seconds = float(provider.get("timeout_seconds") or 60)
                    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
                        response = await client.post(url, headers=_headers(api_key), json=payload)
                    latency = int((perf_counter() - one) * 1000)
                    if not response.is_success:
                        attempts.append({"provider": provider["name"], "model": model, "url": url, "status": response.status_code, "latency_ms": latency})
                        continue
                    content = _extract_text(response.json())
                    if not content:
                        attempts.append({"provider": provider["name"], "model": model, "url": url, "error": "empty content", "latency_ms": latency})
                        continue
                    return CapabilityResult(
                        True,
                        TASK_VISION if vision else TASK_TEXT,
                        content=content,
                        provider_id=provider["id"],
                        provider_name=provider["name"],
                        model=model,
                        latency_ms=int((perf_counter() - started) * 1000),
                        attempts=attempts,
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    attempts.append({"provider": provider["name"], "model": model, "url": url, "error": str(exc)[:260]})
    return CapabilityResult(False, TASK_VISION if vision else TASK_TEXT, latency_ms=int((perf_counter() - started) * 1000), attempts=attempts)


def _parse_images(data: dict[str, Any]) -> list[MediaResult]:
    items = data.get("data")
    if not isinstance(items, list):
        return []
    media: list[MediaResult] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        b64 = str(item.get("b64_json") or "").strip()
        if url or b64:
            media.append(
                MediaResult(
                    kind="image",
                    url=url,
                    data_base64=b64,
                    mime_type="image/png",
                    revised_prompt=str(item.get("revised_prompt") or ""),
                )
            )
    return media


async def route_image_generation(
    store: ProviderStore,
    *,
    prompt: str,
    size: str = "1024x1024",
    providers: Iterable[dict[str, Any]] | None = None,
) -> CapabilityResult:
    started = perf_counter()
    attempts: list[dict[str, Any]] = []
    for provider in _providers(store, providers):
        api_key = str(provider.get("api_key") or "")
        for model in image_model_candidates(provider) if api_key else []:
            payload = {"model": model, "prompt": prompt, "n": 1, "size": size}
            for root in api_roots(provider["base_url"]):
                url = root.rstrip("/") + "/images/generations"
                try:
                    timeout_seconds = float(provider.get("timeout_seconds") or 60)
                    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
                        response = await client.post(url, headers=_headers(api_key), json=payload)
                    if not response.is_success:
                        attempts.append({"provider": provider["name"], "model": model, "status": response.status_code})
                        continue
                    media = _parse_images(response.json())
                    if not media:
                        attempts.append({"provider": provider["name"], "model": model, "error": "empty image result"})
                        continue
                    return CapabilityResult(
                        True,
                        TASK_IMAGE,
                        provider_id=provider["id"],
                        provider_name=provider["name"],
                        model=model,
                        latency_ms=int((perf_counter() - started) * 1000),
                        media=media,
                        attempts=attempts,
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    attempts.append({"provider": provider["name"], "model": model, "error": str(exc)[:260]})
    return CapabilityResult(False, TASK_IMAGE, latency_ms=int((perf_counter() - started) * 1000), attempts=attempts)


def infer_audio_protocol(provider: dict[str, Any], model: str) -> str:
    configured = str(provider.get("audio_protocol") or "auto").strip().lower()
    if configured in {"chat_audio", "speech"}:
        return configured
    lowered = model.lower()
    if "tts" in lowered or lowered.startswith("tts-") or "speech" in lowered:
        return "speech"
    if "realtime" in lowered:
        return "realtime"
    return "chat_audio"


def _parse_chat_audio(data: dict[str, Any], default_format: str) -> tuple[str, list[MediaResult]]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", []
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return "", []
    transcript = _extract_text(data)
    audio = message.get("audio")
    if not isinstance(audio, dict):
        return transcript, []
    transcript = str(audio.get("transcript") or transcript or "")
    url = str(audio.get("url") or "").strip()
    b64 = str(audio.get("data") or "").strip()
    fmt = str(audio.get("format") or default_format or "wav").lower()
    mime = "audio/wav" if fmt == "wav" else f"audio/{fmt}"
    if not url and not b64:
        return transcript, []
    return transcript, [MediaResult(kind="audio", url=url, data_base64=b64, mime_type=mime, transcript=transcript)]


async def _route_chat_audio(
    provider: dict[str, Any],
    model: str,
    *,
    system: str | None,
    prompt: str,
    audio_input: dict[str, str] | None,
    max_tokens: int,
    voice: str,
    audio_format: str,
) -> tuple[str, list[MediaResult], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    payload = {
        "model": model,
        "messages": build_chat_messages(system, prompt, audio=audio_input),
        "modalities": ["text", "audio"],
        "audio": {"voice": voice, "format": audio_format},
        "max_tokens": max_tokens,
        "stream": False,
    }
    api_key = str(provider.get("api_key") or "")
    for root in api_roots(provider["base_url"]):
        url = root.rstrip("/") + "/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=float(provider.get("timeout_seconds") or 60), follow_redirects=True) as client:
                response = await client.post(url, headers=_headers(api_key), json=payload)
            if not response.is_success:
                attempts.append({"url": url, "status": response.status_code})
                continue
            transcript, media = _parse_chat_audio(response.json(), audio_format)
            if media:
                return transcript, media, attempts
            attempts.append({"url": url, "error": "empty audio result"})
        except (httpx.HTTPError, ValueError) as exc:
            attempts.append({"url": url, "error": str(exc)[:260]})
    return "", [], attempts


async def _route_speech(
    provider: dict[str, Any],
    model: str,
    *,
    text: str,
    voice: str,
    audio_format: str,
) -> tuple[list[MediaResult], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    api_key = str(provider.get("api_key") or "")
    payload = {"model": model, "input": text[:4096], "voice": voice, "response_format": audio_format}
    for root in api_roots(provider["base_url"]):
        url = root.rstrip("/") + "/audio/speech"
        try:
            async with httpx.AsyncClient(timeout=float(provider.get("timeout_seconds") or 60), follow_redirects=True) as client:
                response = await client.post(url, headers=_headers(api_key, accept="audio/*"), json=payload)
            if not response.is_success:
                attempts.append({"url": url, "status": response.status_code})
                continue
            if not response.content:
                attempts.append({"url": url, "error": "empty audio file"})
                continue
            mime = response.headers.get("content-type", "").split(";", 1)[0] or f"audio/{audio_format}"
            return [
                MediaResult(
                    kind="audio",
                    data_base64=base64.b64encode(response.content).decode("ascii"),
                    mime_type=mime,
                    transcript=text,
                )
            ], attempts
        except httpx.HTTPError as exc:
            attempts.append({"url": url, "error": str(exc)[:260]})
    return [], attempts


async def route_audio(
    store: ProviderStore,
    *,
    system: str | None,
    prompt: str,
    audio_input: dict[str, str] | None = None,
    max_tokens: int = 1200,
    voice: str = "",
    audio_format: str = "",
    providers: Iterable[dict[str, Any]] | None = None,
) -> CapabilityResult:
    started = perf_counter()
    attempts: list[dict[str, Any]] = []
    provider_list = _providers(store, providers)
    text_answer: CapabilityResult | None = None
    for provider in provider_list:
        api_key = str(provider.get("api_key") or "")
        for model in audio_model_candidates(provider) if api_key else []:
            selected_voice = voice.strip() or str(provider.get("audio_voice") or "alloy")
            selected_format = audio_format.strip().lower() or str(provider.get("audio_format") or "wav")
            protocol = infer_audio_protocol(provider, model)
            if protocol == "realtime":
                attempts.append({"provider": provider["name"], "model": model, "error": "realtime session required"})
                continue
            if protocol == "chat_audio":
                transcript, media, current = await _route_chat_audio(
                    provider,
                    model,
                    system=system,
                    prompt=prompt,
                    audio_input=audio_input,
                    max_tokens=max_tokens,
                    voice=selected_voice,
                    audio_format=selected_format,
                )
                attempts.extend({"provider": provider["name"], "model": model, **item} for item in current)
                if media:
                    return CapabilityResult(
                        True,
                        TASK_AUDIO,
                        content=transcript,
                        provider_id=provider["id"],
                        provider_name=provider["name"],
                        model=model,
                        latency_ms=int((perf_counter() - started) * 1000),
                        media=media,
                        attempts=attempts,
                    )
                continue
            if audio_input:
                attempts.append({"provider": provider["name"], "model": model, "error": "speech/TTS cannot understand input audio"})
                continue
            if text_answer is None:
                text_answer = await route_text_or_vision(store, system=system, prompt=prompt, max_tokens=max_tokens)
            if not text_answer.ok:
                attempts.extend(text_answer.attempts)
                continue
            media, current = await _route_speech(
                provider,
                model,
                text=text_answer.content,
                voice=selected_voice,
                audio_format=selected_format,
            )
            attempts.extend({"provider": provider["name"], "model": model, **item} for item in current)
            if media:
                return CapabilityResult(
                    True,
                    TASK_AUDIO,
                    content=text_answer.content,
                    provider_id=provider["id"],
                    provider_name=provider["name"],
                    model=model,
                    latency_ms=int((perf_counter() - started) * 1000),
                    media=media,
                    attempts=attempts,
                )
    return CapabilityResult(False, TASK_AUDIO, latency_ms=int((perf_counter() - started) * 1000), attempts=attempts)


async def route_capability(
    store: ProviderStore,
    *,
    system: str | None,
    prompt: str,
    requested_task: str = TASK_AUTO,
    images: list[dict[str, str]] | None = None,
    audio_input: dict[str, str] | None = None,
    max_tokens: int = 1200,
    image_size: str = "1024x1024",
    voice: str = "",
    audio_format: str = "",
) -> CapabilityResult:
    task, explicit = detect_task(
        prompt,
        requested_task=requested_task,
        has_images=bool(images),
        has_audio=audio_input is not None,
    )
    if task == TASK_IMAGE:
        result = await route_image_generation(store, prompt=prompt, size=image_size)
        if result.ok or explicit:
            return result
        fallback = await route_text_or_vision(store, system=system, prompt=prompt, max_tokens=max_tokens)
        fallback.fallback_from = TASK_IMAGE
        return fallback
    if task == TASK_AUDIO:
        result = await route_audio(
            store,
            system=system,
            prompt=prompt,
            audio_input=audio_input,
            max_tokens=max_tokens,
            voice=voice,
            audio_format=audio_format,
        )
        if result.ok or explicit or audio_input is not None:
            return result
        fallback = await route_text_or_vision(store, system=system, prompt=prompt, max_tokens=max_tokens)
        fallback.fallback_from = TASK_AUDIO
        return fallback
    return await route_text_or_vision(
        store,
        system=system,
        prompt=prompt,
        images=images if task == TASK_VISION else None,
        max_tokens=max_tokens,
    )
