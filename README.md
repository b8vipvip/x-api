# X-API

Reusable AI provider management and OpenAI-compatible routing module extracted and rewritten from the provider-management design used by `b8vipvip/qianniu-ai-bot/services/api-control-plane`.

## What it provides

- Multiple upstream providers with priority and enable/disable state
- Encrypted API keys (Fernet key stored separately from SQLite)
- Main/backup text models and main/backup vision models
- Configurable protocol order (`chat`, `responses`, `legacy`)
- `/models` discovery and ordinary/deep provider probes
- Last status, latency and model capability persistence
- Runtime provider/model fallback helpers
- Optional FastAPI JSON admin router
- Optional self-contained provider web console
- No project-specific authentication dependency in the core package

## Install

```bash
pip install -e .
```

## Minimal usage

```python
from pathlib import Path
from x_api import ProviderStore

store = ProviderStore(Path("data/ai-providers.db"))
store.init()
provider = store.create_provider(
    name="chat2api",
    base_url="https://example.com/v1",
    api_key="sk-...",
    main_text_model="gpt-5.6-sol",
)
```

The encryption key is generated beside the database as `ai-providers.key`. Back up both files together.

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

## Runtime routing

`complete()` tries enabled providers by priority, then main/backup model and verified protocol order. `stream_chat()` uses Chat Completions SSE as the portable streaming baseline and only switches provider before answer content starts, which prevents duplicated partial answers.

## FDEX integration

FDEX vendors the same core ideas under its server tree and adds its own Jinja admin page, audit log, CSRF handling and systemd deep-probe scheduler. This keeps the reusable X-API package independent from FDEX-specific operations while preserving a fast copy/import path for future projects.

## Origin and scope

This module is based on the provider-management architecture of the owner's `qianniu-ai-bot` control plane, but is refactored into a standalone package. It does not modify or depend on the `qianniu-ai-bot` repository at runtime.
