import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.hermes_live import redact


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
        if after == before:
            raise HermesControlError(f"Hermes schedule update produced no state change for {hermes_job_id}")
        self._write_jobs(raw)
        return {
            "status": "ok",
            "mode": "jobs_json",
            "action": "update_schedule",
            "hermes_job_id": hermes_job_id,
            "hermes_job_name": str(job.get("name") or ""),
            "before": redact(json.dumps(before, default=str)),
            "after": after,
            "backup_path": self.last_backup_path,
        }

    def _control_job(self, raw: Any, job: dict[str, Any], action: str) -> dict[str, Any]:
        before = {
            "enabled": job.get("enabled"),
            "state": job.get("state"),
            "next_run_at": job.get("next_run_at"),
            "last_status": job.get("last_status"),
            "last_error": job.get("last_error"),
            "last_delivery_error": job.get("last_delivery_error"),
        }
        self._apply(job, action)
        after = {
            "enabled": job.get("enabled"),
            "state": job.get("state"),
            "next_run_at": job.get("next_run_at"),
            "last_status": job.get("last_status"),
            "last_error": job.get("last_error"),
            "last_delivery_error": job.get("last_delivery_error"),
        }
        if after == before:
            raise HermesControlError(f"Hermes action produced no state change: {action} for {job.get('id') or job.get('name')}")
        self._write_jobs(raw)
        return {
            "status": "ok",
            "mode": "jobs_json",
            "action": action,
            "hermes_job_id": str(job.get("id") or ""),
            "hermes_job_name": str(job.get("name") or ""),
            "before": redact(json.dumps(before, default=str)),
            "after": after,
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

    def _apply(self, job: dict[str, Any], action: str) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if action == "pause":
            job["enabled"] = False
            job["state"] = "paused"
            job["next_run_at"] = None
            return

        job["enabled"] = True
        job["state"] = "scheduled"
        if action == "run":
            job["next_run_at"] = now
        elif not job.get("next_run_at"):
            job["next_run_at"] = now

        if job.get("last_status") == "error":
            job["last_status"] = None
        for key in ("last_error", "last_delivery_error"):
            if key in job:
                job[key] = None
