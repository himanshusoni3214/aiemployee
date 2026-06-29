import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes import current_user, router
from app.core.config import settings
from app.core.db import SessionLocal, get_db
from app.core.security import hash_password
from app.models.entities import (
    Campaign,
    Company,
    Job,
    JobStatus,
    Lead,
    LeadStatus,
    Role,
    User,
)
from app.services.hermes_sync import periodic_hermes_sync, sync_hermes_once, sync_hermes_snapshot

TORONTO_TZ = ZoneInfo('America/Toronto')
CONFIRMED_SENT_STATUSES = {'sent', 'success', 'successful', 'delivered', 'ok', 'completed'}


# Replace the original CEO report with a version that counts only confirmed
# Hermes outreach-log sends for the Brew It By Sash campaigns.
router.routes = [route for route in router.routes if getattr(route, 'path', None) != '/reports/ceo']


def _toronto_day_utc_bounds() -> tuple[datetime, datetime]:
    local_now = datetime.now(TORONTO_TZ)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).replace(tzinfo=None),
        local_end.astimezone(timezone.utc).replace(tzinfo=None),
    )


@router.get('/reports/ceo')
def ceo_report(db: Session = Depends(get_db), user: User = Depends(current_user)):
    sync_hermes_snapshot(db, user.id)
    today_start, tomorrow_start = _toronto_day_utc_bounds()

    campaign_ids = list(
        db.scalars(
            select(Campaign.id).where(Campaign.name.ilike('Brew It By Sash%'))
        ).all()
    )
    outreach_stmt = select(Job).where(
        Job.id.like('hermes-outreach-%'),
        Job.created_at >= today_start,
        Job.created_at < tomorrow_start,
    )
    if campaign_ids:
        outreach_stmt = outreach_stmt.where(Job.campaign_id.in_(campaign_ids))
    outreach_jobs = db.scalars(outreach_stmt).all()
    emails_sent = sum(
        1
        for job in outreach_jobs
        if str((job.payload or {}).get('status') or '').strip().lower() in CONFIRMED_SENT_STATUSES
    )

    return {
        'todays_leads': db.scalar(
            select(func.count(Lead.id)).where(
                Lead.created_at >= today_start,
                Lead.created_at < tomorrow_start,
            )
        ) or 0,
        'verified_leads': db.scalar(
            select(func.count(Lead.id)).where(Lead.status == LeadStatus.verified)
        ) or 0,
        'emails_sent': emails_sent,
        'emails_sent_source': 'confirmed Hermes outreach_log rows',
        'reporting_timezone': 'America/Toronto',
        'replies': db.scalar(
            select(func.count(Lead.id)).where(Lead.status == LeadStatus.replied)
        ) or 0,
        'meetings': db.scalar(
            select(func.count(Lead.id)).where(Lead.status == LeadStatus.meeting_booked)
        ) or 0,
        'failed_jobs': db.scalar(
            select(func.count(Job.id)).where(Job.status == JobStatus.failed)
        ) or 0,
        'companies': [
            {'id': company.id, 'name': company.name}
            for company in db.scalars(select(Company)).all()
        ],
    }


app = FastAPI(title='Voryx AI Operations API')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
app.include_router(router, prefix='/api')


@app.on_event('startup')
async def startup_tasks():
    db = SessionLocal()
    try:
        if settings.first_superuser_email and settings.first_superuser_password and not db.scalar(select(User).where(User.email == settings.first_superuser_email)):
            db.add(User(email=settings.first_superuser_email, password_hash=hash_password(settings.first_superuser_password), role=Role.admin))
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
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
def health():
    return {'status': 'ok'}
