from datetime import datetime, date
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from redis import Redis
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.db import get_db
from app.core.security import create_token, verify_password
from app.models.entities import *
from app.schemas.common import *
from app.services.audit import log
from app.services.connectors import get_connector
from app.services.hermes_control import HermesControlError, HermesControlService
from app.services.hermes_live import HermesLiveMonitor
from app.services.hermes_sync import hermes_sync_status, sync_hermes_snapshot as _sync_hermes_snapshot

router = APIRouter()
oauth2 = OAuth2PasswordBearer(tokenUrl='/api/auth/login', auto_error=False)
HERMES_SYNCED_LABELS = {'companies', 'employees', 'campaigns', 'schedules'}

def current_user(request: Request, token: str|None = Depends(oauth2), db: Session = Depends(get_db)):
    token = token or request.cookies.get('voryx_token')
    if not token:
        raise HTTPException(401, 'Missing token')
    try: payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError: raise HTTPException(401, 'Invalid token')
    user = db.get(User, payload.get('sub'))
    if not user or not user.is_active: raise HTTPException(401, 'Inactive user')
    return user

def require_write(user: User = Depends(current_user)):
    if user.role == Role.viewer: raise HTTPException(403, 'Viewer is read-only')
    return user

def _employee_hermes_job_id(employee: AIEmployee) -> str | None:
    limits = employee.daily_limits if isinstance(employee.daily_limits, dict) else {}
    value = limits.get('hermes_job_id')
    return str(value) if value else None

def _schedule_hermes_job_id(schedule: Schedule) -> str | None:
    payload = schedule.payload if isinstance(schedule.payload, dict) else {}
    value = payload.get('hermes_job_id')
    return str(value) if value else None

def _control_hermes_job(hermes_job_id: str, action: str) -> dict:
    try:
        return HermesControlService().control(hermes_job_id, action)
    except HermesControlError as exc:
        raise HTTPException(502, f'Hermes control failed: {exc}') from exc

def _force_hermes_sync(db: Session, user_id: str) -> dict:
    result = _sync_hermes_snapshot(db, user_id, force=True)
    if result.get('status') != 'ok':
        raise HTTPException(502, f"Hermes import failed after control action: {result.get('error') or result.get('reason') or result.get('status')}")
    return result

def _campaign_id_for_employee(db: Session, employee_id: str) -> str | None:
    return db.scalar(
        select(Job.campaign_id)
        .where(Job.employee_id == employee_id, Job.campaign_id.is_not(None))
        .order_by(Job.created_at.desc())
        .limit(1)
    )

def _record_manual_run(db: Session, schedule: Schedule, user: User, control_result: dict | None) -> Job:
    now = datetime.utcnow()
    payload = dict(schedule.payload or {})
    payload.update({'source': payload.get('source') or 'dashboard', 'manual_action': 'run', 'requested_by': user.id})
    job = Job(
        employee_id=schedule.employee_id,
        campaign_id=_campaign_id_for_employee(db, schedule.employee_id),
        connector='hermes',
        task_type=schedule.task_type,
        status=JobStatus.completed,
        payload=payload,
        result={'hermes_control': control_result or {'status': 'local'}},
        logs=['Manual Hermes run requested from dashboard; importer will attach external outputs when Hermes writes them.'],
        attempts=1,
        max_attempts=1,
        started_at=now,
        ended_at=now,
        created_at=now,
    )
    db.add(job)
    db.flush()
    log(db, 'Manual Hermes Run Requested', 'Job', job.id, user_id=user.id)
    return job

