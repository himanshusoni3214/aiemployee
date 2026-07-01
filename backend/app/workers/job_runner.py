import asyncio
from datetime import datetime, timedelta
from sqlalchemy import func, or_, select
from app.core.db import SessionLocal
from app.models.entities import AIEmployee, EmployeeStatus, Job, JobStatus
from app.services.audit import log
from app.services.connectors import get_connector
from app.services.job_evidence import apply_decision, classify_delivery_result, is_delivery_task

EMAIL_TASK_KEYWORDS = ("email", "outreach", "send")

def is_email_task(task_type: str) -> bool:
    task = task_type.lower()
    return any(keyword in task for keyword in EMAIL_TASK_KEYWORDS)

def terminal_block_reason(employee: AIEmployee | None) -> str | None:
    if not employee:
        return None
    if employee.status == EmployeeStatus.archived:
        return "Employee is archived"
    if employee.status not in {EmployeeStatus.running, EmployeeStatus.scheduled}:
        return f"Employee is not running or scheduled: {employee.status.value}"
    if employee.circuit_breaker_open:
        return "Employee circuit breaker is open"
    return None

def append_job_log_once(job: Job, message: str):
    logs = list(job.logs or [])
    if not logs or logs[-1] != message:
        logs.append(message)
    job.logs = logs

def block_job(db, job: Job, employee: AIEmployee | None, message: str):
    now = datetime.utcnow()
    job.status = JobStatus.blocked
    job.error_message = message
    job.retry_after = None
    job.ended_at = now
    append_job_log_once(job, message)
    if employee:
        employee.last_error = message[:1000]
        employee.last_heartbeat_at = now
    log(db, 'Job Blocked By Safety Policy', 'Job', job.id)

def skip_unsupported_connector_job(db, job: Job, employee: AIEmployee | None, result: dict):
    message = result.get('error') or result.get('error_message') or '; '.join(result.get('logs') or []) or 'Connector execution is unsupported'
    now = datetime.utcnow()
    job.status = JobStatus.skipped
    job.error_message = message
    job.retry_after = None
    job.ended_at = now
    job.result = result.get('results', result)
    append_job_log_once(job, message)
    if is_delivery_task(job.task_type):
        job.delivery_status = 'not_sent'
        job.evidence_type = 'unsupported_connector'
        job.verification_reason = message
    if employee:
        employee.last_heartbeat_at = now
    log(db, 'Job Skipped Unsupported Connector', 'Job', job.id)


def fail_and_pause_employee(db, job: Job, employee: AIEmployee | None, message: str):
    job.status = JobStatus.failed
    job.error_message = message
    job.ended_at = datetime.utcnow()
    if job.started_at:
        job.duration_seconds = int((job.ended_at - job.started_at).total_seconds())
    if employee:
        employee.status = EmployeeStatus.paused
        employee.circuit_breaker_open = True
        employee.failure_count = (employee.failure_count or 0) + 1
        employee.paused_reason = message[:500]
        employee.last_error = message[:1000]
        employee.last_heartbeat_at = datetime.utcnow()
        log(db, 'Employee Paused By Circuit Breaker', 'AIEmployee', employee.id, employee.company_id)
    log(db, 'Job Failed', 'Job', job.id)

def under_employee_limits(db, job: Job, employee: AIEmployee | None) -> tuple[bool, str | None]:
    if not employee:
        return True, None
    terminal_reason = terminal_block_reason(employee)
    if terminal_reason:
        return False, terminal_reason

    now = datetime.utcnow()
    hour_ago = now - timedelta(hours=1)
    recent_count = db.scalar(
        select(func.count(Job.id)).where(
            Job.employee_id == employee.id,
            Job.started_at >= hour_ago,
            Job.status.in_([JobStatus.running, JobStatus.completed]),
        )
    ) or 0
    if recent_count >= employee.rate_limit_per_hour:
        job.retry_after = now + timedelta(minutes=15)
        return False, f"Rate limit reached: {recent_count}/{employee.rate_limit_per_hour} jobs in the last hour"

    if is_email_task(job.task_type):
        day_start = datetime(now.year, now.month, now.day)
        sent_today = db.scalar(
            select(func.count(Job.id)).where(
                Job.employee_id == employee.id,
                Job.created_at >= day_start,
                Job.status == JobStatus.completed,
                Job.provider_message_id.is_not(None),
                or_(Job.task_type.ilike("%email%"), Job.task_type.ilike("%outreach%"), Job.task_type.ilike("%send%")),
            )
        ) or 0
        if sent_today >= employee.daily_email_limit:
            employee.status = EmployeeStatus.paused
            employee.circuit_breaker_open = True
            job.retry_after = now + timedelta(days=1)
            employee.paused_reason = f"Daily email limit reached: {sent_today}/{employee.daily_email_limit}"
            log(db, 'Employee Paused By Daily Email Limit', 'AIEmployee', employee.id, employee.company_id)
            return False, employee.paused_reason
    return True, None

