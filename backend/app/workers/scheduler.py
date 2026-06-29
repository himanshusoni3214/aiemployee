import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select
from app.core.db import SessionLocal
from app.models.entities import AIEmployee, EmployeeStatus, Job, Schedule
from app.services.audit import log

def _field_matches(value: str, current: int) -> bool:
    if value == '*':
        return True
    if value.startswith('*/'):
        try:
            interval = int(value[2:])
            return interval > 0 and current % interval == 0
        except ValueError:
            return False
    try:
        return int(value) == current
    except ValueError:
        return False

def cron_matches(cron: str, now: datetime) -> bool:
    parts = cron.split()
    if len(parts) != 5:
        return False
    minute, hour, day, month, weekday = parts
    return (
        _field_matches(minute, now.minute)
        and _field_matches(hour, now.hour)
        and _field_matches(day, now.day)
        and _field_matches(month, now.month)
        and _field_matches(weekday, now.weekday())
    )

def already_ran_this_minute(schedule: Schedule, now: datetime) -> bool:
    return bool(schedule.last_run_at and schedule.last_run_at.replace(second=0, microsecond=0) == now.replace(second=0, microsecond=0))

async def run_once() -> int:
    db = SessionLocal()
    queued = 0
    try:
        now = datetime.utcnow().replace(second=0, microsecond=0)
        schedules = db.scalars(select(Schedule).where(Schedule.is_paused == False)).all()  # noqa: E712
        for schedule in schedules:
            if already_ran_this_minute(schedule, now) or not cron_matches(schedule.cron, now):
                continue
            employee = db.get(AIEmployee, schedule.employee_id)
            if not employee or employee.status not in {EmployeeStatus.running, EmployeeStatus.scheduled} or employee.circuit_breaker_open:
                schedule.next_run_at = now + timedelta(minutes=1)
                continue
            job = Job(employee_id=schedule.employee_id, connector='hermes', task_type=schedule.task_type, payload=schedule.payload, max_attempts=1)
            db.add(job)
            db.flush()
            schedule.last_run_at = now
            schedule.next_run_at = now + timedelta(minutes=1)
            log(db, 'Scheduled Job Queued', 'Job', job.id, employee.company_id)
            queued += 1
        db.commit()
        return queued
    finally:
        db.close()

async def main():
    while True:
        await run_once()
        await asyncio.sleep(60)

if __name__ == '__main__':
    asyncio.run(main())
