#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models.entities import CallAttempt, CallCampaignSettings, RetellAgentMigration
from app.services.hermes_safety import (
    OUTREACH_FOLLOWUP_HERMES_JOB_ID,
    is_safety_locked_hermes_job_id,
)


BIBS_JOB_IDS = (
    "47caae0a6a59",
    "0d0c20e25f55",
    OUTREACH_FOLLOWUP_HERMES_JOB_ID,
    "5881b72113ce",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--jobs-json", required=True)
    args = parser.parse_args()
    raw_jobs = json.loads(Path(args.jobs_json).read_text())
    jobs = raw_jobs if isinstance(raw_jobs, list) else raw_jobs.get("jobs", raw_jobs.get("items", []))
    jobs_by_id = {str(job.get("id")): job for job in jobs if isinstance(job, dict)}
    with SessionLocal() as db:
        attempt_count = db.scalar(select(func.count()).select_from(CallAttempt)) or 0
        provider_ids = Counter(
            value or "none"
            for value in db.scalars(select(CallAttempt.provider_agent_id)).all()
        )
        migration = db.scalar(
            select(RetellAgentMigration).order_by(RetellAgentMigration.created_at.desc()).limit(1)
        )
        calling_settings = db.scalar(
            select(CallCampaignSettings).order_by(CallCampaignSettings.created_at.desc()).limit(1)
        )
    result = {
        "call_attempt_count": attempt_count,
        "call_attempts_by_provider_agent_id": dict(sorted(provider_ids.items())),
        "migration": {
            "legacy_agent_id": migration.legacy_agent_id,
            "successor_agent_id": migration.successor_agent_id,
            "conversation_flow_id": migration.conversation_flow_id,
            "cutover_at": migration.cutover_at.isoformat() if migration.cutover_at else None,
            "rollback_status": migration.rollback_status,
            "authorization_recorded": bool(migration.user_authorization),
        } if migration else None,
        "calling_safety": {
            "internal_test_enabled": calling_settings.internal_test_enabled,
            "prospect_calling_enabled": calling_settings.prospect_calling_enabled,
            "automated_queue_enabled": calling_settings.automated_queue_enabled,
            "daily_call_limit": calling_settings.daily_call_limit,
            "hourly_call_limit": calling_settings.hourly_call_limit,
            "concurrent_call_limit": calling_settings.concurrent_call_limit,
        } if calling_settings else None,
        "bibs_jobs": {
            job_id: {
                "name": (jobs_by_id.get(job_id) or {}).get("name"),
                "enabled": (jobs_by_id.get(job_id) or {}).get("enabled"),
                "state": (jobs_by_id.get(job_id) or {}).get("state"),
                "next_run_at": (jobs_by_id.get(job_id) or {}).get("next_run_at"),
                "safety_locked": is_safety_locked_hermes_job_id(job_id),
            }
            for job_id in BIBS_JOB_IDS
        },
    }
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
