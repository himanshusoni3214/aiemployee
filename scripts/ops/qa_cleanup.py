#!/usr/bin/env python3
"""Safely archive QA records without touching production company data."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

from sqlalchemy import func, or_, select

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.db import SessionLocal  # noqa: E402
from app.models.entities import (  # noqa: E402
    AIEmployee,
    ActivityLog,
    Campaign,
    Company,
    EmployeeStatus,
    Job,
    JobStatus,
    Role,
    Schedule,
    Status,
    User,
    OutreachEvent,
)

SAFE_PREFIXES = ('QA-', 'QA-AUDIT-', 'QA Audit', 'QA ', 'Test')
PROTECTED_COMPANY_NAMES = {'brew it by sash'}
SENT_STATUSES = {'sent', 'delivered', 'accepted', 'queued_by_provider'}


def validate_prefix(prefix: str):
    if not any(prefix.startswith(safe) for safe in SAFE_PREFIXES):
        raise ValueError(f"Refusing cleanup for unsafe prefix {prefix!r}; use one of {', '.join(SAFE_PREFIXES)}")



def prospect_send_count(db, company_id: str) -> int:
    return db.scalar(
        select(func.count()).select_from(OutreachEvent).where(
            OutreachEvent.company_id == company_id,
            OutreachEvent.dry_run == False,
            OutreachEvent.status.in_(SENT_STATUSES),
            OutreachEvent.message_id.is_not(None),
        )
    ) or 0


def disable_hermes_jobs(hermes_job_ids: set[str], dry_run: bool) -> dict:
    if not hermes_job_ids:
        return {'requested': 0, 'disabled': 0, 'errors': []}
    try:
        from app.services.hermes_control import HermesControlService
        control = HermesControlService()
        raw = control._read_jobs()
        disabled = 0
        errors = []
        for hermes_id in sorted(hermes_job_ids):
            job = control._find_job(raw, hermes_id)
            if not job:
                errors.append(f'{hermes_id}: not found')
                continue
            if not dry_run:
                before = (job.get('enabled'), job.get('state'))
                job['enabled'] = False
                job['state'] = 'paused'
                job['next_run_at'] = None
                job['paused_reason'] = 'Paused by Voryx safe test-company cleanup'
                if before != (job.get('enabled'), job.get('state')):
                    disabled += 1
            else:
                disabled += 1
        if not dry_run:
            control._write_jobs(raw)
        return {'requested': len(hermes_job_ids), 'disabled': disabled, 'errors': errors, 'backup_path': getattr(control, 'last_backup_path', None)}
    except Exception as exc:
        return {'requested': len(hermes_job_ids), 'disabled': 0, 'errors': [str(exc)]}

def ids(items: Iterable[object]) -> list[str]:
    return [item.id for item in items]


def name_matches(column, prefix: str):
    return column.ilike(f'{prefix}%')


def add_log(db, action: str, entity_type: str, entity_id: str | None, company_id: str | None, user_id: str, dry_run: bool):
    if dry_run:
        return
    db.add(ActivityLog(action=action, entity_type=entity_type, entity_id=entity_id, company_id=company_id, user_id=user_id))


def cleanup_qa_records(db, prefix: str, admin_email: str, dry_run: bool = True) -> dict:
    validate_prefix(prefix)
    admin = db.scalar(select(User).where(User.email == admin_email))
    if not admin or admin.role != Role.admin or not admin.is_active:
        raise ValueError(f'{admin_email} is not an active admin user')

    companies = []
    for company in db.scalars(select(Company).where(name_matches(Company.name, prefix))).all():
        if company.name.lower() in PROTECTED_COMPANY_NAMES:
            continue
        if prefix == 'Test' and prospect_send_count(db, company.id) > 0:
            continue
        companies.append(company)
    company_ids = set(ids(companies))

    campaign_filters = [name_matches(Campaign.name, prefix)]
    if company_ids:
        campaign_filters.append(Campaign.company_id.in_(company_ids))
    campaigns = db.scalars(select(Campaign).where(or_(*campaign_filters))).all()
    campaign_ids = set(ids(campaigns))

    employee_filters = [name_matches(AIEmployee.name, prefix)]
    if company_ids:
        employee_filters.append(AIEmployee.company_id.in_(company_ids))
    if campaign_ids:
        employee_filters.append(AIEmployee.campaign_id.in_(campaign_ids))
    employees = db.scalars(select(AIEmployee).where(or_(*employee_filters))).all()
    employee_ids = set(ids(employees))

    schedule_filters = [name_matches(Schedule.name, prefix)]
    if employee_ids:
        schedule_filters.append(Schedule.employee_id.in_(employee_ids))
    schedules = db.scalars(select(Schedule).where(or_(*schedule_filters))).all()

    job_filters = []
    if campaign_ids:
        job_filters.append(Job.campaign_id.in_(campaign_ids))
    if employee_ids:
        job_filters.append(Job.employee_id.in_(employee_ids))
    jobs = db.scalars(select(Job).where(or_(*job_filters))).all() if job_filters else []

    summary = {
        'dry_run': dry_run,
        'prefix': prefix,
        'admin_email': admin_email,
        'counts': {
            'companies': len(companies),
            'campaigns': len(campaigns),
            'employees': len(employees),
            'schedules': len(schedules),
            'jobs': len(jobs),
        },
        'protected_company_names': sorted(PROTECTED_COMPANY_NAMES),
    }
    hermes_job_ids = {str(employee.hermes_job_id) for employee in employees if employee.hermes_job_id}
    summary['hermes_jobs'] = disable_hermes_jobs(hermes_job_ids, dry_run=True)
    if dry_run:
        return summary
    summary['hermes_jobs'] = disable_hermes_jobs(hermes_job_ids, dry_run=False)

    now = datetime.utcnow()
    for company in companies:
        company.status = Status.archived
        add_log(db, 'QA Company Archived By Cleanup', 'Company', company.id, company.id, admin.id, dry_run)
    for campaign in campaigns:
        campaign.status = Status.archived
        add_log(db, 'QA Campaign Archived By Cleanup', 'Campaign', campaign.id, campaign.company_id, admin.id, dry_run)
    for employee in employees:
        employee.status = EmployeeStatus.archived
        employee.paused_reason = f'Archived by QA cleanup for prefix {prefix}'
        employee.circuit_breaker_open = False
        add_log(db, 'QA Employee Archived By Cleanup', 'AIEmployee', employee.id, employee.company_id, admin.id, dry_run)
    for schedule in schedules:
        schedule.is_paused = True
        add_log(db, 'QA Schedule Paused By Cleanup', 'Schedule', schedule.id, None, admin.id, dry_run)
    for job in jobs:
        if job.status in {JobStatus.queued, JobStatus.running}:
            job.status = JobStatus.cancelled
            job.error_message = f'Cancelled by QA cleanup for prefix {prefix}'
            job.retry_after = None
            job.ended_at = now
            job.logs = [*(job.logs or []), job.error_message]
            add_log(db, 'QA Job Cancelled By Cleanup', 'Job', job.id, None, admin.id, dry_run)

    db.commit()
    return summary



def cleanup_test_companies(db, admin_email: str, dry_run: bool = True) -> dict:
    return cleanup_qa_records(db, 'Test', admin_email, dry_run=dry_run)

def main() -> int:
    parser = argparse.ArgumentParser(description='Archive QA records by a safe prefix.')
    parser.add_argument('--prefix', required=False, default='QA Audit', help='Safe QA prefix, such as QA-, QA Audit, or Test')
    parser.add_argument('--test-companies', action='store_true', help='Archive safe Test* companies with zero prospect sends')
    parser.add_argument('--admin-email', required=True, help='Admin user recorded in activity logs')
    parser.add_argument('--apply', action='store_true', help='Apply changes. Default is dry run.')
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = cleanup_test_companies(db, args.admin_email, dry_run=not args.apply) if args.test_companies else cleanup_qa_records(db, args.prefix, args.admin_email, dry_run=not args.apply)
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        db.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
