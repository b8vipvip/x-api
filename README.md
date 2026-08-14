# X-API

Reusable AI provider management and OpenAI-compatible capability routing module extracted and rewritten from the provider-management design used by `b8vipvip/qianniu-ai-bot/services/api-control-plane`.

## What it provides

- Multiple upstream providers with priority and enable/disable state
- Encrypted API keys (Fernet key stored separately from SQLite)
- Main/backup text models
- Text and vision share the same model pool by default, with optional vision override models
- Main/backup image-generation models
- Main/backup audio models with `auto`, `chat_audio`, or `speech` routing modes
- Configurable text protocol order (`chat`, `responses`, `legacy`)
- `/models` discovery and ordinary/deep text provider probes
- Runtime provider/model fallback helpers
- Automatic intent routing for text, image input, image generation, and voice requests
- Optional FastAPI JSON admin router
- Optional self-contained provider web console
- No project-specific authentication dependency in the core package

## Install

```bash
pip install -e .
```

Or directly from GitHub:

```bash
pip install "git+https://github.com/b8vipvip/x-api.git"
```

## Provider configuration

```python
from pathlib import Path
from x_api import ProviderStore

store = ProviderStore(Path("data/ai-providers.db"))
store.init()

store.create_provider(
    name="primary relay",
    base_url="https://example.com/v1",
    api_key="sk-...",
    main_text_model="gpt-text-model",
    backup_text_models=["gpt-text-backup"],
    # Leave vision fields blank to reuse the text model pool.
    main_vision_model="",
    main_image_model="image-model",
    backup_image_models=["image-backup"],
    main_audio_model="audio-model",
    backup_audio_models=["audio-backup"],
    audio_protocol="auto",
    audio_voice="alloy",
    audio_format="wav",
)
```

The encryption key is generated beside the database as `ai-providers.key`. Back up both files together. Existing X-API provider databases are migrated in place when the multimodal fields are first initialized.

## Capability routing

```python
from x_api import route_capability

result = await route_capability(
    store,
    system="You are a helpful assistant.",
    prompt="帮我生成一张蓝天白云的图片",
)

print(result.task)          # image_generation
print(result.provider_name)
print(result.model)
print(result.media)
```

`route_capability()` uses this routing policy:

1. Normal text uses provider priority, then main/backup text models.
2. Image inputs use vision override models when configured; otherwise they reuse the text model pool.
3. Image-generation intent switches to providers that have image-generation models configured.
4. Voice intent switches to providers that have audio models configured.
5. `chat_audio` models can handle audio-style Chat Completions input/output. `speech` models synthesize a normal text answer through `/audio/speech`.
6. In `auto` mode, a failed or unavailable specialist route can fall back to text for prompt-only requests. Explicit specialist requests and actual audio inputs do not silently degrade.
7. Models marked as realtime are reported as requiring a realtime session; the one-shot router does not pretend a realtime WebSocket/WebRTC model is a normal HTTP completion model.

Media results are normalized as `MediaResult` objects. X-API does not decide where generated binary media must be stored: an upstream URL is preserved as `url`, while binary image/audio results are returned as `data_base64` plus `mime_type`. The host application can persist them locally, upload them to object storage, or stream them directly.

## Vision input example

```python
result = await route_capability(
    store,
    system=None,
    prompt="这张图片里有什么？",
    images=[{
        "url": "data:image/png;base64,...",
        "detail": "auto",
    }],
)
```

When a provider has no dedicated vision override, `main_text_model + backup_text_models` are used directly.

## Voice routing example

```python
result = await route_capability(
    store,
    system=None,
    prompt="请用语音回复：今天的工作重点是什么？",
    voice="alloy",
    audio_format="wav",
)
```

For raw audio input, pass:

```python
result = await route_capability(
    store,
    system=None,
    prompt="请回答这段语音",
    audio_input={"data": "<base64>", "format": "wav"},
)
```

## FastAPI drop-in integration

```python
from pathlib import Path
from fastapi import FastAPI
from x_api import ProviderStore, create_console_router, create_provider_router

store = ProviderStore(Path("data/ai-providers.db"))
store.init()

app = FastAPI()
app.include_router(create_provider_router(store, require_admin=my_admin_dependency))
app.include_router(create_console_router(store, require_admin=my_admin_dependency))
```

Then open `/admin/ai-providers`. The JSON APIs are under `/api/admin/ai-providers` by default. Both prefixes can be changed when creating the routers.

If the host application already has its own visual system (for example FDEX), use only `ProviderStore` and `create_provider_router`, or call the core functions directly and build a host-native page.

## Legacy text routing

`complete()` and `stream_chat()` remain available for projects that only need text routing. `complete()` tries enabled providers by priority, then main/backup model and verified protocol order. `stream_chat()` uses Chat Completions SSE as the portable streaming baseline and only switches provider before answer content starts, which prevents duplicated partial answers.

## Testing policy

Ordinary/deep provider probes remain text-only by default. This is intentional: scheduled health checks should not repeatedly create billable images or audio. Host projects can implement explicit, manual specialist probes when needed.

## FDEX integration

FDEX vendors the same core ideas under its server tree and adds its own Jinja admin page, audit log, CSRF handling, generated-media storage, and systemd text deep-probe scheduler. This keeps the reusable X-API package independent from FDEX-specific operations while preserving a fast copy/import path for future projects.

## Origin and scope

This module is based on the provider-management architecture of the owner's `qianniu-ai-bot` control plane, but is refactored into a standalone package. It does not modify or depend on the `qianniu-ai-bot` repository at runtime.