def crud(model, schema, label: str):
    @router.get(f'/{label}')
    def list_items(db: Session=Depends(get_db), user: User=Depends(current_user), q: str|None=Query(None)):
        if label in HERMES_SYNCED_LABELS:
            _sync_hermes_snapshot(db, user.id)
        stmt = select(model)
        if q and hasattr(model, 'name'): stmt = stmt.where(model.name.ilike(f'%{q}%'))
        return db.scalars(stmt).all()
    @router.post(f'/{label}')
    def create_item(data: schema, db: Session=Depends(get_db), user: User=Depends(require_write)):
        obj = model(**data.model_dump()); db.add(obj); db.flush()
        log(db, f'{model.__name__} Created', model.__name__, obj.id, getattr(obj, 'company_id', None), user.id)
        db.commit(); db.refresh(obj); return obj
    @router.put(f'/{label}/{{item_id}}')
    def update_item(item_id: str, data: schema, db: Session=Depends(get_db), user: User=Depends(require_write)):
        obj = db.get(model, item_id)
        if not obj: raise HTTPException(404, 'Not found')
        for k,v in data.model_dump().items(): setattr(obj,k,v)
        log(db, f'{model.__name__} Updated', model.__name__, obj.id, getattr(obj, 'company_id', None), user.id)
        db.commit(); db.refresh(obj); return obj
    @router.delete(f'/{label}/{{item_id}}')
    def delete_item(item_id: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
        obj = db.get(model, item_id)
        if not obj: raise HTTPException(404, 'Not found')
        log(db, f'{model.__name__} Deleted', model.__name__, obj.id, getattr(obj, 'company_id', None), user.id)
        db.delete(obj); db.commit(); return {'ok': True}

def _set_auth_cookie(response: Response, user: User) -> str:
    token = create_token(user.id, user.role.value)
    response.set_cookie(
        'voryx_token',
        token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=True,
        samesite='lax',
        path='/',
    )
    return token

@router.post('/auth/login', response_model=TokenOut)
def login(data: LoginIn, response: Response, db: Session=Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email))
    if not user or not verify_password(data.password, user.password_hash): raise HTTPException(401, 'Bad credentials')
    return TokenOut(access_token=_set_auth_cookie(response, user))

