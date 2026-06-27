import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import AIEmployee, Campaign, Company, EmployeeStatus, Job, JobStatus, OutreachEvent, Schedule, Status
from app.services.audit import log
from app.services.hermes_live import HermesLiveMonitor, redact

BREW_COMPANY_NAME = "Brew It By Sash"


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "item"


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _file_dt(path: Path) -> datetime | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})", path.name)
    if not match:
        return None
    try:
        return datetime.strptime("_".join(match.groups()), "%Y-%m-%d_%H_%M_%S")
    except ValueError:
        return None


def _assign(obj: Any, **fields: Any) -> bool:
    changed = False
    for key, value in fields.items():
        if getattr(obj, key) != value:
            setattr(obj, key, value)
            changed = True
    return changed


def _human_name(name: str) -> str:
    clean = re.sub(r"^(voryx-)?brew-it-sash-", "", name or "", flags=re.I)
    clean = clean.replace("-", " ").replace("_", " ").strip()
    return clean.title() if clean else "Hermes Workflow"


def _task_type(name: str) -> str:
    text = name.lower()
    if "lead" in text or "research" in text:
        return "Generate Leads"
    if "verify" in text:
        return "Verify Emails"
    if "reply" in text:
        return "Check Replies"
    if "report" in text:
        return "Daily Report"
    if "outreach" in text or "followup" in text or "email" in text:
        return "Send Outreach"
    return "Run Hermes Workflow"


def _employee_type(name: str) -> str:
    text = name.lower()
    if "lead" in text or "research" in text:
        return "Lead Researcher"
    if "reply" in text:
        return "Reply Handler"
    if "appointment" in text:
        return "Appointment Setter"
    if "report" in text or "crm" in text:
        return "CRM Manager"
    return "Email Outreach"


def _campaign_name(name: str) -> str:
    text = name.lower()
    if "lead" in text or "research" in text:
        return "Brew It By Sash Lead Research"
    if "report" in text:
        return "Brew It By Sash Reporting"
    return "Brew It By Sash Outreach"


def _employee_status(job: dict[str, Any]) -> EmployeeStatus:
    state = (job.get("state") or "").lower()
    last_status = (job.get("last_status") or "").lower()
    if not job.get("enabled") or state in {"paused", "disabled"}:
        return EmployeeStatus.paused
    if last_status in {"error", "failed"}:
        return EmployeeStatus.error
    if state == "running":
        return EmployeeStatus.running
    return EmployeeStatus.stopped


def _job_status(job: dict[str, Any]) -> JobStatus:
    last_status = (job.get("last_status") or "").lower()
    state = (job.get("state") or "").lower()
    if state == "running":
        return JobStatus.running
    if last_status in {"ok", "completed", "success"}:
        return JobStatus.completed
    if last_status in {"error", "failed"}:
        return JobStatus.failed
    return JobStatus.queued if job.get("enabled") else JobStatus.completed


def _campaign_status(job: dict[str, Any]) -> Status:
    return Status.active if job.get("enabled") else Status.inactive


def _schedule_paused(job: dict[str, Any]) -> bool:
    return bool(not job.get("enabled") or job.get("state") in {"paused", "disabled"} or job.get("last_status") == "error")


