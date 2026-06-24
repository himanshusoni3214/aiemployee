from abc import ABC, abstractmethod
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

    async def execute(self, task_type: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                res = await client.post(self.jobs_url, json={"task_type": task_type, "payload": payload})
                res.raise_for_status()
                return res.json()
            except Exception as exc:
                return {"status": "failed", "logs": [f"Hermes unavailable: {exc}"], "results": {}}

    async def health(self) -> dict:
        async with httpx.AsyncClient(timeout=5) as client:
            for path in ("/health", "/"):
                try:
                    res = await client.get(f"{settings.hermes_base_url.rstrip('/')}{path}")
                    return {
                        "status": "ok" if res.status_code < 500 else "error",
                        "status_code": res.status_code,
                        "base_url": settings.hermes_base_url,
                        "jobs_url": self.jobs_url,
                    }
                except Exception as exc:
                    last_error = str(exc)
            return {"status": "unreachable", "base_url": settings.hermes_base_url, "jobs_url": self.jobs_url, "error": last_error}

def get_connector(name: str) -> WorkerConnector:
    if name == 'hermes': return HermesConnector()
    raise ValueError(f'Unknown connector {name}')
