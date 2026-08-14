from pathlib import Path

from x_api import (
    ProviderStore,
    audio_model_candidates,
    image_model_candidates,
    normalize_base_url,
    text_model_candidates,
)


def test_provider_store_round_trip(tmp_path: Path) -> None:
    store = ProviderStore(tmp_path / "providers.db")
    item = store.create_provider(
        name="chat2api",
        base_url="https://example.com/v1/chat/completions",
        api_key="sk-1234567890abcdef",
        main_text_model="gpt-5.6-sol",
        backup_text_models=["gpt-5.5", "gpt-5.5"],
        main_image_model="gpt-image-1",
        backup_image_models=["image-backup"],
        main_audio_model="gpt-audio",
        backup_audio_models=["gpt-audio-mini"],
        audio_protocol="chat_audio",
        audio_voice="marin",
        protocol_order=["chat", "responses"],
        priority=1,
    )
    assert item["base_url"] == "https://example.com/v1"
    assert item["api_key_configured"] is True
    assert "1234567890abcdef" not in item["api_key_masked"]
    assert item["backup_text_models"] == ["gpt-5.5"]
    assert text_model_candidates(item, vision=True) == ["gpt-5.6-sol", "gpt-5.5"]
    assert image_model_candidates(item) == ["gpt-image-1", "image-backup"]
    assert audio_model_candidates(item) == ["gpt-audio", "gpt-audio-mini"]
    assert item["audio_voice"] == "marin"

    secret = store.get_provider(item["id"], include_secret=True)
    assert secret["api_key"] == "sk-1234567890abcdef"

    updated = store.update_provider(
        item["id"],
        name="primary",
        api_key="",
        priority=2,
        main_vision_model="vision-special",
        backup_vision_models=["vision-backup"],
    )
    assert updated["name"] == "primary"
    assert text_model_candidates(updated, vision=True) == ["vision-special", "vision-backup"]
    assert store.get_provider(item["id"], include_secret=True)["api_key"] == "sk-1234567890abcdef"

    store.clear_api_key(item["id"])
    assert store.get_provider(item["id"], include_secret=True)["api_key"] == ""


def test_legacy_import_only_when_empty(tmp_path: Path) -> None:
    store = ProviderStore(tmp_path / "providers.db")
    imported = store.import_legacy_provider(
        base_url="https://relay.example/v1",
        api_key="sk-test",
        model="gpt-test",
        timeout_seconds=55,
    )
    assert imported is not None
    assert imported["main_text_model"] == "gpt-test"
    assert imported["main_image_model"] == ""
    assert imported["main_audio_model"] == ""
    assert store.import_legacy_provider(base_url="x", api_key="y", model="z") is None


def test_normalize_base_url() -> None:
    assert normalize_base_url("https://a.example/v1/chat/completions") == "https://a.example/v1"
    assert normalize_base_url("https://a.example/v1/images/generations") == "https://a.example/v1"
    assert normalize_base_url("https://a.example/v1/audio/speech") == "https://a.example/v1"
    assert normalize_base_url("a.example/v1") == "https://a.example/v1"
