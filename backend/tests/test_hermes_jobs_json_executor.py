import tempfile
import unittest
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from app.services import hermes_jobs_json_executor as executor
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT


class HermesJobsJsonExecutorTests(unittest.TestCase):
    def setUp(self):
        self.model_guard = patch.object(executor, "_model_policy_guard", return_value={"allowed": True, "decision": {"status": "allowed"}, "policy": {}})
        self.model_guard.start()

    def tearDown(self):
        self.model_guard.stop()

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

    def test_generic_template_lead_research_executes_without_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "home" / "leads" / "voryx_generic_lead_research.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('ok')\n", encoding="utf-8")
            output_dir = root / "home" / "voryx_workspaces" / "company" / "campaign" / "leads"
            output_dir.mkdir(parents=True)
            output = output_dir / "leads_company_campaign_20260704T000000Z.csv"
            output.write_text("business,industry,location,target_customer,exclusions,email_sending\nA,cafes,Toronto,independent,chains,false\n", encoding="utf-8")
            cron = root / "cron"
            cron.mkdir()
            jobs_path = cron / "jobs.json"
            jobs_path.write_text(
                json.dumps({
                    "jobs": [{
                        "id": "voryx-template-generic",
                        "source": "voryx_template",
                        "enabled": True,
                        "state": "scheduled",
                        "task_type": "Generate Leads",
                        "command": "python3 /opt/data/home/leads/voryx_generic_lead_research.py --company-id company --campaign-id campaign --industry cafes --location Toronto --target-customer independent --exclude chains --limit 1 --output-dir /opt/data/home/voryx_workspaces/company/campaign/leads --no-email",
                        "working_directory": "/opt/data/home/voryx_workspaces/company/campaign",
                        "safety": {"email_sending": False, "prospect_outreach": False},
                    }]
                }),
                encoding="utf-8",
            )

            def fake_run(args, *, cwd):
                self.assertIn("--no-email", args)
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"GENERIC_LEAD_RESEARCH_OUTPUT path={output}\nLEADS_GENERATED=1\n", stderr="")

            with patch.object(executor, "DATA_ROOT", root), \
                patch.object(executor, "CRON_OUTPUT_DIR", root / "cron" / "output"), \
                patch.object(executor, "_run", side_effect=fake_run):
                result = executor.execute_scheduled_jobs_json_task("Generate Leads", {"hermes_job_id": "voryx-template-generic"})

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["results"]["lead_count"], 1)
            self.assertFalse(result["results"]["email_sending"])
            updated = json.loads(jobs_path.read_text(encoding="utf-8"))["jobs"][0]
            self.assertEqual(updated["last_status"], "ok")


    def test_bibs_lead_research_requires_internet_provider_or_upload_source(self):
        from app.services import hermes_jobs_json_executor as executor
        with unittest.mock.patch.object(executor, "LEADS_DIR") as leads_dir:
            import tempfile
            from pathlib import Path
            with tempfile.TemporaryDirectory() as tmp:
                leads_dir.__truediv__.side_effect = lambda name: Path(tmp) / name
                leads_dir.glob.side_effect = lambda pattern: []
                result = executor.execute_scheduled_jobs_json_task("Generate Leads", {"hermes_job_id": executor.LEAD_RESEARCH_JOB_ID})
        self.assertEqual(result["status"], "failed")
        self.assertIn("internet_research_provider_not_configured", result.get("error", ""))
        self.assertEqual(result.get("results", {}).get("error_code"), "internet_research_provider_not_configured")

    def test_bibs_ai_internet_research_routes_to_native_browser_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leads = root / "home" / "leads"
            leads.mkdir(parents=True)
            script = leads / "hermes_native_browser_research.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            config = leads / "bibs_real_lead_source_config.json"
            config.write_text(json.dumps({"source_type": "ai_internet_research", "lead_limit": 10}), encoding="utf-8")
            output = leads / "leads_brew_it_browser_20260715T000000Z.csv"
            rows = ["Business Name,Website,Source URL,Evidence URL"]
            for index in range(10):
                rows.append(f"Browser Cafe {index},https://browser-cafe-{index}.example,https://browser-cafe-{index}.example/contact,https://browser-cafe-{index}.example/contact")
            output.write_text("\n".join(rows) + "\n", encoding="utf-8")

            def fake_run(args, *, cwd):
                self.assertIn("hermes_native_browser_research.py", args[1])
                self.assertIn("--no-email", args)
                self.assertIn("--min-success", args)
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"HERMES_NATIVE_BROWSER_OUTPUT path={output}\nNEW_UNIQUE_BUSINESSES=10\nPROSPECT_EMAILS_SENT=0\n", stderr="")

            with patch.object(executor, "DATA_ROOT", root), \
                patch.object(executor, "LEADS_DIR", leads), \
                patch.object(executor, "CRON_OUTPUT_DIR", root / "cron" / "output"), \
                patch.object(executor, "_run", side_effect=fake_run):
                result = executor.execute_scheduled_jobs_json_task("Generate Leads", {"hermes_job_id": executor.LEAD_RESEARCH_JOB_ID})

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["results"]["provider"], "hermes_native_browser")
            self.assertEqual(result["results"]["new_unique_businesses"], 10)
            self.assertEqual(result["results"]["prospect_emails_sent"], 0)

    def test_bibs_native_browser_accepts_low_positive_new_unique_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            leads = root / "home" / "leads"
            leads.mkdir(parents=True)
            script = leads / "hermes_native_browser_research.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            config = leads / "bibs_real_lead_source_config.json"
            config.write_text(json.dumps({"source_type": "ai_internet_research", "lead_limit": 25}), encoding="utf-8")
            output = leads / "leads_brew_it_browser_20260717T000000Z.csv"
            rows = ["Business Name,Website,Source URL,Evidence URL"]
            for index in range(3):
                rows.append(f"New Cafe {index},https://new-cafe-{index}.example,https://new-cafe-{index}.example/contact,https://new-cafe-{index}.example/contact")
            output.write_text("\n".join(rows) + "\n", encoding="utf-8")

            def fake_run(args, *, cwd):
                min_index = args.index("--min-success")
                self.assertEqual(args[min_index + 1], "1")
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=f"HERMES_NATIVE_BROWSER_OUTPUT path={output}\nNEW_UNIQUE_BUSINESSES=3\nPROSPECT_EMAILS_SENT=0\n", stderr="")

            with patch.object(executor, "DATA_ROOT", root), \
                patch.object(executor, "LEADS_DIR", leads), \
                patch.object(executor, "CRON_OUTPUT_DIR", root / "cron" / "output"), \
                patch.object(executor, "_run", side_effect=fake_run):
                result = executor.execute_scheduled_jobs_json_task("Generate Leads", {"hermes_job_id": executor.LEAD_RESEARCH_JOB_ID})

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["results"]["new_unique_businesses"], 3)
            self.assertIn("LOW_NEW_UNIQUE_BUSINESSES=3", "\n".join(result["logs"]))
            self.assertEqual(result["results"]["prospect_emails_sent"], 0)

if __name__ == "__main__":
    unittest.main()
