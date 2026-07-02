from datetime import datetime, date
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from redis import Redis
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.db import get_db
from app.core.security import create_token, verify_password
from app.models.entities import *
from app.schemas.common import *
from app.services.audit import log
from app.services.connectors import get_connector
from app.services.daily_report import generate_daily_report, render_report, write_report_artifact
from app.services.hermes_control import HermesControlError, HermesControlService
from app.services.hermes_live import HermesLiveMonitor
from app.services.hermes_safety import SAFETY_LOCK_MESSAGE, is_safety_blocked_action, safety_block_result
from app.services.hermes_sync import hermes_sync_status, sync_hermes_snapshot as _sync_hermes_snapshot
from app.services.internal_mail_queue import enqueue_daily_report_delivery, ingest_internal_mail_receipts
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT, validate_report_recipient
from app.services.template_provisioning import (
    PROVISIONED_STATES,
    create_template_sample_job,
    mark_provisioning_failed,
    provision_campaign_template,
    validate_employee_operational_state,
)

router = APIRouter()
oauth2 = OAuth2PasswordBearer(tokenUrl='/api/auth/login', auto_error=False)
HERMES_SYNCED_LABELS = {'companies', 'employees', 'campaigns', 'schedules'}
TERMINAL_JOB_STATUSES = {JobStatus.completed, JobStatus.failed, JobStatus.blocked, JobStatus.cancelled, JobStatus.skipped, JobStatus.imported, JobStatus.synced}
ACTION_LABELS = {'dry-run': 'Dry run', 'test-run': 'Test run', 'run': 'Run'}

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
    if employee.hermes_job_id:
        return str(employee.hermes_job_id)
    limits = employee.daily_limits if isinstance(employee.daily_limits, dict) else {}
    value = limits.get('hermes_job_id')
    return str(value) if value else None

def _schedule_hermes_job_id(schedule: Schedule) -> str | None:
    payload = schedule.payload if isinstance(schedule.payload, dict) else {}
    value = payload.get('hermes_job_id')
    return str(value) if value else None

def _manual_run_block_reason(employee: AIEmployee | None) -> str | None:
    if not employee:
        return None
    if employee.status == EmployeeStatus.archived:
        return 'employee is archived'
    if employee.status not in {EmployeeStatus.running, EmployeeStatus.scheduled}:
        return f'employee is {employee.status.value.lower()}'
    if employee.circuit_breaker_open:
        return 'employee circuit breaker is open'
    return None

def _append_log_once(job: Job, message: str):
    logs = list(job.logs or [])
    if not logs or logs[-1] != message:
        logs.append(message)
    job.logs = logs

def _block_manual_job(db: Session, job: Job, employee: AIEmployee | None, reason: str, user: User) -> Job:
    now = datetime.utcnow()
    job.status = JobStatus.blocked
    job.error_message = f'Manual run blocked: {reason}'
    job.retry_after = None
    job.ended_at = now
    _append_log_once(job, job.error_message)
    log(db, 'Manual Hermes Run Blocked', 'Job', job.id, getattr(employee, 'company_id', None), user.id, {'reason': reason})
    return job

def _action_response(action: str, job: Job | None = None, hermes_control: dict | None = None, message: str | None = None) -> dict:
    label = ACTION_LABELS.get(action, action.replace('-', ' ').title())
    state = 'request_accepted'
    is_terminal = False
    safety_blocked = bool(hermes_control and hermes_control.get('status') == 'safety_blocked')
    if safety_blocked:
        state = 'safety_blocked'
        is_terminal = True
        message = message or hermes_control.get('message') or SAFETY_LOCK_MESSAGE
    if job:
        state = 'safety_blocked' if safety_blocked else job.status.name
        is_terminal = job.status in TERMINAL_JOB_STATUSES
        if not message:
            if job.status == JobStatus.blocked:
                message = f'{label} blocked: {job.error_message or "safety policy blocked the job"}. Job ID: {job.id}'
            elif job.status == JobStatus.queued:
                message = f'{label} queued. Job ID: {job.id}'
            elif job.status == JobStatus.failed:
                message = f'{label} failed: {job.error_message or "worker failed"}. Job ID: {job.id}'
            else:
                message = f'{label} {job.status.value.lower()}. Job ID: {job.id}'
    return {
        'ok': state not in {'failed', 'blocked', 'cancelled', 'skipped', 'safety_blocked'},
        'state': state,
        'status': state,
        'terminal': is_terminal,
        'job_id': getattr(job, 'id', None),
        'message': message or f'{label} request accepted',
        'hermes_control': hermes_control,
    }

def _safety_block_response(action: str, hermes_job_id: str, hermes_job_name: str | None = None, job: Job | None = None) -> dict:
    result = safety_block_result(action, hermes_job_id, hermes_job_name)
    return _action_response(action, job, result, result['message'])

