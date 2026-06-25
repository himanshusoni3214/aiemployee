import csv
import json
from pathlib import Path
import re
from typing import Any

from app.core.config import settings

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
OPENAI_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{10,}")
OPENROUTER_KEY_PATH_RE = re.compile(r"keys/[A-Za-z0-9]+")


def redact(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = OPENAI_KEY_RE.sub("[REDACTED_OPENAI_KEY]", value)
    return OPENROUTER_KEY_PATH_RE.sub("keys/[REDACTED]", value)


def _jobs_from_raw(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("jobs", "items"):
            if isinstance(raw.get(key), list):
                return [item for item in raw[key] if isinstance(item, dict)]
        return [raw]
    return []


class HermesLiveMonitor:
    def __init__(self, data_path: str | None = None):
        self.data_path = Path(data_path or settings.hermes_data_path) if (data_path or settings.hermes_data_path) else None

    def _missing(self) -> dict[str, Any] | None:
        if not self.data_path:
            return {"status": "unavailable", "reason": "HERMES_DATA_PATH is not configured"}
        if not self.data_path.exists():
            return {"status": "unavailable", "reason": f"{self.data_path} does not exist"}
        return None

    def jobs(self) -> list[dict[str, Any]]:
        if not self.data_path:
            return []
        jobs_file = self.data_path / "cron" / "jobs.json"
        if not jobs_file.exists():
            return []
        raw = json.loads(jobs_file.read_text())
        jobs = []
        for job in _jobs_from_raw(raw):
            schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
            repeat = job.get("repeat") if isinstance(job.get("repeat"), dict) else {}
            last_error = redact(job.get("last_error"))
            jobs.append({
                "id": job.get("id"),
                "name": job.get("name") or job.get("title") or job.get("id"),
                "skill": job.get("skill"),
                "created_at": job.get("created_at"),
                "deliver": redact(job.get("deliver")),
                "enabled_toolsets": job.get("enabled_toolsets") if isinstance(job.get("enabled_toolsets"), list) else [],
                "prompt_excerpt": redact((job.get("prompt") or job.get("instructions") or "")[:500]),
                "schedule_display": job.get("schedule_display") or schedule.get("display"),
                "schedule": schedule,
                "enabled": job.get("enabled"),
                "state": job.get("state"),
                "next_run_at": job.get("next_run_at"),
                "last_run_at": job.get("last_run_at"),
                "last_status": job.get("last_status"),
                "last_error": last_error,
                "last_delivery_error": redact(job.get("last_delivery_error")),
                "repeat": repeat,
            })
        return jobs

    def outreach(self) -> dict[str, Any]:
        if not self.data_path:
            return {"row_count": 0, "recent": []}
        log_file = self.data_path / "outreach_log.csv"
        if not log_file.exists():
            return {"row_count": 0, "recent": [], "missing": True}
        rows = []
        with log_file.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                rows.append({
                    "recipient": redact(row.get("recipient") or row.get("email") or ""),
                    "status": row.get("status"),
                    "timestamp": row.get("timestamp"),
                    "note": redact(row.get("note") or row.get("business") or ""),
                })
        return {"row_count": len(rows), "recent": rows[-10:]}

    def outputs(self) -> dict[str, Any]:
        if not self.data_path:
            return {"output_count": 0, "recent": []}
        output_dir = self.data_path / "cron" / "output"
        if not output_dir.exists():
            return {"output_count": 0, "recent": []}
        files = sorted([p for p in output_dir.glob("*/*.md") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            "output_count": len(files),
            "recent": [{
                "job_id": p.parent.name,
                "path": str(p.relative_to(self.data_path)),
                "size_bytes": p.stat().st_size,
                "modified_at": p.stat().st_mtime,
            } for p in files[:10]],
        }

    def summary(self) -> dict[str, Any]:
        missing = self._missing()
        if missing:
            return missing
        try:
            jobs = self.jobs()
            outreach = self.outreach()
            outputs = self.outputs()
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

        enabled_jobs = [job for job in jobs if job.get("enabled")]
        failing_jobs = [job for job in jobs if job.get("last_status") == "error"]
        key_limit_failures = [
            job for job in failing_jobs
            if "key limit exceeded" in (job.get("last_error") or "").lower()
        ]
        status = "ok"
        if failing_jobs or outreach.get("missing"):
            status = "degraded"

        return {
            "status": status,
            "data_path": str(self.data_path),
            "job_count": len(jobs),
            "enabled_job_count": len(enabled_jobs),
            "failing_job_count": len(failing_jobs),
            "key_limit_failure_count": len(key_limit_failures),
            "jobs": jobs,
            "outreach": outreach,
            "outputs": outputs,
        }
