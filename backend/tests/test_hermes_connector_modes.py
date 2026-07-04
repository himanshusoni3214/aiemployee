import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.core.config import settings
from app.services import connectors
from app.services.connectors import HermesConnector


class _ExplodingClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("HTTP client must not be constructed")


class HermesConnectorModeTests(unittest.TestCase):
    def setUp(self):
        self.original_mode = settings.hermes_connector_mode
        self.original_base_url = settings.hermes_base_url
        self.original_jobs_path = settings.hermes_jobs_path
        self.original_data_path = settings.hermes_data_path
        self.original_client = connectors.httpx.AsyncClient
        self.original_jobs_json_executor = connectors.execute_scheduled_jobs_json_task

    def tearDown(self):
        settings.hermes_connector_mode = self.original_mode
        settings.hermes_base_url = self.original_base_url
        settings.hermes_jobs_path = self.original_jobs_path
        settings.hermes_data_path = self.original_data_path
        connectors.httpx.AsyncClient = self.original_client
        connectors.execute_scheduled_jobs_json_task = self.original_jobs_json_executor

    def write_jobs(self, root):
        cron = Path(root) / "cron"
        cron.mkdir(parents=True)
        (cron / "jobs.json").write_text(json.dumps({"jobs": [{"id": "lead", "enabled": True}]}), encoding="utf-8")

    def test_jobs_json_execute_does_not_call_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_jobs(tmp)
            settings.hermes_connector_mode = "jobs_json"
            settings.hermes_base_url = "http://hermes-agent:4860"
            settings.hermes_data_path = tmp
            connectors.httpx.AsyncClient = _ExplodingClient

            result = asyncio.run(HermesConnector().execute("qa", {}))

            self.assertEqual(result["status"], "unsupported")
            self.assertEqual(result["mode"], "jobs_json")
            self.assertIn("requires payload.hermes_job_id", result["error"])

    def test_jobs_json_execute_delegates_safe_scheduled_jobs_without_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_jobs(tmp)
            settings.hermes_connector_mode = "jobs_json"
            settings.hermes_base_url = "http://hermes-agent:4860"
            settings.hermes_data_path = tmp
            connectors.httpx.AsyncClient = _ExplodingClient
            calls = []

            def fake_executor(task_type, payload):
                calls.append((task_type, payload))
                return {"status": "ok", "logs": ["safe executor"], "results": {"output_path": "/opt/data/out.csv"}}

            connectors.execute_scheduled_jobs_json_task = fake_executor

            result = asyncio.run(HermesConnector().execute("Generate Leads", {"hermes_job_id": "0d0c20e25f55"}))

            self.assertEqual(result["status"], "ok")
            self.assertEqual(calls, [("Generate Leads", {"hermes_job_id": "0d0c20e25f55"})])

    def test_ttyd_base_url_is_not_treated_as_http_jobs_api(self):
        settings.hermes_connector_mode = "http"
        settings.hermes_base_url = "http://hermes-agent:4860"
        settings.hermes_jobs_path = "/jobs"
        settings.hermes_data_path = ""
        connectors.httpx.AsyncClient = _ExplodingClient

        result = asyncio.run(HermesConnector().execute("qa", {}))
        health = asyncio.run(HermesConnector().health())

        self.assertEqual(result["status"], "unsupported")
        self.assertIn("ttyd", result["error"])
        self.assertEqual(health["status"], "misconfigured")
        self.assertEqual(health["jobs_api"], "disabled")

    def test_jobs_json_health_reports_file_mode_without_jobs_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_jobs(tmp)
            settings.hermes_connector_mode = "jobs_json"
            settings.hermes_base_url = "http://hermes-agent:4860"
            settings.hermes_data_path = tmp

            health = asyncio.run(HermesConnector().health())

            self.assertEqual(health["status"], "ok")
            self.assertEqual(health["mode"], "jobs_json")
            self.assertEqual(health["jobs_api"], "disabled")
            self.assertNotIn("jobs_url", health)

    def test_jobs_json_capabilities_disable_manual_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.write_jobs(tmp)
            settings.hermes_connector_mode = "jobs_json"
            settings.hermes_base_url = "http://hermes-agent:4860"
            settings.hermes_data_path = tmp

            capabilities = HermesConnector().capabilities()
            health = asyncio.run(HermesConnector().health())

            self.assertEqual(capabilities["connector_mode"], "jobs_json")
            self.assertTrue(capabilities["supports_pause_resume"])
            self.assertFalse(capabilities["supports_manual_run"])
            self.assertFalse(capabilities["supports_dry_run"])
            self.assertEqual(health["connector_mode"], "jobs_json")
            self.assertFalse(health["supports_manual_run"])


if __name__ == "__main__":
    unittest.main()