def _enum_member(enum_cls, value):
    if isinstance(value, enum_cls):
        return value
    if value is None:
        return value
    text_value = str(value)
    for member in enum_cls:
        if text_value == member.value or text_value.lower() == member.name.lower():
            return member
    return value

def _coerce_payload(model, payload: dict) -> dict:
    data = dict(payload)
    if 'status' in data:
        if model in {Company, Campaign}:
            data['status'] = _enum_member(Status, data['status'])
        elif model is AIEmployee:
            data['status'] = _enum_member(EmployeeStatus, data['status'])
        elif model is Lead:
            data['status'] = _enum_member(LeadStatus, data['status'])
    return data

def _validate_model_state(db: Session, obj) -> None:
    if isinstance(obj, AIEmployee):
        try:
            validate_employee_operational_state(db, obj)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

def _control_hermes_job(hermes_job_id: str, action: str) -> dict:
    try:
        return HermesControlService().control(hermes_job_id, action)
    except HermesControlError as exc:
        raise HTTPException(502, f'Hermes control failed: {exc}') from exc

def _jobs_json_mode() -> bool:
    mode = (settings.hermes_connector_mode or 'auto').strip().lower()
    return mode in {'jobs_json', 'json', 'file', 'file_backed'}

def _unsupported_dry_run_reason(action: str) -> str | None:
    if action not in {'dry-run', 'test-run'} or not _jobs_json_mode():
        return None
    return (
        f'Hermes {action} is unsupported in jobs_json connector mode because no safe dry-run executor is exposed. '
        'No Hermes HTTP request was made and no email was sent.'
    )

def _force_hermes_sync(db: Session, user_id: str) -> dict:
    result = _sync_hermes_snapshot(db, user_id, force=True)
    if result.get('status') != 'ok':
        raise HTTPException(502, f"Hermes import failed after control action: {result.get('error') or result.get('reason') or result.get('status')}")
    return result

def _refresh_employee_from_hermes(db: Session, employee_id: str, user_id: str) -> AIEmployee:
    _force_hermes_sync(db, user_id)
    db.expire_all()
    employee = db.get(AIEmployee, employee_id)
    if not employee:
        raise HTTPException(404, 'Not found after Hermes sync')
    return employee

def _refresh_schedule_from_hermes(db: Session, schedule_id: str, user_id: str) -> tuple[Schedule, AIEmployee | None]:
    _force_hermes_sync(db, user_id)
    db.expire_all()
    schedule = db.get(Schedule, schedule_id)
    if not schedule:
        raise HTTPException(404, 'Not found after Hermes sync')
    employee = db.get(AIEmployee, schedule.employee_id) if schedule.employee_id else None
    return schedule, employee

def _campaign_id_for_employee(db: Session, employee_id: str) -> str | None:
    return db.scalar(
        select(Job.campaign_id)
        .where(Job.employee_id == employee_id, Job.campaign_id.is_not(None))
        .order_by(Job.created_at.desc())
        .limit(1)
    )

def _record_manual_run(db: Session, schedule: Schedule, user: User, control_result: dict | None, action: str = 'run') -> Job:
    now = datetime.utcnow()
    payload = dict(schedule.payload or {})
    payload.update({'source': payload.get('source') or 'dashboard', 'manual_action': action, 'requested_by': user.id})
    job = Job(
        employee_id=schedule.employee_id,
        campaign_id=_campaign_id_for_employee(db, schedule.employee_id),
        connector='hermes',
        task_type=schedule.task_type,
        status=JobStatus.queued,
        payload=payload,
        result={'hermes_control': control_result or {'status': 'local'}},
        logs=[f'Manual Hermes {action} queued from dashboard; success requires Hermes output or a verified business artifact.'],
        attempts=0,
        max_attempts=1,
        started_at=None,
        ended_at=None,
        created_at=now,
    )
    db.add(job)
    db.flush()
    log(db, 'Manual Hermes Run Requested', 'Job', job.id, user_id=user.id)
    return job

def _filtered_job_stmt(company_id: str | None = None, campaign_id: str | None = None, employee_id: str | None = None):
    stmt = select(Job)
    if company_id:
        stmt = stmt.outerjoin(Campaign, Job.campaign_id == Campaign.id).outerjoin(AIEmployee, Job.employee_id == AIEmployee.id)
        stmt = stmt.where(or_(Campaign.company_id == company_id, AIEmployee.company_id == company_id))
        stmt = stmt.where(or_(AIEmployee.id.is_(None), AIEmployee.status != EmployeeStatus.archived))
    if campaign_id:
        stmt = stmt.where(Job.campaign_id == campaign_id)
    if employee_id:
        stmt = stmt.where(Job.employee_id == employee_id)
    return stmt