@router.post('/auth/login-form')
def login_form(email: str=Form(...), password: str=Form(...), db: Session=Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(password, user.password_hash): raise HTTPException(401, 'Bad credentials')
    response = RedirectResponse(url='/dashboard', status_code=303)
    _set_auth_cookie(response, user)
    return response

@router.post('/auth/logout')
def logout(response: Response):
    response.delete_cookie('voryx_token', path='/', secure=True, samesite='lax')
    return {'ok': True}

@router.post('/auth/password-reset')
def password_reset(): return {'message': 'Password reset workflow placeholder: connect SMTP provider in credential vault.'}

crud(Company, CompanyIn, 'companies'); crud(AIEmployee, EmployeeIn, 'employees'); crud(Campaign, CampaignIn, 'campaigns'); crud(Lead, LeadIn, 'leads'); crud(Schedule, ScheduleIn, 'schedules')

@router.post('/employees/{employee_id}/{action}')
def employee_action(employee_id: str, action: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
    emp = db.get(AIEmployee, employee_id)
    if not emp: raise HTTPException(404, 'Not found')
    action = action.lower()
    mapping = {'start': EmployeeStatus.running, 'resume': EmployeeStatus.running, 'pause': EmployeeStatus.paused, 'stop': EmployeeStatus.stopped, 'restart': EmployeeStatus.running}
    if action == 'duplicate':
        copy = AIEmployee(company_id=emp.company_id, name=f'{emp.name} Copy', employee_type=emp.employee_type, prompt=emp.prompt, daily_limits=emp.daily_limits, rate_limit_per_hour=emp.rate_limit_per_hour, daily_email_limit=emp.daily_email_limit, status=EmployeeStatus.stopped)
        db.add(copy); db.flush(); log(db, 'Employee Duplicated', 'AIEmployee', copy.id, emp.company_id, user.id); db.commit(); return copy
    if action not in mapping and action != 'run': raise HTTPException(400, 'Unsupported action')

    hermes_id = _employee_hermes_job_id(emp)
    control_result = None
    control_action = {'start': 'resume', 'resume': 'resume', 'restart': 'resume', 'pause': 'pause', 'stop': 'pause', 'run': 'run'}.get(action)
    if hermes_id and control_action:
        control_result = _control_hermes_job(hermes_id, control_action)
        _force_hermes_sync(db, user.id)
        db.expire_all()
        emp = db.get(AIEmployee, employee_id)
        if not emp: raise HTTPException(404, 'Not found after Hermes sync')
    elif action != 'run':
        emp.status = mapping[action]
        if action in {'start', 'resume', 'restart'}:
            emp.circuit_breaker_open = False
            emp.paused_reason = None
            emp.last_error = None
            emp.failure_count = 0
        if action in {'pause', 'stop'}:
            emp.paused_reason = f'Manual {action} by {user.email}'

    if action == 'run':
        schedule = db.scalar(select(Schedule).where(Schedule.employee_id == emp.id).order_by(Schedule.name).limit(1))
        if not schedule:
            raise HTTPException(400, 'Employee has no schedule to run')
        _record_manual_run(db, schedule, user, control_result)
    log(db, f'Employee {action.title()}', 'AIEmployee', emp.id, emp.company_id, user.id, {'hermes_control': control_result} if control_result else None)
    db.commit(); db.refresh(emp); return emp

@router.post('/schedules/{schedule_id}/{action}')
def schedule_action(schedule_id: str, action: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
    schedule = db.get(Schedule, schedule_id)
    if not schedule: raise HTTPException(404, 'Not found')
    action = action.lower()
    if action not in {'pause', 'resume', 'run'}: raise HTTPException(400, 'Unsupported action')

    hermes_id = _schedule_hermes_job_id(schedule)
    control_result = None
    if hermes_id:
        control_result = _control_hermes_job(hermes_id, action)
        _force_hermes_sync(db, user.id)
        db.expire_all()
        schedule = db.get(Schedule, schedule_id)
        if not schedule: raise HTTPException(404, 'Not found after Hermes sync')
    else:
        if action == 'pause':
            schedule.is_paused = True
        elif action == 'resume':
            schedule.is_paused = False

    job_id = None
    if action == 'run':
        job_id = _record_manual_run(db, schedule, user, control_result).id
    log(db, f'Schedule {action.title()}', 'Schedule', schedule.id, user_id=user.id, metadata={'hermes_control': control_result, 'job_id': job_id})
    db.commit()
    return {'ok': True, 'schedule_id': schedule.id, 'job_id': job_id, 'hermes_control': control_result}

@router.post('/jobs')
def create_job(data: JobIn, db: Session=Depends(get_db), user: User=Depends(require_write)):
    payload = data.model_dump()
    payload['max_attempts'] = min(max(payload.get('max_attempts') or 1, 1), 3)
    job = Job(**payload); db.add(job); db.flush(); log(db, 'Job Queued', 'Job', job.id, user_id=user.id); db.commit(); db.refresh(job); return job

@router.get('/jobs')
def list_jobs(status: str|None=None, limit: int=Query(500, ge=1, le=1000), db: Session=Depends(get_db), user: User=Depends(current_user)):
    _sync_hermes_snapshot(db, user.id)
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status:
        status_filter = next((s for s in JobStatus if s.value == status or s.name == status.lower()), None)
        if not status_filter: raise HTTPException(400, 'Unsupported job status')
        stmt = stmt.where(Job.status == status_filter)
    return db.scalars(stmt).all()

@router.post('/jobs/{job_id}/retry')
def retry_job(job_id: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
    job = db.get(Job, job_id)
    if not job: raise HTTPException(404, 'Not found')
    if job.status not in {JobStatus.failed, JobStatus.queued}: raise HTTPException(400, 'Only failed or queued jobs can be retried')
    job.status = JobStatus.queued
    job.retry_after = None
    job.ended_at = None
    job.error_message = None
    job.logs = [*(job.logs or []), f'Retry requested by {user.email} at {datetime.utcnow().isoformat()}']
    log(db, 'Job Retry Requested', 'Job', job.id, user_id=user.id)
    db.commit(); db.refresh(job); return job

@router.get('/activity')
def activity(db: Session=Depends(get_db), user: User=Depends(current_user)):
    _sync_hermes_snapshot(db, user.id)
    return db.scalars(select(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(200)).all()

@router.get('/workers/status')
def worker_status(db: Session=Depends(get_db), user: User=Depends(current_user)):
    _sync_hermes_snapshot(db, user.id)
    employees = db.scalars(select(AIEmployee).order_by(AIEmployee.name)).all()
    job_counts = {status.value: db.scalar(select(func.count(Job.id)).where(Job.status == status)) or 0 for status in JobStatus}
    return {
        'employees': [{
            'id': e.id,
            'company_id': e.company_id,
            'name': e.name,
            'employee_type': e.employee_type,
            'status': e.status.value,
            'failure_count': e.failure_count,
            'circuit_breaker_open': e.circuit_breaker_open,
            'paused_reason': e.paused_reason,
            'last_error': e.last_error,
            'last_heartbeat_at': e.last_heartbeat_at,
            'rate_limit_per_hour': e.rate_limit_per_hour,
            'daily_email_limit': e.daily_email_limit,
        } for e in employees],
        'job_counts': job_counts,
        'queued_jobs': job_counts.get(JobStatus.queued.value, 0),
        'running_jobs': job_counts.get(JobStatus.running.value, 0),
        'failed_jobs': job_counts.get(JobStatus.failed.value, 0),
    }

@router.get('/hermes/live')
def hermes_live(db: Session=Depends(get_db), user: User=Depends(current_user)):
    summary = HermesLiveMonitor().summary()
    summary['platform_import'] = _sync_hermes_snapshot(db, user.id)
    return summary

@router.get('/sync/status')
def sync_status(user: User=Depends(current_user)):
    return hermes_sync_status()

@router.get('/system/health')
async def system_health(db: Session=Depends(get_db), user: User=Depends(current_user)):
    platform_import = _sync_hermes_snapshot(db, user.id)
    checks = {}
    try:
        db.execute(text('select 1'))
        checks['database'] = {'status': 'ok'}
    except Exception as exc:
        checks['database'] = {'status': 'error', 'error': str(exc)}
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
        checks['redis'] = {'status': 'ok'}
    except Exception as exc:
        checks['redis'] = {'status': 'error', 'error': str(exc)}
    checks['hermes'] = await get_connector('hermes').health()
    checks['hermes_live'] = HermesLiveMonitor().summary()
    checks['platform_import'] = platform_import
    failed_jobs = db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.failed)) or 0
    checks['jobs'] = {
        'status': 'degraded' if failed_jobs else 'ok',
        'queued': db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.queued)) or 0,
        'running': db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.running)) or 0,
        'failed': failed_jobs,
    }
    blocking = [name for name, check in checks.items() if isinstance(check, dict) and check.get('status') == 'error']
    hermes_status = checks['hermes'].get('status') if isinstance(checks.get('hermes'), dict) else 'unknown'
    hermes_live_status = checks['hermes_live'].get('status') if isinstance(checks.get('hermes_live'), dict) else 'unknown'
    status = 'ok' if not blocking and hermes_status in {'ok', 'unknown'} and hermes_live_status in {'ok', 'unknown', 'unavailable'} else 'degraded'
    return {'status': status, 'checked_at': datetime.utcnow(), 'checks': checks}

@router.get('/reports/ceo')
def ceo_report(db: Session=Depends(get_db), user: User=Depends(current_user)):
    _sync_hermes_snapshot(db, user.id)
    today = datetime.combine(date.today(), datetime.min.time())
    return {
      'todays_leads': db.scalar(select(func.count(Lead.id)).where(Lead.created_at >= today)) or 0,
      'verified_leads': db.scalar(select(func.count(Lead.id)).where(Lead.status == LeadStatus.verified)) or 0,
      'emails_sent': db.scalar(select(func.count(Job.id)).where(Job.task_type == 'Send Outreach', Job.status == JobStatus.completed, Job.created_at >= today)) or 0,
      'replies': db.scalar(select(func.count(Lead.id)).where(Lead.status == LeadStatus.replied)) or 0,
      'meetings': db.scalar(select(func.count(Lead.id)).where(Lead.status == LeadStatus.meeting_booked)) or 0,
      'failed_jobs': db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.failed)) or 0,
      'companies': [{'id': c.id, 'name': c.name} for c in db.scalars(select(Company)).all()]
    }
