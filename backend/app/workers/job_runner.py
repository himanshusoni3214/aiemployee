import asyncio
from datetime import datetime, timedelta
from sqlalchemy import func, select
from app.core.db import SessionLocal
from app.models.entities import AIEmployee, EmployeeStatus, Job, JobStatus
from app.services.audit import log
from app.services.connectors import get_connector

EMAIL_TASK_KEYWORDS = ("email", "outreach", "send")

def is_email_task(task_type: str) -> bool:
    task = task_type.lower()
    return any(keyword in task for keyword in EMAIL_TASK_KEYWORDS)

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
    if employee.status != EmployeeStatus.running:
        job.retry_after = datetime.utcnow() + timedelta(minutes=5)
        return False, f"Employee is not running: {employee.status.value}"
    if employee.circuit_breaker_open:
        job.retry_after = datetime.utcnow() + timedelta(minutes=15)
        return False, "Employee circuit breaker is open"

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
                Job.task_type.ilike("%email%"),
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
            job.logs = [*(job.logs or []), reason]
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
        if result.get('status') == 'failed':
            message = '; '.join(job.logs[-3:]) or "Worker failed without logs"
            if job.attempts < job.max_attempts:
                job.status = JobStatus.queued
                job.retry_after = datetime.utcnow() + timedelta(minutes=5 * job.attempts)
                job.error_message = message
                log(db, 'Job Retry Scheduled', 'Job', job.id)
            else:
                fail_and_pause_employee(db, job, employee, message)
        else:
            job.status = JobStatus.completed
            job.error_message = None
            job.retry_after = None
            job.ended_at = datetime.utcnow()
            if job.started_at:
                job.duration_seconds = int((job.ended_at - job.started_at).total_seconds())
            if employee:
                employee.failure_count = 0
                employee.last_heartbeat_at = job.ended_at
            log(db, 'Job Completed', 'Job', job.id)
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