def crud(model, schema, label: str):
    @router.get(f'/{label}')
    def list_items(
        db: Session=Depends(get_db),
        user: User=Depends(current_user),
        q: str|None=Query(None),
        company_id: str|None=Query(None),
        campaign_id: str|None=Query(None),
        employee_id: str|None=Query(None),
    ):
        if label in HERMES_SYNCED_LABELS:
            _sync_hermes_snapshot(db, user.id)
        if model is Schedule:
            stmt = select(Schedule).join(AIEmployee, Schedule.employee_id == AIEmployee.id)
            if company_id:
                stmt = stmt.where(AIEmployee.company_id == company_id)
            if campaign_id:
                stmt = stmt.where(AIEmployee.campaign_id == campaign_id)
            if employee_id:
                stmt = stmt.where(Schedule.employee_id == employee_id)
        else:
            stmt = select(model)
            if company_id and hasattr(model, 'company_id'):
                stmt = stmt.where(model.company_id == company_id)
            if campaign_id and hasattr(model, 'campaign_id'):
                stmt = stmt.where(model.campaign_id == campaign_id)
            if employee_id and hasattr(model, 'employee_id'):
                stmt = stmt.where(model.employee_id == employee_id)
        if model is AIEmployee:
            stmt = stmt.where(AIEmployee.status != EmployeeStatus.archived)
        if q and hasattr(model, 'name'): stmt = stmt.where(model.name.ilike(f'%{q}%'))
        if hasattr(model, 'name'):
            stmt = stmt.order_by(model.name)
        return db.scalars(stmt).all()
    @router.post(f'/{label}')
    def create_item(data: schema, db: Session=Depends(get_db), user: User=Depends(require_write)):
        obj = model(**_coerce_payload(model, data.model_dump())); db.add(obj); db.flush()
        if model is Campaign:
            try:
                provision_campaign_template(db, obj, user.id)
            except Exception as exc:
                mark_provisioning_failed(obj, exc)
                db.flush()
                if (obj.campaign_type or 'custom').strip().lower() != 'custom':
                    log(db, 'Campaign Template Provisioning Failed', 'Campaign', obj.id, obj.company_id, user.id, {'error': str(exc)})
        _validate_model_state(db, obj)
        log(db, f'{model.__name__} Created', model.__name__, obj.id, getattr(obj, 'company_id', None), user.id)
        db.commit(); db.refresh(obj); return obj
    @router.put(f'/{label}/{{item_id}}')
    def update_item(item_id: str, data: schema, db: Session=Depends(get_db), user: User=Depends(require_write)):
        obj = db.get(model, item_id)
        if not obj: raise HTTPException(404, 'Not found')
        payload = _coerce_payload(model, data.model_dump())
        metadata = None
        if model is Schedule:
            hermes_id = _schedule_hermes_job_id(obj)
            next_cron = str(payload.get('cron') or obj.cron)
            next_timezone = str(payload.get('timezone') or obj.timezone)
            if hermes_id and (next_cron != obj.cron or next_timezone != obj.timezone):
                metadata = {'hermes_control': HermesControlService().update_schedule(hermes_id, next_cron, next_timezone)}
        previous_campaign_type = getattr(obj, 'campaign_type', None)
        for k,v in payload.items(): setattr(obj,k,v)
        if model is Campaign and (obj.campaign_type or 'custom').strip().lower() != 'custom':
            should_provision = previous_campaign_type != obj.campaign_type or obj.provisioning_state not in {'Provisioned', 'Active', 'Paused'}
            if should_provision:
                try:
                    provision_campaign_template(db, obj, user.id)
                except Exception as exc:
                    mark_provisioning_failed(obj, exc)
                    db.flush()
                    log(db, 'Campaign Template Provisioning Failed', 'Campaign', obj.id, obj.company_id, user.id, {'error': str(exc)})
        _validate_model_state(db, obj)
        log(db, f'{model.__name__} Updated', model.__name__, obj.id, getattr(obj, 'company_id', None), user.id, metadata)
        db.commit(); db.refresh(obj); return obj
    @router.delete(f'/{label}/{{item_id}}')
    def delete_item(item_id: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
        obj = db.get(model, item_id)
        if not obj: raise HTTPException(404, 'Not found')
        if model is AIEmployee:
            obj.status = EmployeeStatus.archived
            obj.paused_reason = f'Archived by {user.email}'
            log(db, 'AIEmployee Archived', 'AIEmployee', obj.id, getattr(obj, 'company_id', None), user.id)
            db.commit(); db.refresh(obj); return obj
        if model is Lead:
            log(db, 'Lead Deleted', 'Lead', obj.id, getattr(obj, 'company_id', None), user.id)
            db.delete(obj); db.commit(); return {'ok': True}
        if hasattr(obj, 'status'):
            obj.status = Status.archived
            log(db, f'{model.__name__} Archived', model.__name__, obj.id, getattr(obj, 'company_id', None), user.id)
            db.commit(); db.refresh(obj); return obj
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

def _safe_redirect_path(path: str | None) -> str:
    if path and path.startswith('/') and not path.startswith('//'):
        return path
    return '/dashboard'

@router.post('/auth/login-form')
def login_form(email: str=Form(...), password: str=Form(...), redirect_to: str|None=Form(None), db: Session=Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email))
    if not user or not verify_password(password, user.password_hash): raise HTTPException(401, 'Bad credentials')
    response = RedirectResponse(url=_safe_redirect_path(redirect_to), status_code=303)
    _set_auth_cookie(response, user)
    return response

