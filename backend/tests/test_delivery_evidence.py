import asyncio
import csv
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.entities import AIEmployee, Company, EmployeeStatus, Job, JobStatus
from app.services.daily_report import generate_daily_report
from app.services.hermes_import import HermesImportService
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


if __name__ == "__main__":
    unittest.main()