async def run_once() -> bool:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        job = db.scalar(
            select(Job)
            .where(Job.status == JobStatus.queued)
            .where((Job.retry_after == None) | (Job.retry_after <= now))  # noqa: E711
            .order_by(Job.created_at)
            .limit(1)
        )
        if not job:
            return False
        employee = db.get(AIEmployee, job.employee_id) if job.employee_id else None
        allowed, reason = under_employee_limits(db, job, employee)
        if not allowed:
            if reason in {
                "Employee is archived",
                "Employee circuit breaker is open",
            } or reason.startswith(("Employee is not running", "Daily email limit reached:")):
                block_job(db, job, employee, reason)
            else:
                append_job_log_once(job, reason)
                log(db, 'Job Deferred By Safety Policy', 'Job', job.id)
            db.commit()
            return True

        job.status = JobStatus.running
        job.started_at = datetime.utcnow()
        job.attempts = (job.attempts or 0) + 1
        if employee:
            employee.last_heartbeat_at = job.started_at
            employee.paused_reason = None
            employee.last_error = None
        log(db, 'Job Running', 'Job', job.id)
        db.commit()
        connector = get_connector(job.connector)
        result = await connector.execute(job.task_type, job.payload)
        job.logs = result.get('logs', [])
        job.result = result.get('results', result)
        if result.get('status') == 'unsupported':
            skip_unsupported_connector_job(db, job, employee, result)
        elif result.get('status') == 'failed':
            message = '; '.join(job.logs[-3:]) or result.get('error') or result.get('error_message') or "Worker failed without logs"
            if job.attempts < job.max_attempts:
                job.status = JobStatus.queued
                job.retry_after = datetime.utcnow() + timedelta(minutes=5 * job.attempts)
                job.error_message = message
                log(db, 'Job Retry Scheduled', 'Job', job.id)
            else:
                if is_delivery_task(job.task_type):
                    job.delivery_status = 'failed'
                    job.evidence_type = 'provider_error'
                    job.verification_reason = message
                fail_and_pause_employee(db, job, employee, message)
        else:
            if is_delivery_task(job.task_type):
                decision = classify_delivery_result(job.task_type, job.payload or {}, result, imported=False)
                apply_decision(job, decision)
            else:
                job.status = JobStatus.completed
                job.error_message = None
                job.delivery_status = 'not_applicable'
                job.evidence_type = 'worker_result'
                job.verification_reason = 'non-delivery task completed by worker result'
            job.retry_after = None
            job.ended_at = datetime.utcnow()
            if job.started_at:
                job.duration_seconds = int((job.ended_at - job.started_at).total_seconds())
            if employee:
                if job.status == JobStatus.failed:
                    employee.status = EmployeeStatus.paused
                    employee.circuit_breaker_open = True
                    employee.failure_count = (employee.failure_count or 0) + 1
                    employee.paused_reason = (job.verification_reason or job.error_message or "delivery evidence missing")[:500]
                    employee.last_error = employee.paused_reason
                else:
                    employee.failure_count = 0
                employee.last_heartbeat_at = job.ended_at
            log(db, 'Job Completed' if job.status == JobStatus.completed else f'Job {job.status.value}', 'Job', job.id)
        if job.status != JobStatus.queued:
            job.ended_at = datetime.utcnow()
        if job.started_at and job.ended_at and job.duration_seconds is None:
            job.duration_seconds = int((job.ended_at - job.started_at).total_seconds())
        db.commit()
        return True
    finally:
        db.close()

async def main():
    while True:
        worked = await run_once()
        await asyncio.sleep(1 if worked else 5)

if __name__ == '__main__':
    asyncio.run(main())