@router.post('/auth/logout')
def logout(response: Response):
    response.delete_cookie('voryx_token', path='/', secure=True, samesite='lax')
    return {'ok': True}

@router.post('/auth/password-reset')
def password_reset(): return {'message': 'Password reset workflow placeholder: connect SMTP provider in credential vault.'}

crud(Company, CompanyIn, 'companies'); crud(AIEmployee, EmployeeIn, 'employees'); crud(Campaign, CampaignIn, 'campaigns'); crud(Lead, LeadIn, 'leads'); crud(Schedule, ScheduleIn, 'schedules')

def _campaign_payload(campaign: Campaign, *, state: str = 'ok', hermes_controls: list | None = None, blocked: list | None = None) -> dict:
    return {
        'id': campaign.id,
        'company_id': campaign.company_id,
        'name': campaign.name,
        'description': campaign.description,
        'industry': campaign.industry,
        'target_audience': campaign.target_audience,
        'geographic_area': campaign.geographic_area,
        'daily_lead_goal': campaign.daily_lead_goal,
        'daily_email_goal': campaign.daily_email_goal,
        'daily_email_limit': campaign.daily_email_limit,
        'campaign_type': campaign.campaign_type,
        'provisioning_state': campaign.provisioning_state,
        'provisioning_result': campaign.provisioning_result,
        'timezone': campaign.timezone,
        'allowed_sending_days': campaign.allowed_sending_days,
        'allowed_sending_hours': campaign.allowed_sending_hours,
        'internal_test_recipient': campaign.internal_test_recipient,
        'report_recipient': campaign.report_recipient,
        'dry_run_mode': campaign.dry_run_mode,
        'start_date': campaign.start_date,
        'end_date': campaign.end_date,
        'status': campaign.status,
        'ok': state == 'ok',
        'state': state,
        'hermes_controls': hermes_controls or [],
        'blocked': blocked or [],
    }

def _control_campaign_workers(db: Session, campaign: Campaign, action: str) -> tuple[list, list]:
    hermes_action = 'resume' if action == 'resume' else 'pause'
    controls = []
    blocked = []
    employees = db.scalars(select(AIEmployee).where(AIEmployee.campaign_id == campaign.id, AIEmployee.hermes_job_id.is_not(None))).all()
    for employee in employees:
        hermes_id = _employee_hermes_job_id(employee)
        if not hermes_id:
            continue
        try:
            result = HermesControlService().control(hermes_id, hermes_action)
            controls.append(result)
            if result.get('status') == 'safety_blocked':
                blocked.append({'employee_id': employee.id, 'employee_name': employee.name, 'hermes_job_id': hermes_id, 'reason': result.get('message') or result.get('reason')})
        except HermesControlError as exc:
            blocked.append({'employee_id': employee.id, 'employee_name': employee.name, 'hermes_job_id': hermes_id, 'reason': str(exc)})
    return controls, blocked

@router.post('/campaigns/{campaign_id}/{action}')
def campaign_action(campaign_id: str, action: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, 'Not found')
    action = action.lower()
    if action == 'duplicate':
        data = {
            'company_id': campaign.company_id,
            'name': f'{campaign.name} Copy',
            'description': campaign.description,
            'industry': campaign.industry,
            'target_audience': campaign.target_audience,
            'geographic_area': campaign.geographic_area,
            'daily_lead_goal': campaign.daily_lead_goal,
            'daily_email_goal': campaign.daily_email_goal,
            'daily_email_limit': campaign.daily_email_limit,
            'campaign_type': 'custom',
            'provisioning_state': 'Draft',
            'provisioning_result': {'provisioned': False, 'source_campaign_id': campaign.id},
            'timezone': campaign.timezone,
            'allowed_sending_days': campaign.allowed_sending_days,
            'allowed_sending_hours': campaign.allowed_sending_hours,
            'internal_test_recipient': campaign.internal_test_recipient,
            'report_recipient': campaign.report_recipient,
            'dry_run_mode': campaign.dry_run_mode,
            'start_date': campaign.start_date,
            'end_date': campaign.end_date,
            'status': Status.inactive,
        }
        copy = Campaign(**data)
        db.add(copy); db.flush()
        log(db, 'Campaign Duplicated', 'Campaign', copy.id, campaign.company_id, user.id, {'source_campaign_id': campaign.id})
        db.commit(); db.refresh(copy); return copy
    if action == 'pause':
        campaign.status = Status.inactive
        campaign.provisioning_state = 'Paused' if campaign.provisioning_state in PROVISIONED_STATES else campaign.provisioning_state
    elif action == 'resume':
        campaign.status = Status.active
        campaign.provisioning_state = 'Active' if campaign.provisioning_state in PROVISIONED_STATES else campaign.provisioning_state
    else:
        raise HTTPException(400, 'Unsupported action')
    controls, blocked = _control_campaign_workers(db, campaign, action)
    state = 'partial' if blocked else 'ok'
    metadata = {'hermes_controls': controls, 'blocked': blocked} if controls or blocked else None
    log(db, f'Campaign {action.title()}', 'Campaign', campaign.id, campaign.company_id, user.id, metadata)
    db.commit(); db.refresh(campaign); return _campaign_payload(campaign, state=state, hermes_controls=controls, blocked=blocked)

