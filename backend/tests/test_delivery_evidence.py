import asyncio
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.entities import AIEmployee, Company, EmployeeStatus, Job, JobStatus
from app.services.daily_report import generate_daily_report
from app.services.hermes_import import HermesImportService
from app.services.internal_mail_queue import enqueue_daily_report_delivery, ingest_internal_mail_receipts
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT, validate_report_recipient
from app.workers import job_runner


class FakeMonitor:
    def __init__(self, jobs):
        self.jobs = jobs

    def summary(self):
        return {"status": "ok", "jobs": self.jobs}


class Connector:
    def __init__(self, result):
        self.result = result

    async def execute(self, task_type, payload):
        return self.result


class DeliveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_session = job_runner.SessionLocal
        self.original_connector = job_runner.get_connector
        job_runner.SessionLocal = self.Session

    def tearDown(self):
        job_runner.SessionLocal = self.original_session
        job_runner.get_connector = self.original_connector
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create_delivery_job(self, task_type="Send Outreach", payload=None):
        db = self.Session()
        try:
            company = Company(id="company-brew-it-by-sash", name="Brew It By Sash")
            db.add(company)
            db.flush()
            employee = AIEmployee(
                company_id=company.id,
                name="Delivery Worker",
                employee_type="Email Outreach",
                status=EmployeeStatus.running,
            )
            db.add(employee)
            db.flush()
            job = Job(employee_id=employee.id, connector="hermes", task_type=task_type, payload=payload or {}, status=JobStatus.queued)
            db.add(job)
            db.commit()
            return job.id
        finally:
            db.close()

    def load_job(self, job_id):
        db = self.Session()
        try:
            return db.get(Job, job_id)
        finally:
            db.close()

    def test_last_status_ok_without_provider_message_id_imports_as_synced(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = HermesImportService(tmp)
            service.monitor = FakeMonitor([{
                "id": "brew-report",
                "name": "Hermes End Day Report",
                "enabled": True,
                "state": "idle",
                "last_status": "ok",
                "last_run_at": "2026-06-27T22:00:00Z",
            }])
            db = self.Session()
            try:
                service.sync(db)
                job = db.get(Job, "hermes-schedule-brew-report")
                self.assertEqual(job.status, JobStatus.synced)
                self.assertEqual(job.delivery_status, "synced")
                self.assertEqual(job.evidence_type, "hermes_schedule_state")
                self.assertIsNone(job.provider_message_id)
            finally:
                db.close()

    def test_imported_output_without_delivery_evidence_is_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "cron" / "output" / "brew-report"
            output.mkdir(parents=True)
            (output / "2026-06-27_22-00-00.md").write_text("REPORT_WRITTEN path=/tmp/report.txt\n", encoding="utf-8")
            service = HermesImportService(tmp)
            service.monitor = FakeMonitor([{"id": "brew-report", "name": "Hermes End Day Report", "enabled": True, "state": "idle"}])
            db = self.Session()
            try:
                service.sync(db)
                imported = db.scalar(select(Job).where(Job.evidence_type == "output_file"))
                self.assertEqual(imported.status, JobStatus.imported)
                self.assertEqual(imported.delivery_status, "imported")
                self.assertIn("not delivery evidence", imported.verification_reason)
            finally:
                db.close()

    def test_duplicate_hermes_imports_produce_one_output_execution_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "cron" / "output" / "brew-report"
            output.mkdir(parents=True)
            (output / "2026-06-27_22-00-00.md").write_text("REPORT_WRITTEN path=/tmp/report.txt\n", encoding="utf-8")
            service = HermesImportService(tmp)
            service.monitor = FakeMonitor([{"id": "brew-report", "name": "Hermes End Day Report", "enabled": True, "state": "idle"}])
            db = self.Session()
            try:
                service.sync(db)
                service.sync(db)
                count = db.scalar(select(func.count()).select_from(select(Job.id).where(Job.evidence_type == "output_file").subquery()))
                self.assertEqual(count, 1)
            finally:
                db.close()

    def test_dry_run_with_zero_sends_is_skipped(self):
        job_runner.get_connector = lambda connector: Connector({"status": "ok", "results": {"dry_run": True, "sent_count": 0}})
        job_id = self.create_delivery_job(payload={"dry_run": True})

        self.assertTrue(asyncio.run(job_runner.run_once()))
        job = self.load_job(job_id)
        self.assertEqual(job.status, JobStatus.skipped)
        self.assertEqual(job.delivery_status, "dry_run")

    def test_zero_verified_recipients_is_skipped(self):
        job_runner.get_connector = lambda connector: Connector({"status": "ok", "results": {"eligible_count": 0, "sent_count": 0}})
        job_id = self.create_delivery_job()

        self.assertTrue(asyncio.run(job_runner.run_once()))
        job = self.load_job(job_id)
        self.assertEqual(job.status, JobStatus.skipped)
        self.assertIn("zero eligible", job.verification_reason)

    def test_provider_error_becomes_failed_with_exact_error(self):
        job_runner.get_connector = lambda connector: Connector({"status": "failed", "error": "SMTP 550 exact provider error", "logs": []})
        job_id = self.create_delivery_job()

        self.assertTrue(asyncio.run(job_runner.run_once()))
        job = self.load_job(job_id)
        self.assertEqual(job.status, JobStatus.failed)
        self.assertEqual(job.error_message, "SMTP 550 exact provider error")
        self.assertEqual(job.delivery_status, "failed")
        self.assertEqual(job.evidence_type, "provider_error")
        self.assertEqual(job.verification_reason, "SMTP 550 exact provider error")

    def test_internal_report_with_provider_message_id_completes(self):
        job_runner.get_connector = lambda connector: Connector({
            "status": "ok",
            "results": {
                "recipient": INTERNAL_REPORT_RECIPIENT,
                "provider_message_id": "<report-123@gmail.com>",
                "sent_at": "2026-06-27T23:00:00Z",
            },
        })
        job_id = self.create_delivery_job(task_type="Daily Report")

        self.assertTrue(asyncio.run(job_runner.run_once()))
        job = self.load_job(job_id)
        self.assertEqual(job.status, JobStatus.completed)
        self.assertEqual(job.provider_message_id, "<report-123@gmail.com>")
        self.assertEqual(job.recipient_email, INTERNAL_REPORT_RECIPIENT)

    def test_outreach_with_verified_provider_message_id_completes(self):
        job_runner.get_connector = lambda connector: Connector({
            "status": "ok",
            "results": {
                "sent": [{
                    "status": "sent",
                    "recipient": "owner@example.org",
                    "provider_message_id": "<outreach-123@gmail.com>",
                    "sent_at": "2026-06-27T23:00:00Z",
                }],
            },
        })
        job_id = self.create_delivery_job()

        self.assertTrue(asyncio.run(job_runner.run_once()))
        job = self.load_job(job_id)
        self.assertEqual(job.status, JobStatus.completed)
        self.assertEqual(job.provider_message_id, "<outreach-123@gmail.com>")
        self.assertEqual(job.recipient_email, "owner@example.org")

    def test_report_included_count_cannot_exceed_distinct_examined_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            leads = Path(tmp) / "home" / "leads"
            leads.mkdir(parents=True)
            with (leads / "leads_verified.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Public Email", "created_at"])
                writer.writeheader()
                writer.writerow({"Public Email": "a@example.org", "created_at": "2026-06-27T10:00:00Z"})
                writer.writerow({"Public Email": "a@example.org", "created_at": "2026-06-27T11:00:00Z"})
            with (leads / "leads_extra.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["Public Email", "created_at"])
                writer.writeheader()
                writer.writerow({"Public Email": "b@example.org", "created_at": "2026-06-27T12:00:00Z"})

            report = generate_daily_report("2026-06-27", data_path=tmp)
            for item in report["evidence"]:
                self.assertLessEqual(item["rows_included"], item["rows_examined"])

    def test_prospect_recipient_rejected_in_report_only_acceptance_mode(self):
        with self.assertRaises(ValueError):
            validate_report_recipient("prospect@example.org", report_only_acceptance=True)

    def test_internal_report_queue_writes_atomic_pending_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "home" / "leads" / "brew_daily_report.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("safe internal report\n", encoding="utf-8")
            db = self.Session()
            try:
                job, queued = enqueue_daily_report_delivery(
                    db,
                    recipient=INTERNAL_REPORT_RECIPIENT,
                    subject="QA report",
                    artifact_path=artifact,
                    report_date="2026-06-28",
                    data_path=tmp,
                )
                pending = Path(queued["request_path"])
                self.assertTrue(pending.exists())
                self.assertFalse(list(pending.parent.glob("*.tmp")))
                request = json.loads(pending.read_text(encoding="utf-8"))
                self.assertEqual(request["job_id"], job.id)
                self.assertEqual(request["recipient"], INTERNAL_REPORT_RECIPIENT)
                self.assertEqual(request["artifact_path"], "/opt/data/home/leads/brew_daily_report.txt")
                self.assertEqual(job.status, JobStatus.queued)
            finally:
                db.close()

    def test_internal_report_queue_rejects_outside_artifact_and_prospect(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            artifact = Path(outside) / "report.txt"
            artifact.write_text("outside\n", encoding="utf-8")
            db = self.Session()
            try:
                with self.assertRaises(ValueError):
                    enqueue_daily_report_delivery(
                        db,
                        recipient=INTERNAL_REPORT_RECIPIENT,
                        subject="QA report",
                        artifact_path=artifact,
                        report_date="2026-06-28",
                        data_path=tmp,
                    )
                safe_artifact = Path(tmp) / "home" / "leads" / "report.txt"
                safe_artifact.parent.mkdir(parents=True)
                safe_artifact.write_text("inside\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    enqueue_daily_report_delivery(
                        db,
                        recipient="prospect@example.org",
                        subject="QA report",
                        artifact_path=safe_artifact,
                        report_date="2026-06-28",
                        data_path=tmp,
                    )
            finally:
                db.close()

    def run_processor(self, root: Path, fake_sender: Path | None = None):
        env = {
            **dict(HERMES_DATA_ROOT=str(root), VORYX_PROCESS_ONE_MAIL="1", HIMALAYA_BIN="/no/such/himalaya"),
        }
        if fake_sender:
            env["VORYX_HIMALAYA_SEND_COMMAND"] = f"{sys.executable} {fake_sender} {{message_file}}"
        return subprocess.run(
            [sys.executable, "backend/app/assets/process_internal_mail_queue.py"],
            cwd=Path(__file__).resolve().parents[2],
            env={**__import__("os").environ, **env},
            text=True,
            capture_output=True,
            check=False,
        )

    def write_pending_request(self, root: Path, payload: dict):
        pending = root / "home" / "voryx_mail_queue" / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        path = pending / f"{payload['request_id']}.json"
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path

    def make_processor_request(self, root: Path, **overrides):
        artifact = root / "home" / "leads" / "report.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("safe report body\n", encoding="utf-8")
        payload = {
            "request_id": "req-123",
            "job_id": "job-123",
            "kind": "daily_report",
            "recipient": INTERNAL_REPORT_RECIPIENT,
            "subject": "QA report",
            "artifact_path": "/opt/data/home/leads/report.txt",
            "report_date": "2026-06-28",
            "created_at": "2026-06-28T01:00:00Z",
            "report_only_acceptance": True,
        }
        payload.update(overrides)
        self.write_pending_request(root, payload)
        return payload

    def fake_sender_script(self, root: Path, exit_code: int = 0):
        calls = root / "calls.txt"
        script = root / "fake_sender.py"
        script.write_text(
            "import pathlib, sys\n"
            f"calls = pathlib.Path({str(calls)!r})\n"
            "if sys.argv[-1].endswith('.eml'):\n"
            "    calls.write_text((calls.read_text() if calls.exists() else '') + sys.argv[-1] + '\\n')\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8",
        )
        return script, calls

    def test_processor_rejects_prospect_cc_bcc_and_non_report_requests(self):
        cases = [
            {"recipient": "prospect@example.org"},
            {"cc": "other@example.org"},
            {"bcc": "other@example.org"},
            {"kind": "outreach"},
        ]
        for index, override in enumerate(cases):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.make_processor_request(root, request_id=f"reject-{index}", **override)
                result = self.run_processor(root)
                self.assertEqual(result.returncode, 0)
                failed = json.loads((root / "home" / "voryx_mail_queue" / "failed" / f"reject-{index}.json").read_text())
                self.assertEqual(failed["status"], "failed")
                self.assertFalse((root / "calls.txt").exists())

    def test_processor_writes_success_and_failure_receipts_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_processor_request(root)
            fake_sender, calls = self.fake_sender_script(root, 0)
            first = self.run_processor(root, fake_sender)
            second = self.run_processor(root, fake_sender)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 0)
            receipt = json.loads((root / "home" / "voryx_mail_queue" / "receipts" / "req-123.json").read_text())
            self.assertEqual(receipt["status"], "sent")
            self.assertEqual(receipt["recipient"], INTERNAL_REPORT_RECIPIENT)
            self.assertTrue(receipt["provider_message_id"].startswith("<req-123@"))
            self.assertEqual(len(calls.read_text().splitlines()), 1)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_processor_request(root)
            fake_sender, _ = self.fake_sender_script(root, 7)
            result = self.run_processor(root, fake_sender)
            self.assertEqual(result.returncode, 0)
            failed = json.loads((root / "home" / "voryx_mail_queue" / "failed" / "req-123.json").read_text())
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["exit_code"], 7)

    def test_duplicate_receipt_import_does_not_duplicate_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.Session()
            try:
                job = Job(
                    id="job-123",
                    connector="hermes",
                    task_type="Daily Report",
                    status=JobStatus.queued,
                    payload={"source": "internal_mail_queue", "request_id": "req-123"},
                    recipient_email=INTERNAL_REPORT_RECIPIENT,
                )
                db.add(job)
                receipt_dir = root / "home" / "voryx_mail_queue" / "receipts"
                receipt_dir.mkdir(parents=True)
                (receipt_dir / "req-123.json").write_text(json.dumps({
                    "request_id": "req-123",
                    "job_id": "job-123",
                    "status": "sent",
                    "delivery_status": "sent",
                    "recipient": INTERNAL_REPORT_RECIPIENT,
                    "provider_message_id": "<req-123@voryx.ca>",
                    "sent_at": "2026-06-28T01:00:00Z",
                    "evidence_type": "rfc_message_id",
                }), encoding="utf-8")
                ingest_internal_mail_receipts(db, data_path=tmp)
                ingest_internal_mail_receipts(db, data_path=tmp)
                db.flush()
                self.assertEqual(db.get(Job, "job-123").status, JobStatus.completed)
                self.assertEqual(db.get(Job, "job-123").provider_message_id, "<req-123@voryx.ca>")
                count = db.scalar(select(func.count()).select_from(select(Job.id).subquery()))
                self.assertEqual(count, 1)
            finally:
                db.close()

    def test_receipt_without_message_id_and_timeout_fail_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self.Session()
            try:
                missing_id_job = Job(
                    id="job-missing-id",
                    connector="hermes",
                    task_type="Daily Report",
                    status=JobStatus.queued,
                    payload={"source": "internal_mail_queue", "request_id": "req-missing-id"},
                    recipient_email=INTERNAL_REPORT_RECIPIENT,
                )
                stale_job = Job(
                    id="job-stale",
                    connector="hermes",
                    task_type="Daily Report",
                    status=JobStatus.queued,
                    payload={"source": "internal_mail_queue", "request_id": "req-stale"},
                    recipient_email=INTERNAL_REPORT_RECIPIENT,
                    created_at=datetime.utcnow() - timedelta(minutes=30),
                )
                db.add_all([missing_id_job, stale_job])
                receipt_dir = root / "home" / "voryx_mail_queue" / "receipts"
                receipt_dir.mkdir(parents=True)
                (receipt_dir / "req-missing-id.json").write_text(json.dumps({
                    "request_id": "req-missing-id",
                    "job_id": "job-missing-id",
                    "status": "sent",
                    "delivery_status": "sent",
                    "recipient": INTERNAL_REPORT_RECIPIENT,
                    "sent_at": "2026-06-28T01:00:00Z",
                }), encoding="utf-8")
                ingest_internal_mail_receipts(db, data_path=tmp, stale_after_minutes=15)
                self.assertEqual(db.get(Job, "job-missing-id").status, JobStatus.failed)
                self.assertIn("provider_message_id", db.get(Job, "job-missing-id").verification_reason)
                self.assertEqual(db.get(Job, "job-stale").status, JobStatus.failed)
                self.assertEqual(db.get(Job, "job-stale").evidence_type, "mail_queue_timeout")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
