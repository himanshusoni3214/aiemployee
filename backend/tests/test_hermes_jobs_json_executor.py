import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from app.services import hermes_jobs_json_executor as executor
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT


class HermesJobsJsonExecutorTests(unittest.TestCase):
    def test_requires_known_hermes_job_id(self):
        result = executor.execute_scheduled_jobs_json_task("Generate Leads", {})

        self.assertEqual(result["status"], "unsupported")
        self.assertIn("requires payload.hermes_job_id", result["error"])

    def test_blocks_unsafe_outreach_script_payload(self):
        result = executor.execute_scheduled_jobs_json_task(
            "Generate Leads",
            {"hermes_job_id": executor.LEAD_RESEARCH_JOB_ID, "command": "python3 send_outreach.py"},
        )

        self.assertEqual(result["status"], "failed")
        self.assertIn("send_outreach.py", result["error"])

    def test_blocks_outreach_jobs_from_scheduled_execution(self):
        for hermes_job_id in (executor.OUTREACH_DRAFT_JOB_ID, executor.OUTREACH_FOLLOWUP_JOB_ID):
            result = executor.execute_scheduled_jobs_json_task("Send Outreach", {"hermes_job_id": hermes_job_id})
            self.assertEqual(result["status"], "unsupported")
            self.assertIn("not executable", result["error"])

    def test_daily_report_rejects_non_internal_recipient(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(executor, "DATA_ROOT", Path(tmp)):
            result = executor.execute_scheduled_jobs_json_task(
                "Daily Report",
                {"hermes_job_id": executor.DAILY_REPORT_JOB_ID, "recipient": "prospect@example.org"},
            )

        self.assertEqual(result["status"], "failed")
        self.assertIn(INTERNAL_REPORT_RECIPIENT, result["logs"][-1])

    def test_daily_report_requires_delivery_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leads = root / "home" / "leads"
            queue = root / "home" / "voryx_mail_queue"
            leads.mkdir(parents=True)
            queue.mkdir(parents=True)
            (leads / "generate_daily_report.py").write_text("print('ok')\n", encoding="utf-8")

            with patch.object(executor, "DATA_ROOT", root), \
                patch.object(executor, "LEADS_DIR", leads), \
                patch.object(executor, "MAIL_QUEUE_DIR", queue), \
                patch.object(executor, "CRON_OUTPUT_DIR", root / "cron" / "output"), \
                patch.object(executor, "_run") as run, \
                patch.object(executor, "_process_one_internal_mail") as process:
                run.return_value.returncode = 0
                run.return_value.stdout = "REPORT_WRITTEN path=/tmp/report.txt\n"
                run.return_value.stderr = ""
                (leads / "brew_daily_report.txt").write_text("report\n", encoding="utf-8")
                process.return_value.returncode = 0
                process.return_value.stdout = "EMAIL_SENT recipient=himanshusoni3214@gmail.com\n"
                process.return_value.stderr = ""

                result = executor.execute_scheduled_jobs_json_task(
                    "Daily Report",
                    {"hermes_job_id": executor.DAILY_REPORT_JOB_ID, "recipient": INTERNAL_REPORT_RECIPIENT},
                )

            self.assertEqual(result["status"], "failed")
            self.assertIn("missing provider message evidence", result["error"])

    def test_records_approved_job_execution_in_jobs_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cron = root / "cron"
            cron.mkdir()
            jobs_path = cron / "jobs.json"
            jobs_path.write_text(
                json.dumps({"jobs": [{"id": executor.LEAD_RESEARCH_JOB_ID, "enabled": True, "state": "scheduled"}]}),
                encoding="utf-8",
            )

            with patch.object(executor, "DATA_ROOT", root):
                executor._record_jobs_json_execution(
                    executor.LEAD_RESEARCH_JOB_ID,
                    {"status": "ok", "results": {"hermes_output_path": "/opt/data/cron/output/lead.md"}},
                )

            raw = json.loads(jobs_path.read_text(encoding="utf-8"))
            job = raw["jobs"][0]
            self.assertEqual(job["last_status"], "ok")
            self.assertEqual(job["last_output_path"], "/opt/data/cron/output/lead.md")
            self.assertEqual(job["state"], "scheduled")
            self.assertIsNotNone(job["last_run_at"])


if __name__ == "__main__":
    unittest.main()