@router.post('/employees/{employee_id}/{action}')
def employee_action(employee_id: str, action: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
    emp = db.get(AIEmployee, employee_id)
    if not emp: raise HTTPException(404, 'Not found')
    action = action.lower()
    mapping = {'start': EmployeeStatus.running, 'resume': EmployeeStatus.running, 'pause': EmployeeStatus.paused, 'stop': EmployeeStatus.stopped, 'restart': EmployeeStatus.running}
    if action == 'duplicate':
        copy = AIEmployee(company_id=emp.company_id, name=f'{emp.name} Copy', employee_type=emp.employee_type, prompt=emp.prompt, daily_limits=emp.daily_limits, rate_limit_per_hour=emp.rate_limit_per_hour, daily_email_limit=emp.daily_email_limit, status=EmployeeStatus.stopped)
        db.add(copy); db.flush(); log(db, 'Employee Duplicated', 'AIEmployee', copy.id, emp.company_id, user.id); db.commit(); return copy
    if action not in mapping and action not in {'run', 'dry-run'}: raise HTTPException(400, 'Unsupported action')

    hermes_id = _employee_hermes_job_id(emp)
    if hermes_id:
        emp = _refresh_employee_from_hermes(db, employee_id, user.id)
        hermes_id = _employee_hermes_job_id(emp)
    if hermes_id and is_safety_blocked_action(hermes_id, action):
        job = None
        if action in {'run', 'dry-run'}:
            schedule = db.scalar(select(Schedule).where(Schedule.employee_id == emp.id).order_by(Schedule.name).limit(1))
            if schedule:
                job = _record_manual_run(db, schedule, user, None, action)
                _block_manual_job(db, job, emp, SAFETY_LOCK_MESSAGE, user)
        log(db, 'Employee Safety Blocked', 'AIEmployee', emp.id, emp.company_id, user.id, {'hermes_job_id': hermes_id, 'action': action})
        db.commit()
        return _safety_block_response(action, hermes_id, emp.name, job)

    unsupported_reason = _unsupported_dry_run_reason(action)
    if unsupported_reason:
        log(db, 'Employee Dry Run Unsupported', 'AIEmployee', emp.id, emp.company_id, user.id, {'hermes_job_id': hermes_id, 'action': action, 'reason': unsupported_reason})
        db.commit()
        raise HTTPException(501, unsupported_reason)

    run_block_reason = _manual_run_block_reason(emp) if action in {'run', 'dry-run'} else None
    control_result = None
    control_action = {'start': 'resume', 'resume': 'resume', 'restart': 'resume', 'pause': 'pause', 'stop': 'pause', 'run': 'run'}.get(action)
    if hermes_id and control_action and not run_block_reason:
        control_result = _control_hermes_job(hermes_id, control_action)
        emp = _refresh_employee_from_hermes(db, employee_id, user.id)
    elif action in mapping:
        if mapping[action] in {EmployeeStatus.running, EmployeeStatus.scheduled}:
            try:
                validate_employee_operational_state(db, emp, mapping[action])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        emp.status = mapping[action]
        if action in {'start', 'resume', 'restart'}:
            emp.circuit_breaker_open = False
            emp.paused_reason = None
            emp.last_error = None
            emp.failure_count = 0
        if action in {'pause', 'stop'}:
            emp.paused_reason = f'Manual {action} by {user.email}'

    if action in {'run', 'dry-run'}:
        schedule = db.scalar(select(Schedule).where(Schedule.employee_id == emp.id).order_by(Schedule.name).limit(1))
        if not schedule:
            raise HTTPException(400, 'Employee has no schedule to run')
        manual = _record_manual_run(db, schedule, user, control_result, action)
        if action == 'dry-run':
            manual.payload = {**(manual.payload or {}), 'dry_run': True, 'manual_action': 'dry-run'}
            _append_log_once(manual, 'Dry run queued from dashboard; Hermes was not triggered for prospect outreach.')
        reason = run_block_reason or _manual_run_block_reason(emp)
        if reason:
            _block_manual_job(db, manual, emp, reason, user)
        response_payload = _action_response(action, manual, control_result)
    else:
        response_payload = _action_response(action, hermes_control=control_result)
    log(db, f'Employee {action.title()}', 'AIEmployee', emp.id, emp.company_id, user.id, {'hermes_control': control_result} if control_result else None)
    db.commit(); return response_payload

@router.post('/campaigns/{campaign_id}/template/{action}')
def campaign_template_action(campaign_id: str, action: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
    campaign = db.get(Campaign, campaign_id)
    if not campaign: raise HTTPException(404, 'Not found')
    try:
        job = create_template_sample_job(db, campaign, action.lower(), user.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    db.refresh(job)
    return {
        'ok': True,
        'state': job.status.name,
        'status': job.status.name,
        'job_id': job.id,
        'message': (job.logs or ['Template action completed without sending email.'])[-1],
        'result': job.result,
    }

@router.post('/schedules/{schedule_id}/{action}')
def schedule_action(schedule_id: str, action: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
    schedule = db.get(Schedule, schedule_id)
    if not schedule: raise HTTPException(404, 'Not found')
    action = action.lower()
    if action not in {'pause', 'resume', 'run', 'dry-run', 'test-run'}: raise HTTPException(400, 'Unsupported action')

    hermes_id = _schedule_hermes_job_id(schedule)
    employee_for_action = db.get(AIEmployee, schedule.employee_id) if schedule.employee_id else None
    if hermes_id:
        schedule, employee_for_action = _refresh_schedule_from_hermes(db, schedule_id, user.id)
        hermes_id = _schedule_hermes_job_id(schedule)
    if hermes_id and is_safety_blocked_action(hermes_id, action):
        job = None
        if action in {'run', 'dry-run', 'test-run'}:
            job = _record_manual_run(db, schedule, user, None, action)
            _block_manual_job(db, job, employee_for_action, SAFETY_LOCK_MESSAGE, user)
        log(db, 'Schedule Safety Blocked', 'Schedule', schedule.id, user_id=user.id, metadata={'hermes_job_id': hermes_id, 'action': action, 'job_id': getattr(job, 'id', None)})
        db.commit()
        return {'schedule_id': schedule.id, **_safety_block_response(action, hermes_id, schedule.name, job)}

    unsupported_reason = _unsupported_dry_run_reason(action)
    if unsupported_reason:
        log(db, 'Schedule Dry Run Unsupported', 'Schedule', schedule.id, user_id=user.id, metadata={'hermes_job_id': hermes_id, 'action': action, 'reason': unsupported_reason})
        db.commit()
        raise HTTPException(501, unsupported_reason)

    run_block_reason = _manual_run_block_reason(employee_for_action) if action in {'run', 'dry-run', 'test-run'} else None
    control_result = None
    if hermes_id and action in {'pause', 'resume', 'run'} and not run_block_reason:
        control_result = _control_hermes_job(hermes_id, action)
        schedule, employee_for_action = _refresh_schedule_from_hermes(db, schedule_id, user.id)
    else:
        if action == 'pause':
            schedule.is_paused = True
        elif action == 'resume':
            schedule.is_paused = False

    job_id = None
    response_payload = _action_response(action, hermes_control=control_result)
    if action in {'run', 'dry-run', 'test-run'}:
        employee = employee_for_action
        job = _record_manual_run(db, schedule, user, control_result, action)
        if action in {'dry-run', 'test-run'}:
            job.payload = {**(job.payload or {}), 'manual_action': action, 'dry_run': action == 'dry-run', 'test_run': action == 'test-run'}
            _append_log_once(job, f'{action} queued from dashboard; Hermes was not triggered for prospect outreach.')
        reason = run_block_reason or _manual_run_block_reason(employee)
        if reason:
            _block_manual_job(db, job, employee, reason, user)
        job_id = job.id
        response_payload = _action_response(action, job, control_result)
    log(db, f'Schedule {action.title()}', 'Schedule', schedule.id, user_id=user.id, metadata={'hermes_control': control_result, 'job_id': job_id})
    db.commit()
    return {'schedule_id': schedule.id, **response_payload}

@router.post('/jobs')
def create_job(data: JobIn, db: Session=Depends(get_db), user: User=Depends(require_write)):
    payload = data.model_dump()
    payload['max_attempts'] = min(max(payload.get('max_attempts') or 1, 1), 3)
    job = Job(**payload); db.add(job); db.flush(); log(db, 'Job Queued', 'Job', job.id, user_id=user.id); db.commit(); db.refresh(job); return job

@router.get('/jobs')
def list_jobs(
    status: str|None=None,
    company_id: str|None=None,
    campaign_id: str|None=None,
    employee_id: str|None=None,
    limit: int=Query(500, ge=1, le=1000),
    db: Session=Depends(get_db),
    user: User=Depends(current_user),
):
    _sync_hermes_snapshot(db, user.id)
    stmt = _filtered_job_stmt(company_id, campaign_id, employee_id)
    if status:
        status_filter = next((s for s in JobStatus if s.value == status or s.name == status.lower()), None)
        if not status_filter: raise HTTPException(400, 'Unsupported job status')
        stmt = stmt.where(Job.status == status_filter)
    stmt = stmt.order_by(Job.created_at.desc()).limit(limit)
    return db.scalars(stmt).all()

@router.get('/jobs/{job_id}')
def get_job(job_id: str, db: Session=Depends(get_db), user: User=Depends(current_user)):
    job = db.get(Job, job_id)
    if not job: raise HTTPException(404, 'Not found')
    return job

@router.post('/jobs/{job_id}/retry')
def retry_job(job_id: str, db: Session=Depends(get_db), user: User=Depends(require_write)):
    job = db.get(Job, job_id)
    if not job: raise HTTPException(404, 'Not found')
    if job.status not in {JobStatus.failed, JobStatus.queued, JobStatus.blocked, JobStatus.skipped}: raise HTTPException(400, 'Only failed, blocked, skipped, or queued jobs can be retried')
    job.status = JobStatus.queued
    job.retry_after = None
    job.ended_at = None
    job.error_message = None
    job.logs = [*(job.logs or []), f'Retry requested by {user.email} at {datetime.utcnow().isoformat()}']
    log(db, 'Job Retry Requested', 'Job', job.id, user_id=user.id)
    db.commit(); db.refresh(job); return job

@router.get('/activity')
def activity(company_id: str|None=None, db: Session=Depends(get_db), user: User=Depends(current_user)):
    _sync_hermes_snapshot(db, user.id)
    stmt = select(ActivityLog)
    if company_id:
        stmt = stmt.where(ActivityLog.company_id == company_id)
    return db.scalars(stmt.order_by(ActivityLog.created_at.desc()).limit(200)).all()

@router.get('/workers/status')
def worker_status(company_id: str|None=None, db: Session=Depends(get_db), user: User=Depends(current_user)):
    _sync_hermes_snapshot(db, user.id)
    employee_stmt = select(AIEmployee)
    if company_id:
        employee_stmt = employee_stmt.where(AIEmployee.company_id == company_id)
    employees = db.scalars(employee_stmt.order_by(AIEmployee.name)).all()
    job_counts = {
        status.value: db.scalar(
            select(func.count()).select_from(_filtered_job_stmt(company_id).where(Job.status == status).subquery())
        ) or 0
        for status in JobStatus
    }
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

@router.get('/connectors/capabilities')
async def connector_capabilities(user: User=Depends(current_user)):
    return {'hermes': get_connector('hermes').capabilities()}

@router.get('/system/health')
async def system_health(company_id: str|None=None, db: Session=Depends(get_db), user: User=Depends(current_user)):
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
    job_counts = {
        status: db.scalar(
            select(func.count()).select_from(_filtered_job_stmt(company_id).where(Job.status == status).subquery())
        ) or 0
        for status in JobStatus
    }
    failed_jobs = job_counts.get(JobStatus.failed, 0)
    checks['jobs'] = {
        'status': 'degraded' if failed_jobs else 'ok',
        'scope': company_id or 'global',
        'queued': job_counts.get(JobStatus.queued, 0),
        'running': job_counts.get(JobStatus.running, 0),
        'failed': failed_jobs,
        'blocked': job_counts.get(JobStatus.blocked, 0),
        'cancelled': job_counts.get(JobStatus.cancelled, 0),
        'skipped': job_counts.get(JobStatus.skipped, 0),
    }
    blocking = [name for name, check in checks.items() if isinstance(check, dict) and check.get('status') == 'error']
    hermes_status = checks['hermes'].get('status') if isinstance(checks.get('hermes'), dict) else 'unknown'
    hermes_live_status = checks['hermes_live'].get('status') if isinstance(checks.get('hermes_live'), dict) else 'unknown'
    status = 'ok' if not blocking and hermes_status in {'ok', 'unknown'} and hermes_live_status in {'ok', 'unknown', 'unavailable'} else 'degraded'
    return {'status': status, 'checked_at': datetime.utcnow(), 'company_id': company_id, 'checks': checks}

@router.get('/reports/ceo')
def ceo_report(company_id: str|None=None, db: Session=Depends(get_db), user: User=Depends(current_user)):
    _sync_hermes_snapshot(db, user.id)
    today = datetime.combine(date.today(), datetime.min.time())
    sent_statuses = {'sent', 'delivered', 'accepted', 'queued_by_provider'}
    lead_base = select(Lead.id)
    outreach_base = select(OutreachEvent.event_id)
    if company_id:
        lead_base = lead_base.where(Lead.company_id == company_id)
        outreach_base = outreach_base.where(OutreachEvent.company_id == company_id)
    return {
      'todays_leads': db.scalar(select(func.count()).select_from(lead_base.where(Lead.created_at >= today).subquery())) or 0,
      'verified_leads': db.scalar(select(func.count()).select_from(lead_base.where(Lead.status == LeadStatus.verified).subquery())) or 0,
      'emails_sent': db.scalar(select(func.count()).select_from(outreach_base.where(OutreachEvent.status.in_(sent_statuses), OutreachEvent.message_id.is_not(None), OutreachEvent.sent_at >= today, OutreachEvent.dry_run == False).subquery())) or 0,
      'replies': db.scalar(select(func.count()).select_from(lead_base.where(Lead.status == LeadStatus.replied).subquery())) or 0,
      'meetings': db.scalar(select(func.count()).select_from(lead_base.where(Lead.status == LeadStatus.meeting_booked).subquery())) or 0,
      'failed_jobs': db.scalar(select(func.count()).select_from(_filtered_job_stmt(company_id).where(Job.status == JobStatus.failed).subquery())) or 0,
      'companies': [{'id': c.id, 'name': c.name} for c in db.scalars(select(Company)).all()]
    }

@router.get('/reports/daily')
def daily_report(report_date: str|None=None, db: Session=Depends(get_db), user: User=Depends(current_user)):
    report = generate_daily_report(report_date)
    return {'report': report, 'text': render_report(report)}

@router.post('/reports/daily')
def create_daily_report(data: DailyReportRequest, db: Session=Depends(get_db), user: User=Depends(require_write)):
    ingest_internal_mail_receipts(db)
    report = generate_daily_report(data.report_date)
    artifact = write_report_artifact(report)
    delivery = {}
    status = 'generated'
    delivery_job = None
    if data.send_email:
        try:
            recipient = validate_report_recipient(data.recipient or INTERNAL_REPORT_RECIPIENT, report_only_acceptance=True)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            subject = f"Brew It by Sash Outreach Report - {report['report_date']}"
            delivery_job, queued = enqueue_daily_report_delivery(
                db,
                recipient=recipient,
                subject=subject,
                artifact_path=artifact,
                report_date=report['report_date'],
                company_id=data.company_id,
                campaign_id=data.campaign_id,
            )
            delivery = {
                'status': 'queued',
                'delivery_status': 'queued',
                'recipient': recipient,
                'subject': subject,
                'artifact_path': str(artifact),
                'request_id': queued['request']['request_id'],
                'request_path': queued['request_path'],
                'processor_path': queued['processor_path'],
                'job_id': delivery_job.id,
                'message': 'Daily report queued for Hermes internal mail processor; completion requires receipt evidence.',
            }
            try:
                delivery['hermes_control'] = HermesControlService().trigger_internal_mail_processor()
            except HermesControlError as exc:
                delivery['hermes_control_error'] = str(exc)
                _append_log_once(delivery_job, f'Hermes processor trigger failed: {exc}')
        except Exception as exc:
            delivery = {'status': 'failed', 'recipient': recipient, 'artifact_path': str(artifact), 'error': str(exc)}
            delivery_job = Job(
                employee_id=None,
                campaign_id=data.campaign_id,
                connector='hermes',
                task_type='Daily Report',
                status=JobStatus.failed,
                payload={'source': 'internal_mail_queue', 'kind': 'daily_report', 'report_only_acceptance': True, 'report_date': report['report_date']},
                result={'delivery': delivery, 'report_date': report['report_date']},
                logs=[f"Daily report queue failed: {exc}"],
                error_message=str(exc),
                recipient_email=recipient,
                delivery_status='failed',
                evidence_type='mail_queue_request',
                source_output_path=str(artifact),
                verification_reason=str(exc),
                attempts=1,
                max_attempts=1,
                started_at=datetime.utcnow(),
                ended_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            db.add(delivery_job)
            db.flush()
            delivery['job_id'] = delivery_job.id
        status = 'delivery_queued' if delivery_job and delivery_job.status == JobStatus.queued else 'delivery_failed'
    run = ReportRun(
        company_id=data.company_id,
        campaign_id=data.campaign_id,
        report_date=report['report_date'],
        timezone=report['timezone'],
        artifact_path=str(artifact),
        metrics=report['metrics'],
        evidence=report['evidence'],
        delivery_result=delivery,
        status=status,
    )
    db.add(run); db.flush()
    log(db, 'Daily Report Generated', 'ReportRun', run.id, data.company_id, user.id, {'status': status, 'artifact_path': str(artifact), 'job_id': getattr(delivery_job, 'id', None)})
    db.commit(); db.refresh(run)
    return {'report_run': run, 'report': report, 'text': render_report(report), 'delivery': delivery}
