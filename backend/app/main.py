from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from app.api.routes import router
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.entities import User, Role
from app.services.hermes_import import HermesImportService

app = FastAPI(title='Voryx AI Operations API')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
app.include_router(router, prefix='/api')

@app.on_event('startup')
def startup_tasks():
    db = SessionLocal()
    try:
        if settings.first_superuser_email and settings.first_superuser_password and not db.scalar(select(User).where(User.email == settings.first_superuser_email)):
            db.add(User(email=settings.first_superuser_email, password_hash=hash_password(settings.first_superuser_password), role=Role.admin)); db.commit()
        HermesImportService().sync(db)
    except Exception:
        db.rollback()
    finally: db.close()

@app.get('/health')
def health(): return {'status': 'ok'}
