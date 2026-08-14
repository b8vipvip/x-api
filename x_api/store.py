from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from cryptography.fernet import Fernet, InvalidToken

DEFAULT_PROTOCOLS = ["chat", "responses", "legacy"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    for suffix in ("/chat/completions", "/responses", "/completions"):
        if value.lower().endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    return value


def mask_key(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 8) + value[-4:]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _parse(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


class ProviderStore:
    """SQLite provider store with a separate Fernet key file.

    The database contains only encrypted API keys. The key file must be backed up
    together with the database; losing it makes stored API keys unrecoverable.
    """

    def __init__(self, db_path: str | Path, key_path: str | Path | None = None):
        self.db_path = Path(db_path).resolve()
        self.key_path = Path(key_path).resolve() if key_path else self.db_path.with_suffix(".key")
        self._fernet: Fernet | None = None

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        self._load_or_create_key()
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS providers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_key_cipher TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 100,
                    main_text_model TEXT NOT NULL DEFAULT '',
                    backup_text_models_json TEXT NOT NULL DEFAULT '[]',
                    main_vision_model TEXT NOT NULL DEFAULT '',
                    backup_vision_models_json TEXT NOT NULL DEFAULT '[]',
                    protocol_order_json TEXT NOT NULL DEFAULT '["chat","responses","legacy"]',
                    model_capabilities_json TEXT NOT NULL DEFAULT '{}',
                    timeout_seconds INTEGER NOT NULL DEFAULT 60,
                    auto_test_enabled INTEGER NOT NULL DEFAULT 0,
                    auto_test_interval_hours INTEGER NOT NULL DEFAULT 12,
                    last_test_at TEXT,
                    last_status TEXT NOT NULL DEFAULT '未测试',
                    last_latency_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_xapi_provider_priority
                  ON providers(enabled, priority, id);
                """
            )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _load_or_create_key(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
        else:
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            tmp = self.key_path.with_suffix(self.key_path.suffix + ".tmp")
            tmp.write_bytes(key + b"\n")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.key_path)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        return self._load_or_create_key().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        try:
            return self._load_or_create_key().decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("无法解密供应商 API Key，请确认 ai-providers.key 未变化") from exc

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _row(self, row: sqlite3.Row, include_secret: bool = False) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        data["auto_test_enabled"] = bool(data["auto_test_enabled"])
        data["backup_text_models"] = _parse(data.pop("backup_text_models_json"), [])
        data["backup_vision_models"] = _parse(data.pop("backup_vision_models_json"), [])
        data["protocol_order"] = _parse(data.pop("protocol_order_json"), DEFAULT_PROTOCOLS.copy())
        data["model_capabilities"] = _parse(data.pop("model_capabilities_json"), {})
        cipher = data.pop("api_key_cipher")
        plain = self.decrypt(cipher) if cipher else ""
        data["api_key_masked"] = mask_key(plain)
        data["api_key_configured"] = bool(plain)
        if include_secret:
            data["api_key"] = plain
        return data

    def list_providers(self, *, include_secret: bool = False, enabled_only: bool = False) -> list[dict[str, Any]]:
        self.init()
        sql = "SELECT * FROM providers"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY priority ASC, id ASC"
        with self.db() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row(row, include_secret=include_secret) for row in rows]

    def get_provider(self, provider_id: int, *, include_secret: bool = False) -> dict[str, Any]:
        self.init()
        with self.db() as conn:
            row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if row is None:
            raise KeyError(f"provider {provider_id} not found")
        return self._row(row, include_secret=include_secret)

    @staticmethod
    def _models(values: Iterable[str] | None) -> list[str]:
        return list(dict.fromkeys(str(x).strip() for x in (values or []) if str(x).strip()))

    @staticmethod
    def _protocols(values: Iterable[str] | None) -> list[str]:
        allowed = {"chat", "responses", "legacy"}
        result = [str(x).strip() for x in (values or []) if str(x).strip() in allowed]
        return list(dict.fromkeys(result)) or DEFAULT_PROTOCOLS.copy()

    def create_provider(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str = "",
        enabled: bool = True,
        priority: int = 100,
        main_text_model: str = "",
        backup_text_models: Iterable[str] | None = None,
        main_vision_model: str = "",
        backup_vision_models: Iterable[str] | None = None,
        protocol_order: Iterable[str] | None = None,
        timeout_seconds: int = 60,
        auto_test_enabled: bool = False,
        auto_test_interval_hours: int = 12,
    ) -> dict[str, Any]:
        self.init()
        now = utc_now()
        base = normalize_base_url(base_url)
        if not name.strip() or not base.startswith(("http://", "https://")):
            raise ValueError("供应商名称或 BaseUrl 无效")
        with self.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO providers(
                    name, base_url, api_key_cipher, enabled, priority,
                    main_text_model, backup_text_models_json,
                    main_vision_model, backup_vision_models_json,
                    protocol_order_json, model_capabilities_json,
                    timeout_seconds, auto_test_enabled, auto_test_interval_hours,
                    created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    name.strip(), base, self.encrypt(api_key.strip()), 1 if enabled else 0,
                    max(1, int(priority or 100)), main_text_model.strip(), _json(self._models(backup_text_models)),
                    main_vision_model.strip(), _json(self._models(backup_vision_models)),
                    _json(self._protocols(protocol_order)), "{}", max(5, min(600, int(timeout_seconds or 60))),
                    1 if auto_test_enabled else 0, max(1, min(720, int(auto_test_interval_hours or 12))),
                    now, now,
                ),
            )
            provider_id = int(cur.lastrowid)
        return self.get_provider(provider_id)

    def update_provider(self, provider_id: int, **values: Any) -> dict[str, Any]:
        existing = self.get_provider(provider_id, include_secret=True)
        name = str(values.get("name", existing["name"])).strip()
        base = normalize_base_url(str(values.get("base_url", existing["base_url"])))
        if not name or not base.startswith(("http://", "https://")):
            raise ValueError("供应商名称或 BaseUrl 无效")
        api_key = values.get("api_key", None)
        cipher = self.encrypt(str(api_key).strip()) if api_key not in (None, "") else self.encrypt(existing.get("api_key", ""))
        backup_text = self._models(values.get("backup_text_models", existing["backup_text_models"]))
        backup_vision = self._models(values.get("backup_vision_models", existing["backup_vision_models"]))
        protocols = self._protocols(values.get("protocol_order", existing["protocol_order"]))
        now = utc_now()
        with self.db() as conn:
            conn.execute(
                """
                UPDATE providers SET
                    name=?, base_url=?, api_key_cipher=?, enabled=?, priority=?,
                    main_text_model=?, backup_text_models_json=?,
                    main_vision_model=?, backup_vision_models_json=?, protocol_order_json=?,
                    timeout_seconds=?, auto_test_enabled=?, auto_test_interval_hours=?, updated_at=?
                WHERE id=?
                """,
                (
                    name, base, cipher,
                    1 if values.get("enabled", existing["enabled"]) else 0,
                    max(1, int(values.get("priority", existing["priority"]))),
                    str(values.get("main_text_model", existing["main_text_model"])).strip(), _json(backup_text),
                    str(values.get("main_vision_model", existing["main_vision_model"])).strip(), _json(backup_vision),
                    _json(protocols), max(5, min(600, int(values.get("timeout_seconds", existing["timeout_seconds"])))),
                    1 if values.get("auto_test_enabled", existing["auto_test_enabled"]) else 0,
                    max(1, min(720, int(values.get("auto_test_interval_hours", existing["auto_test_interval_hours"])))),
                    now, provider_id,
                ),
            )
        return self.get_provider(provider_id)

    def clear_api_key(self, provider_id: int) -> dict[str, Any]:
        self.get_provider(provider_id)
        with self.db() as conn:
            conn.execute("UPDATE providers SET api_key_cipher='', updated_at=? WHERE id=?", (utc_now(), provider_id))
        return self.get_provider(provider_id)

    def delete_provider(self, provider_id: int) -> None:
        self.get_provider(provider_id)
        with self.db() as conn:
            conn.execute("DELETE FROM providers WHERE id=?", (provider_id,))

    def record_probe(
        self,
        provider_id: int,
        *,
        ok: bool,
        status: str,
        latency_ms: int,
        capabilities: dict[str, Any] | None = None,
        main_text_model: str | None = None,
        backup_text_models: Iterable[str] | None = None,
        protocol_order: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        provider = self.get_provider(provider_id)
        caps = capabilities if capabilities is not None else provider["model_capabilities"]
        main = provider["main_text_model"] if main_text_model is None else main_text_model.strip()
        backups = provider["backup_text_models"] if backup_text_models is None else self._models(backup_text_models)
        protocols = provider["protocol_order"] if protocol_order is None else self._protocols(protocol_order)
        with self.db() as conn:
            conn.execute(
                """
                UPDATE providers SET last_status=?, last_latency_ms=?, last_test_at=?,
                    model_capabilities_json=?, main_text_model=?, backup_text_models_json=?,
                    protocol_order_json=?, updated_at=? WHERE id=?
                """,
                (
                    status or ("可用" if ok else "失败"), int(latency_ms), utc_now(), _json(caps),
                    main, _json(backups), _json(protocols), utc_now(), provider_id,
                ),
            )
        return self.get_provider(provider_id)

    def import_legacy_provider(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        name: str = "原 FDEX AI 接口",
        timeout_seconds: int = 60,
    ) -> dict[str, Any] | None:
        """Create one provider only when the new store is empty."""
        if self.list_providers():
            return None
        if not (base_url.strip() and api_key.strip() and model.strip()):
            return None
        return self.create_provider(
            name=name,
            base_url=base_url,
            api_key=api_key,
            main_text_model=model,
            priority=1,
            timeout_seconds=timeout_seconds,
        )
