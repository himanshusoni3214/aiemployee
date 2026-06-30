from abc import ABC, abstractmethod
import json
from pathlib import Path

import httpx
from app.core.config import settings

class WorkerConnector(ABC):
    @abstractmethod
    async def execute(self, task_type: str, payload: dict) -> dict: ...
    async def health(self) -> dict: return {"status": "unknown"}

class HermesConnector(WorkerConnector):
    @property
    def jobs_url(self) -> str:
        jobs_path = settings.hermes_jobs_path if settings.hermes_jobs_path.startswith("/") else f"/{settings.hermes_jobs_path}"
        return f"{settings.hermes_base_url.rstrip('/')}{jobs_path}"

    @property
    def jobs_file(self) -> Path | None:
        data_path = (settings.hermes_data_path or "").strip()
        if not data_path:
            return None
        return Path(data_path) / "cron" / "jobs.json"

    async def execute(self, task_type: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                res = await client.post(self.jobs_url, json={"task_type": task_type, "payload": payload})
                res.raise_for_status()
                return res.json()
            except Exception as exc:
                return {"status": "failed", "logs": [f"Hermes unavailable: {exc}"], "results": {}}

    async def health(self) -> dict:
        jobs_file = self.jobs_file
        if jobs_file and jobs_file.exists():
            try:
                raw = json.loads(jobs_file.read_text(encoding="utf-8"))
                jobs = raw.get("jobs", raw if isinstance(raw, list) else [])
                if not isinstance(jobs, list):
                    jobs = []
                enabled = [job for job in jobs if isinstance(job, dict) and job.get("enabled")]
                return {
                    "status": "ok",
                    "mode": "jobs_json",
                    "base_url": settings.hermes_base_url,
                    "jobs_url": self.jobs_url,
                    "jobs_file": str(jobs_file),
                    "job_count": len(jobs),
                    "enabled_job_count": len(enabled),
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "mode": "jobs_json",
                    "base_url": settings.hermes_base_url,
                    "jobs_url": self.jobs_url,
                    "jobs_file": str(jobs_file),
                    "error": str(exc),
                }

        last_error = "Hermes jobs.json is not mounted; HTTP API probe skipped because this deployment uses the web terminal on HERMES_BASE_URL."
        return {"status": "unreachable", "mode": "unknown", "base_url": settings.hermes_base_url, "jobs_url": self.jobs_url, "error": last_error}

def get_connector(name: str) -> WorkerConnector:
    if name == 'hermes': return HermesConnector()
    raise ValueError(f'Unknown connector {name}')
