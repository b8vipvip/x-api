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
- No project-specific authentication/UI dependency in the core package

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

## FastAPI integration

```python
from fastapi import FastAPI
from x_api.fastapi_admin import create_provider_router

app = FastAPI()
app.include_router(create_provider_router(store, require_admin=my_admin_dependency))
```

The router exposes CRUD, provider testing and model discovery APIs. The host project stays responsible for authentication, CSRF and its visual console.

## FDEX integration

FDEX vendors the same core under its server tree and adds its own Jinja admin page. This keeps the reusable provider logic independent from FDEX-specific login, audit logging and styling.

## Origin and scope

This module is based on the provider-management architecture of the owner's `qianniu-ai-bot` control plane, but is refactored into a standalone package. It does not modify or depend on the `qianniu-ai-bot` repository at runtime.