class HermesImportService:
    def __init__(self, data_path: str | None = None):
        self.monitor = HermesLiveMonitor(data_path)
        self.data_path = Path(data_path or settings.hermes_data_path) if (data_path or settings.hermes_data_path) else None

    def sync(self, db: Session, user_id: str | None = None) -> dict[str, Any]:
        summary = self.monitor.summary()
        if summary.get("status") in {"unavailable", "error"}:
            return {"status": summary.get("status"), "created": 0, "updated": 0, "reason": summary.get("reason") or summary.get("error")}

        created = 0
        updated = 0
        company, was_created, was_updated = self._company(db)
        created += int(was_created)
        updated += int(was_updated)

        by_hermes_id: dict[str, tuple[AIEmployee, Campaign, dict[str, Any]]] = {}
        for hermes_job in summary.get("jobs", []):
            employee, campaign, counts = self._workflow(db, company, hermes_job)
            created += counts["created"]
            updated += counts["updated"]
            hermes_id = hermes_job.get("id")
            if hermes_id:
                by_hermes_id[str(hermes_id)] = (employee, campaign, hermes_job)

        counts = self._outputs(db, company, by_hermes_id)
        created += counts["created"]
        updated += counts["updated"]

        counts = self._outreach_rows(db, company, by_hermes_id)
        created += counts["created"]
        updated += counts["updated"]

        counts = self._outreach_events(db, company)
        created += counts["created"]
        updated += counts["updated"]

        if created or updated:
            db.flush()
            log(db, "Hermes Import Synced", "Hermes", None, company.id, user_id, {"created": created, "updated": updated})
        db.commit()
        return {"status": "ok", "created": created, "updated": updated, "company_id": company.id}

    def _company(self, db: Session) -> tuple[Company, bool, bool]:
        company = db.scalar(select(Company).where(func.lower(Company.name) == BREW_COMPANY_NAME.lower()))
        if company:
            changed = _assign(company, industry=company.industry or "Cold Brew Coffee", status=Status.active)
            return company, False, changed
        company = Company(id="company-brew-it-by-sash", name=BREW_COMPANY_NAME, industry="Cold Brew Coffee", status=Status.active)
        db.add(company)
        db.flush()
        return company, True, False

    def _campaign(self, db: Session, company: Company, hermes_job: dict[str, Any]) -> tuple[Campaign, bool, bool]:
        name = _campaign_name(hermes_job.get("name") or "")
        campaign = db.scalar(select(Campaign).where(Campaign.company_id == company.id, func.lower(Campaign.name) == name.lower()))
        status = _campaign_status(hermes_job)
        if campaign:
            changed = _assign(campaign, industry="Cold Brew Coffee B2B Outreach", daily_lead_goal=25, daily_email_goal=25, status=status)
            return campaign, False, changed
        campaign = Campaign(
            id=f"campaign-{_slug(name)}",
            company_id=company.id,
            name=name,
            industry="Cold Brew Coffee B2B Outreach",
            daily_lead_goal=25,
            daily_email_goal=25,
            status=status,
        )
        db.add(campaign)
        db.flush()
        return campaign, True, False

    def _employee(self, db: Session, company: Company, campaign: Campaign, hermes_job: dict[str, Any]) -> tuple[AIEmployee, bool, bool]:
        hermes_id = str(hermes_job.get("id") or _slug(hermes_job.get("name") or "workflow"))
        name = f"Hermes {_human_name(hermes_job.get('name') or hermes_id)}"
        employee = db.get(AIEmployee, f"employee-hermes-{hermes_id}")
        if not employee:
            employee = db.scalar(select(AIEmployee).where(AIEmployee.company_id == company.id, func.lower(AIEmployee.name) == name.lower()))
        status = _employee_status(hermes_job)
        last_error = hermes_job.get("last_error") or hermes_job.get("last_delivery_error")
        paused_reason = hermes_job.get("paused_reason") or (last_error if status in {EmployeeStatus.error, EmployeeStatus.paused} else None)
        fields = {
            "company_id": company.id,
            "campaign_id": campaign.id,
            "name": name,
            "employee_type": _employee_type(hermes_job.get("name") or ""),
            "hermes_job_id": hermes_id,
            "prompt": hermes_job.get("prompt_excerpt") or "",
            "daily_limits": {"source": "hermes", "hermes_job_id": hermes_id},
            "status": status,
            "rate_limit_per_hour": 20,
            "daily_email_limit": 50,
            "failure_count": 1 if status == EmployeeStatus.error else 0,
            "circuit_breaker_open": status == EmployeeStatus.error,
            "paused_reason": paused_reason,
            "last_error": last_error if status == EmployeeStatus.error else None,
            "last_heartbeat_at": _parse_dt(hermes_job.get("last_run_at")),
        }
        if employee and employee.status == EmployeeStatus.archived:
            fields["status"] = EmployeeStatus.archived
        if employee:
            return employee, False, _assign(employee, **fields)
        employee = AIEmployee(id=f"employee-hermes-{hermes_id}", **fields)
        db.add(employee)
        db.flush()
        return employee, True, False

    def _schedule(self, db: Session, employee: AIEmployee, hermes_job: dict[str, Any]) -> tuple[Schedule, bool, bool]:
        hermes_id = str(hermes_job.get("id") or _slug(hermes_job.get("name") or "workflow"))
        schedule = db.get(Schedule, f"schedule-hermes-{hermes_id}")
        schedule_data = hermes_job.get("schedule") if isinstance(hermes_job.get("schedule"), dict) else {}
        cron = schedule_data.get("expr") or hermes_job.get("schedule_display") or schedule_data.get("display") or "manual"
        fields = {
            "employee_id": employee.id,
            "name": hermes_job.get("name") or hermes_id,
            "cron": cron,
            "task_type": _task_type(hermes_job.get("name") or ""),
            "payload": {
                "source": "hermes",
                "hermes_job_id": hermes_id,
                "hermes_schedule": schedule_data,
                "hermes_state": hermes_job.get("state"),
                "hermes_enabled": hermes_job.get("enabled"),
                "hermes_last_status": hermes_job.get("last_status"),
                "hermes_last_error": hermes_job.get("last_error"),
                "hermes_last_delivery_error": hermes_job.get("last_delivery_error"),
                "hermes_paused_at": hermes_job.get("paused_at"),
                "hermes_paused_reason": hermes_job.get("paused_reason"),
            },
            "is_paused": _schedule_paused(hermes_job),
            "last_run_at": _parse_dt(hermes_job.get("last_run_at")),
            "next_run_at": _parse_dt(hermes_job.get("next_run_at")),
        }
        if schedule:
            return schedule, False, _assign(schedule, **fields)
        schedule = Schedule(id=f"schedule-hermes-{hermes_id}", **fields)
        db.add(schedule)
        db.flush()
        return schedule, True, False

    def _schedule_job(self, db: Session, employee: AIEmployee, campaign: Campaign, hermes_job: dict[str, Any]) -> tuple[Job, bool, bool]:
        hermes_id = str(hermes_job.get("id") or _slug(hermes_job.get("name") or "workflow"))
        job = db.get(Job, f"hermes-schedule-{hermes_id}")
        status = _job_status(hermes_job)
        last_run = _parse_dt(hermes_job.get("last_run_at")) or _parse_dt(hermes_job.get("created_at")) or datetime.utcnow()
        error = hermes_job.get("last_error") or hermes_job.get("last_delivery_error")
        fields = {
            "employee_id": employee.id,
            "campaign_id": campaign.id,
            "connector": "hermes",
            "task_type": _task_type(hermes_job.get("name") or ""),
            "status": status,
            "payload": {"source": "hermes", "kind": "schedule_state", "hermes_job_id": hermes_id, "name": hermes_job.get("name")},
            "result": {"hermes": hermes_job},
            "logs": [
                f"Imported Hermes schedule {hermes_id}",
                f"state={hermes_job.get('state') or 'unknown'}",
                f"enabled={hermes_job.get('enabled')}",
                f"last_status={hermes_job.get('last_status') or 'unknown'}",
                f"last_run_at={hermes_job.get('last_run_at') or 'never'}",
            ],
            "error_message": error if status == JobStatus.failed else None,
            "attempts": 1 if hermes_job.get("last_run_at") else 0,
            "max_attempts": 1,
            "started_at": last_run if hermes_job.get("last_run_at") else None,
            "ended_at": last_run if status in {JobStatus.completed, JobStatus.failed} else None,
            "created_at": last_run,
        }
        if job:
            return job, False, _assign(job, **fields)
        job = Job(id=f"hermes-schedule-{hermes_id}", **fields)
        db.add(job)
        db.flush()
        return job, True, False

    def _workflow(self, db: Session, company: Company, hermes_job: dict[str, Any]) -> tuple[AIEmployee, Campaign, dict[str, int]]:
        created = 0
        updated = 0
        campaign, was_created, was_updated = self._campaign(db, company, hermes_job)
        created += int(was_created)
        updated += int(was_updated)
        employee, was_created, was_updated = self._employee(db, company, campaign, hermes_job)
        created += int(was_created)
        updated += int(was_updated)
        schedule, was_created, was_updated = self._schedule(db, employee, hermes_job)
        created += int(was_created)
        updated += int(was_updated)
        _, was_created, was_updated = self._schedule_job(db, employee, campaign, hermes_job)
        created += int(was_created)
        updated += int(was_updated)
        return employee, campaign, {"created": created, "updated": updated}

    def _outputs(self, db: Session, company: Company, by_hermes_id: dict[str, tuple[AIEmployee, Campaign, dict[str, Any]]]) -> dict[str, int]:
        created = 0
        updated = 0
        if not self.data_path:
            return {"created": 0, "updated": 0}
        output_dir = self.data_path / "cron" / "output"
        if not output_dir.exists():
            return {"created": 0, "updated": 0}
        for path in sorted(output_dir.glob("*/*.md")):
            if not path.is_file():
                continue
            hermes_id = path.parent.name
            employee, campaign, hermes_job = by_hermes_id.get(hermes_id) or self._fallback_workflow(db, company, hermes_id)
            relative = str(path.relative_to(self.data_path))
            job_id = f"hermes-output-{_hash(relative)}"
            content = path.read_text(encoding="utf-8", errors="replace")[:20000]
            error_line = self._error_line(content)
            created_at = _file_dt(path) or datetime.fromtimestamp(path.stat().st_mtime)
            fields = {
                "employee_id": employee.id,
                "campaign_id": campaign.id,
                "connector": "hermes",
                "task_type": _task_type(hermes_job.get("name") or hermes_id),
                "status": JobStatus.failed if error_line else JobStatus.completed,
                "payload": {"source": "hermes", "kind": "output_file", "hermes_job_id": hermes_id, "output_path": relative},
                "result": {"output_path": relative, "size_bytes": path.stat().st_size},
                "logs": [f"Imported Hermes output {relative}"],
                "error_message": error_line,
                "attempts": 1,
                "max_attempts": 1,
                "started_at": created_at,
                "ended_at": created_at,
                "created_at": created_at,
            }
            job = db.get(Job, job_id)
            if job:
                updated += int(_assign(job, **fields))
            else:
                db.add(Job(id=job_id, **fields))
                created += 1
        db.flush()
        return {"created": created, "updated": updated}

    def _outreach_rows(self, db: Session, company: Company, by_hermes_id: dict[str, tuple[AIEmployee, Campaign, dict[str, Any]]]) -> dict[str, int]:
        if not self.data_path:
            return {"created": 0, "updated": 0}
        log_file = self.data_path / "outreach_log.csv"
        if not log_file.exists():
            return {"created": 0, "updated": 0}
        employee, campaign, _ = self._outreach_workflow(db, company, by_hermes_id)
        created = 0
        updated = 0
        with log_file.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                key = "|".join([row.get("timestamp") or "", row.get("recipient") or row.get("email") or "", row.get("status") or ""])
                if not key.strip("|"):
                    continue
                job_id = f"hermes-outreach-{_hash(key)}"
                status = (row.get("status") or "").lower()
                created_at = _parse_dt(row.get("timestamp")) or datetime.utcnow()
                failed = status in {"failed", "error", "bounced"}
                fields = {
                    "employee_id": employee.id,
                    "campaign_id": campaign.id,
                    "connector": "hermes",
                    "task_type": "Send Outreach",
                    "status": JobStatus.failed if failed else JobStatus.completed,
                    "payload": {"source": "hermes", "kind": "outreach_log", "status": row.get("status"), "recipient": redact(row.get("recipient") or row.get("email") or "")},
                    "result": {"business": redact(row.get("business") or ""), "note": redact(row.get("note") or "")},
                    "logs": [f"Imported Hermes outreach row {row.get('timestamp') or ''}".strip()],
                    "error_message": redact(row.get("note") or "") if failed else None,
                    "attempts": 1,
                    "max_attempts": 1,
                    "started_at": created_at,
                    "ended_at": created_at,
                    "created_at": created_at,
                }
                job = db.get(Job, job_id)
                if job:
                    updated += int(_assign(job, **fields))
                else:
                    db.add(Job(id=job_id, **fields))
                    created += 1
        db.flush()
        return {"created": created, "updated": updated}

    def _outreach_events(self, db: Session, company: Company) -> dict[str, int]:
        if not self.data_path:
            return {"created": 0, "updated": 0}
        created = 0
        updated = 0
        for path in (self.data_path / "outreach_events.jsonl", self.data_path / "home" / "leads" / "outreach_events.jsonl"):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                event_id = str(row.get("event_id") or _hash(line))
                fields = {
                    "company_id": row.get("company_id") or company.id,
                    "campaign_id": row.get("campaign_id"),
                    "employee_id": row.get("employee_id"),
                    "lead_id": row.get("lead_id"),
                    "recipient": redact(row.get("recipient") or ""),
                    "business": row.get("business"),
                    "subject": row.get("subject"),
                    "attempted_at": _parse_dt(row.get("attempted_at")),
                    "sent_at": _parse_dt(row.get("sent_at")),
                    "status": str(row.get("status") or "unknown"),
                    "message_id": row.get("message_id"),
                    "thread_id": row.get("thread_id"),
                    "provider": row.get("provider"),
                    "error_code": row.get("error_code"),
                    "error_message": redact(row.get("error_message")),
                    "dry_run": bool(row.get("dry_run")),
                    "job_run_id": row.get("job_run_id"),
                    "source_file": str(path.relative_to(self.data_path)),
                    "raw": {**row, "recipient": redact(row.get("recipient") or ""), "error_message": redact(row.get("error_message"))},
                }
                event = db.get(OutreachEvent, event_id)
                if event:
                    updated += int(_assign(event, **fields))
                else:
                    db.add(OutreachEvent(event_id=event_id, **fields))
                    created += 1
        db.flush()
        return {"created": created, "updated": updated}

    def _fallback_workflow(self, db: Session, company: Company, hermes_id: str) -> tuple[AIEmployee, Campaign, dict[str, Any]]:
        hermes_job = {"id": hermes_id, "name": f"hermes-{hermes_id}", "enabled": False, "state": "archived"}
        campaign, _, _ = self._campaign(db, company, hermes_job)
        employee, _, _ = self._employee(db, company, campaign, hermes_job)
        return employee, campaign, hermes_job

    def _outreach_workflow(self, db: Session, company: Company, by_hermes_id: dict[str, tuple[AIEmployee, Campaign, dict[str, Any]]]) -> tuple[AIEmployee, Campaign, dict[str, Any]]:
        for employee, campaign, hermes_job in by_hermes_id.values():
            if "outreach" in (hermes_job.get("name") or "").lower():
                return employee, campaign, hermes_job
        return self._fallback_workflow(db, company, "outreach-log")

    def _error_line(self, content: str) -> str | None:
        for line in content.splitlines():
            lowered = line.lower()
            if "runtimeerror:" in lowered or "traceback" in lowered or "key limit exceeded" in lowered or "http 403" in lowered:
                return redact(line.strip())[:1000]
        return None
