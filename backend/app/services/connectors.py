from abc import ABC, abstractmethod
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx
from app.core.config import settings
from app.services.hermes_jobs_json_executor import execute_scheduled_jobs_json_task


class WorkerConnector(ABC):
    @abstractmethod
    async def execute(self, task_type: str, payload: dict) -> dict: ...

    async def health(self) -> dict:
        return {"status": "unknown"}


class HermesConnector(WorkerConnector):
    @property
    def configured_mode(self) -> str:
        return (settings.hermes_connector_mode or "auto").strip().lower() or "auto"

    @property
    def mode(self) -> str:
        configured = self.configured_mode
        if configured in {"jobs_json", "json", "file", "file_backed"}:
            return "jobs_json"
        if configured == "http":
            return "http"
        jobs_file = self.jobs_file
        if jobs_file and jobs_file.exists():
            return "jobs_json"
        return "http"

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

    def _base_url_is_ttyd(self) -> bool:
        try:
            parsed = urlparse(settings.hermes_base_url)
        except Exception:
            return False
        return parsed.port == 4860

    def _unsupported(self, message: str) -> dict:
        return {
            "status": "unsupported",
            "mode": self.mode,
            "logs": [message],
            "results": {},
            "error": message,
        }

    def capabilities(self) -> dict:
        mode = self.mode
        supports_run = mode == "http" and not self._base_url_is_ttyd()
        return {
            "connector": "hermes",
            "connector_mode": mode,
            "supports_pause_resume": mode == "jobs_json" or supports_run,
            "supports_manual_run": supports_run,
            "supports_dry_run": supports_run,
            "manual_run_message": None if supports_run else "Manual run unavailable in jobs_json mode",
            "model_policy_supported": True,
            "default_provider": "openrouter",
            "default_model": "nvidia/nemotron-3-super-120b-a12b",
            "silent_fallback_enabled": False,
        }

    async def execute(self, task_type: str, payload: dict) -> dict:
        if self.mode == "jobs_json":
            return execute_scheduled_jobs_json_task(task_type, payload)
        if self._base_url_is_ttyd():
            return self._unsupported(
                "HERMES_BASE_URL points to ttyd on port 4860, which is the Hermes web terminal, not a jobs API. "
                "No Hermes HTTP request was made."
            )
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                res = await client.post(self.jobs_url, json={"task_type": task_type, "payload": payload})
                res.raise_for_status()
                return res.json()
            except Exception as exc:
                return {"status": "failed", "logs": [f"Hermes unavailable: {exc}"], "results": {}}

    async def health(self) -> dict:
        if self.mode == "jobs_json":
            jobs_file = self.jobs_file
            if not jobs_file:
                return {
                    "status": "error",
                    "mode": "jobs_json",
                    "base_url": settings.hermes_base_url,
                    "jobs_api": "disabled",
                    "error": "HERMES_DATA_PATH is required for jobs_json mode",
                }
            if not jobs_file.exists():
                return {
                    "status": "error",
                    "mode": "jobs_json",
                    "base_url": settings.hermes_base_url,
                    "jobs_api": "disabled",
                    "jobs_file": str(jobs_file),
                    "error": "Hermes jobs.json is not mounted",
                }
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
                    "jobs_api": "disabled",
                    "jobs_file": str(jobs_file),
                    "job_count": len(jobs),
                    "enabled_job_count": len(enabled),
                    **self.capabilities(),
                    "message": "Hermes state/control uses mounted jobs.json; ttyd is not used as a Jobs API.",
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "mode": "jobs_json",
                    "base_url": settings.hermes_base_url,
                    "jobs_api": "disabled",
                    "jobs_file": str(jobs_file),
                    "error": str(exc),
                }

        if self._base_url_is_ttyd():
            return {
                "status": "misconfigured",
                "mode": "http",
                "base_url": settings.hermes_base_url,
                "jobs_api": "disabled",
                **self.capabilities(),
                "error": "HERMES_BASE_URL points to ttyd on port 4860, not a Hermes Jobs API.",
            }
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                res = await client.get(self.jobs_url)
                res.raise_for_status()
                return {"status": "ok", "mode": "http", "jobs_url": self.jobs_url, **self.capabilities()}
            except Exception as exc:
                return {"status": "unreachable", "mode": "http", "jobs_url": self.jobs_url, "error": str(exc)}


def get_connector(name: str) -> WorkerConnector:
    if name == 'hermes':
        return HermesConnector()
    raise ValueError(f'Unknown connector {name}')
