import asyncio
import time
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.services.hermes_import import HermesImportService

HERMES_SYNC_TTL_SECONDS = 10

_sync_lock = Lock()
_sync_last_at = 0.0
_sync_last_result: dict[str, Any] | None = None


def sync_hermes_snapshot(db: Session, user_id: str | None = None, force: bool = False) -> dict[str, Any]:
    global _sync_last_at, _sync_last_result

    now = time.monotonic()
    if not force and _sync_last_result and now - _sync_last_at < HERMES_SYNC_TTL_SECONDS:
        return _sync_last_result

    try:
        with _sync_lock:
            now = time.monotonic()
            if not force and _sync_last_result and now - _sync_last_at < HERMES_SYNC_TTL_SECONDS:
                return _sync_last_result
            _sync_last_result = HermesImportService().sync(db, user_id=user_id)
            _sync_last_at = time.monotonic()
            return _sync_last_result
    except Exception as exc:
        db.rollback()
        _sync_last_result = {"status": "error", "error": str(exc)}
        _sync_last_at = time.monotonic()
        return _sync_last_result


def sync_hermes_once(user_id: str | None = None, force: bool = True) -> dict[str, Any]:
    db = SessionLocal()
    try:
        return sync_hermes_snapshot(db, user_id=user_id, force=force)
    finally:
        db.close()


async def periodic_hermes_sync() -> None:
    interval = max(30, min(int(settings.hermes_sync_interval_seconds), 60))
    while True:
        await asyncio.to_thread(sync_hermes_once, None, True)
        await asyncio.sleep(interval)
