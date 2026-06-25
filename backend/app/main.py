import asyncio
import contextlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from app.api.routes import router
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.entities import User, Role
from app.services.hermes_sync import periodic_hermes_sync, sync_hermes_once

app = FastAPI(title='Voryx AI Operations API')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
app.include_router(router, prefix='/api')

@app.on_event('startup')
async def startup_tasks():
    db = SessionLocal()
    try:
        if settings.first_superuser_email and settings.first_superuser_password and not db.scalar(select(User).where(User.email == settings.first_superuser_email)):
            db.add(User(email=settings.first_superuser_email, password_hash=hash_password(settings.first_superuser_password), role=Role.admin)); db.commit()
    except Exception:
        db.rollback()
    finally: db.close()
    sync_hermes_once(force=True)
    app.state.hermes_sync_task = asyncio.create_task(periodic_hermes_sync())

@app.on_event('shutdown')
async def shutdown_tasks():
    task = getattr(app.state, 'hermes_sync_task', None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

@app.get('/health')
def health(): return {'status': 'ok'}
