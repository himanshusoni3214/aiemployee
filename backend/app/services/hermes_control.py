import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.hermes_live import redact
from app.services.hermes_safety import is_safety_blocked_action, safety_block_result
from app.services.internal_mail_queue import PROCESSOR_JOB_ID, PROCESSOR_JOB_NAME


class HermesControlError(RuntimeError):
    pass


class HermesControlService:
    def __init__(self, data_path: str | None = None):
        self.data_path = Path(data_path or settings.hermes_data_path) if (data_path or settings.hermes_data_path) else None
        self.last_backup_path: str | None = None

    @property
    def jobs_file(self) -> Path:
        if not self.data_path:
            raise HermesControlError("HERMES_DATA_PATH is not configured")
        return self.data_path / "cron" / "jobs.json"

    def control(self, hermes_job_id: str, action: str) -> dict[str, Any]:
        action = action.lower()
        if action not in {"pause", "resume", "run"}:
            raise HermesControlError(f"Unsupported Hermes action: {action}")

        raw = self._read_jobs()
        job = self._find_job(raw, hermes_job_id)
        if not job:
            raise HermesControlError(f"Hermes job not found: {hermes_job_id}")
        if is_safety_blocked_action(hermes_job_id, action):
            return safety_block_result(action, hermes_job_id, str(job.get("name") or ""))
        return self._control_job(raw, job, action)

    def control_matching(self, required_terms: list[str], action: str) -> dict[str, Any]:
        action = action.lower()
        if action not in {"pause", "resume", "run"}:
            raise HermesControlError(f"Unsupported Hermes action: {action}")
        terms = [term.lower() for term in required_terms if term.strip()]
        if not terms:
            raise HermesControlError("No Hermes job match terms provided")

        raw = self._read_jobs()
        matches = []
        for job in self._jobs(raw):
            name = str(job.get("name") or job.get("id") or "").lower()
            if all(term in name for term in terms):
                matches.append(job)
        if not matches:
            raise HermesControlError(f"Hermes job not found for terms: {', '.join(terms)}")
        if len(matches) > 1:
            names = ", ".join(str(job.get("name") or job.get("id")) for job in matches[:5])
            raise HermesControlError(f"Hermes job match was ambiguous for terms {', '.join(terms)}: {names}")
        hermes_id = str(matches[0].get("id") or "")
        if is_safety_blocked_action(hermes_id, action):
            return safety_block_result(action, hermes_id, str(matches[0].get("name") or ""))
        return self._control_job(raw, matches[0], action)

    def update_schedule(self, hermes_job_id: str, cron: str, timezone_name: str) -> dict[str, Any]:
        self._validate_cron(cron)
        raw = self._read_jobs()
        job = self._find_job(raw, hermes_job_id)
        if not job:
            raise HermesControlError(f"Hermes job not found: {hermes_job_id}")
        schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
        before = {
            "schedule": schedule,
            "schedule_display": job.get("schedule_display"),
            "next_run_at": job.get("next_run_at"),
        }

        schedule = {**schedule, "expr": cron, "timezone": timezone_name}
        job["schedule"] = schedule
        job["schedule_display"] = cron
        after = {
            "schedule": job.get("schedule"),
            "schedule_display": job.get("schedule_display"),
            "next_run_at": job.get("next_run_at"),
        }
        no_change = after == before
        if not no_change:
            self._write_jobs(raw)
        return {
            "status": "ok",
            "mode": "jobs_json",
            "action": "update_schedule",
            "no_change": no_change,
            "hermes_job_id": hermes_job_id,
            "hermes_job_name": str(job.get("name") or ""),
            "before": redact(json.dumps(before, default=str)),
            "after": after,
            "backup_path": self.last_backup_path,
        }

    def ensure_internal_mail_processor_job(self) -> dict[str, Any]:
        raw = self._read_jobs()
        job = self._find_job(raw, PROCESSOR_JOB_ID)
        command = "python3 /opt/data/home/voryx_mail_queue/process_internal_mail_queue.py"
        desired = {
            "id": PROCESSOR_JOB_ID,
            "name": PROCESSOR_JOB_NAME,
            "enabled": True,
            "state": "scheduled",
            "schedule": {"expr": "*/1 * * * *", "timezone": "America/Toronto"},
            "schedule_display": "*/1 * * * *",
            "command": command,
            "working_directory": "/opt/data/home/voryx_mail_queue",
            "description": "Processes only Voryx internal daily-report mail queue items for the approved internal recipient.",
            "source": "voryx_ops",
            "safety": {
                "kind": "internal_report_mail_only",
                "allowed_recipient": "himanshusoni3214@gmail.com",
                "prospect_outreach": False,
            },
        }
        if job:
            before = dict(job)
            changed = False
            for key, value in desired.items():
                if job.get(key) != value:
                    job[key] = value
                    changed = True
            if changed:
                self._write_jobs(raw)
            return {
                "status": "ok",
                "mode": "jobs_json",
                "action": "ensure_internal_mail_processor",
                "created": False,
                "updated": changed,
                "hermes_job_id": PROCESSOR_JOB_ID,
                "hermes_job_name": PROCESSOR_JOB_NAME,
                "before": redact(json.dumps(before, default=str)) if changed else None,
            }
        self._append_job(raw, desired)
        self._write_jobs(raw)
        return {
            "status": "ok",
            "mode": "jobs_json",
            "action": "ensure_internal_mail_processor",
            "created": True,
            "updated": False,
            "hermes_job_id": PROCESSOR_JOB_ID,
            "hermes_job_name": PROCESSOR_JOB_NAME,
        }

    def trigger_internal_mail_processor(self) -> dict[str, Any]:
        ensured = self.ensure_internal_mail_processor_job()
        control = self.control(PROCESSOR_JOB_ID, "run")
        return {"status": "ok", "ensure": ensured, "control": control}

    def _control_job(self, raw: Any, job: dict[str, Any], action: str) -> dict[str, Any]:
        before = {
            "enabled": job.get("enabled"),
            "state": job.get("state"),
            "next_run_at": job.get("next_run_at"),
            "last_status": job.get("last_status"),
            "last_error": job.get("last_error"),
            "last_delivery_error": job.get("last_delivery_error"),
            "paused_at": job.get("paused_at"),
            "paused_reason": job.get("paused_reason"),
        }
        self._apply(job, action)
        after = {
            "enabled": job.get("enabled"),
            "state": job.get("state"),
            "next_run_at": job.get("next_run_at"),
            "last_status": job.get("last_status"),
            "last_error": job.get("last_error"),
            "last_delivery_error": job.get("last_delivery_error"),
            "paused_at": job.get("paused_at"),
            "paused_reason": job.get("paused_reason"),
        }
        no_change = after == before
        if not no_change:
            self._write_jobs(raw)
        return {
            "status": "ok",
            "mode": "jobs_json",
            "action": action,
            "no_change": no_change,
            "hermes_job_id": str(job.get("id") or ""),
            "hermes_job_name": str(job.get("name") or ""),
            "before": redact(json.dumps(before, default=str)),
            "after": after,
            "backup_path": self.last_backup_path,
        }

    def _read_jobs(self) -> Any:
        path = self.jobs_file
        if not path.exists():
            raise HermesControlError(f"{path} does not exist")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HermesControlError(f"Could not read Hermes jobs file: {exc}") from exc

    def _write_jobs(self, raw: Any) -> None:
        path = self.jobs_file
        try:
            lock_path = path.with_name(f".{path.name}.lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_handle = lock_path.open("w")
            try:
                import fcntl
                fcntl.flock(lock_handle, fcntl.LOCK_EX)
            except Exception:
                pass
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = path.with_name(f"{path.name}.bak-dashboard-{stamp}")
            if path.exists():
                shutil.copy2(path, backup_path)
                self.last_backup_path = str(backup_path)
            temp_path = path.with_name(f".{path.name}.tmp")
            temp_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
            temp_path.replace(path)
            try:
                import fcntl
                fcntl.flock(lock_handle, fcntl.LOCK_UN)
            except Exception:
                pass
            lock_handle.close()
        except Exception as exc:
            raise HermesControlError(f"Could not update Hermes jobs file: {exc}") from exc

    def _validate_cron(self, cron: str) -> None:
        value = (cron or "").strip()
        if value in {"manual", "@hourly", "@daily", "@weekly"}:
            return
        fields = value.split()
        if len(fields) != 5:
            raise HermesControlError("Cron must be 5 fields or one of manual/@hourly/@daily/@weekly")
        token_pattern = re.compile(r"^[0-9*/,\-]+$")
        for field in fields:
            if not token_pattern.match(field):
                raise HermesControlError(f"Invalid cron field: {field}")

    def _find_job(self, raw: Any, hermes_job_id: str) -> dict[str, Any] | None:
        for job in self._jobs(raw):
            if str(job.get("id")) == hermes_job_id:
                return job
        return None

    def _jobs(self, raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            for key in ("jobs", "items"):
                if isinstance(raw.get(key), list):
                    return [item for item in raw[key] if isinstance(item, dict)]
            return [raw]
        return []

    def _append_job(self, raw: Any, job: dict[str, Any]) -> None:
        if isinstance(raw, list):
            raw.append(job)
            return
        if isinstance(raw, dict):
            for key in ("jobs", "items"):
                if isinstance(raw.get(key), list):
                    raw[key].append(job)
                    return
            raise HermesControlError("Hermes jobs file is an object without a jobs/items list; refusing to append a processor job")
        raise HermesControlError("Unsupported Hermes jobs file format")

    def _apply(self, job: dict[str, Any], action: str) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if action == "pause":
            already_paused = job.get("enabled") is False and job.get("state") == "paused" and job.get("next_run_at") is None
            if already_paused:
                return
            job["enabled"] = False
            job["state"] = "paused"
            job["next_run_at"] = None
            job["paused_at"] = now
            job["paused_reason"] = "Paused from Voryx dashboard"
            return

        already_scheduled = job.get("enabled") is True and job.get("state") == "scheduled"
        job["enabled"] = True
        job["state"] = "scheduled"
        if action == "run":
            job["next_run_at"] = now
        elif not already_scheduled and not job.get("next_run_at"):
            job["next_run_at"] = now

        if job.get("last_status") == "error":
            job["last_status"] = None
        for key in ("last_error", "last_delivery_error", "paused_at", "paused_reason"):
            if key in job:
                job[key] = None
