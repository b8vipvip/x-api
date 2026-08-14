from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .store import DEFAULT_PROTOCOLS, ProviderStore
from .testing import discover_models, probe_provider


class ProviderInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = ""
    enabled: bool = True
    priority: int = Field(default=100, ge=1, le=10000)
    main_text_model: str = ""
    backup_text_models: list[str] = Field(default_factory=list)
    main_vision_model: str = ""
    backup_vision_models: list[str] = Field(default_factory=list)
    main_image_model: str = ""
    backup_image_models: list[str] = Field(default_factory=list)
    main_audio_model: str = ""
    backup_audio_models: list[str] = Field(default_factory=list)
    audio_protocol: str = Field(default="auto", pattern="^(auto|chat_audio|speech)$")
    audio_voice: str = Field(default="alloy", min_length=1, max_length=80)
    audio_format: str = Field(default="wav", pattern="^(mp3|opus|aac|flac|wav|pcm)$")
    protocol_order: list[str] = Field(default_factory=lambda: DEFAULT_PROTOCOLS.copy())
    timeout_seconds: int = Field(default=60, ge=5, le=600)
    auto_test_enabled: bool = False
    auto_test_interval_hours: int = Field(default=12, ge=1, le=720)
    clear_api_key: bool = False


class ProbeInput(BaseModel):
    mode: str = Field(default="ordinary", pattern="^(ordinary|deep)$")
    auto_apply: bool = True


def create_provider_router(
    store: ProviderStore,
    *,
    require_admin: Callable[..., object] | None = None,
    prefix: str = "/api/admin/ai-providers",
) -> APIRouter:
    dependencies = [Depends(require_admin)] if require_admin is not None else []
    router = APIRouter(prefix=prefix, tags=["ai-providers"], dependencies=dependencies)

    @router.get("")
    def list_items() -> list[dict]:
        return store.list_providers()

    @router.get("/{provider_id}")
    def get_item(provider_id: int) -> dict:
        try:
            return store.get_provider(provider_id)
        except KeyError as exc:
            raise HTTPException(404, "供应商不存在") from exc

    @router.post("")
    def create_item(payload: ProviderInput) -> dict:
        try:
            return store.create_provider(**payload.model_dump(exclude={"clear_api_key"}))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.put("/{provider_id}")
    def update_item(provider_id: int, payload: ProviderInput) -> dict:
        try:
            data = payload.model_dump(exclude={"clear_api_key"})
            item = store.update_provider(provider_id, **data)
            if payload.clear_api_key:
                item = store.clear_api_key(provider_id)
            return item
        except KeyError as exc:
            raise HTTPException(404, "供应商不存在") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete("/{provider_id}")
    def delete_item(provider_id: int) -> dict[str, bool]:
        try:
            store.delete_provider(provider_id)
            return {"ok": True}
        except KeyError as exc:
            raise HTTPException(404, "供应商不存在") from exc

    @router.post("/{provider_id}/discover")
    async def discover(provider_id: int) -> dict:
        try:
            provider = store.get_provider(provider_id, include_secret=True)
        except KeyError as exc:
            raise HTTPException(404, "供应商不存在") from exc
        return await discover_models(provider)

    @router.post("/{provider_id}/probe")
    async def probe(provider_id: int, payload: ProbeInput) -> dict:
        try:
            return await probe_provider(store, provider_id, mode=payload.mode, auto_apply=payload.auto_apply)
        except KeyError as exc:
            raise HTTPException(404, "供应商不存在") from exc

    return router
