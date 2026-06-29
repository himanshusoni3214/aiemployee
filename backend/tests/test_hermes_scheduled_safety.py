import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes
from app.models.base import Base
from app.models.entities import AIEmployee, Campaign, Company, EmployeeStatus, Job, JobStatus, Role, Schedule, Status, User
from app.services.hermes_control import HermesControlService
from app.services.hermes_import import HermesImportService
from app.services.hermes_safety import OUTREACH_FOLLOWUP_HERMES_JOB_ID, SAFETY_LOCK_MESSAGE


class FakeMonitor:
    def __init__(self, jobs):
        self.jobs = jobs

    def summary(self):
        return {"status": "ok", "jobs": self.jobs}


def hermes_job(job_id: str, name: str, *, enabled=True, state="scheduled", last_status=None, **extra):
    data = {
        "id": job_id,
        "name": name,
        "enabled": enabled,
        "state": state,
        "last_status": last_status,
        "schedule": {"expr": "0 7 * * *", "timezone": "America/Toronto"},
        "schedule_display": "0 7 * * *",
    }
    data.update(extra)
    return data


class HermesScheduledSafetyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_sync = routes._sync_hermes_snapshot
        self.original_control = routes._control_hermes_job

    def tearDown(self):
        routes._sync_hermes_snapshot = self.original_sync
        routes._control_hermes_job = self.original_control
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def make_user(self, db):
        user = User(id="qa-admin", email="qa-admin@example.invalid", password_hash="hash", role=Role.admin, is_active=True)
        db.add(user)
        return user

    def make_company_campaign(self, db):
        company = Company(id="company-brew-it-by-sash", name="Brew It By Sash", status=Status.active)
        campaign = Campaign(id="campaign-brew-it-by-sash-qa", company_id=company.id, name="Brew QA", industry="QA", status=Status.active)
        db.add_all([company, campaign])
        db.flush()
        return company, campaign

    def make_employee_schedule(self, db, *, status=EmployeeStatus.stopped, hermes_job_id="5881b72113ce", schedule_id=None):
        company, campaign = self.make_company_campaign(db)
        employee = AIEmployee(
            id=f"employee-hermes-{hermes_job_id}",
            company_id=company.id,
            campaign_id=campaign.id,
            name=f"Hermes {hermes_job_id}",
            employee_type="QA",
            status=status,
            hermes_job_id=hermes_job_id,
            last_error="old-error",
            last_heartbeat_at=datetime(2026, 6, 27, 12, 0, 0),
        )
        schedule = Schedule(
            id=schedule_id or f"schedule-hermes-{hermes_job_id}",
            employee_id=employee.id,
            name=f"Schedule {hermes_job_id}",
            cron="0 7 * * *",
            task_type="Daily Report",
            payload={"source": "hermes", "hermes_job_id": hermes_job_id},
            is_paused=status == EmployeeStatus.paused,
        )
        db.add_all([employee, schedule])
        db.flush()
        return employee, schedule

    def test_hermes_import_maps_live_states_to_employee_statuses(self):
        service = HermesImportService()
        service.monitor = FakeMonitor([
            hermes_job("scheduled-job", "Hermes Lead Research", enabled=True, state="scheduled"),
            hermes_job("paused-job", "Hermes Outreach Draft", enabled=False, state="paused"),
            hermes_job("running-job", "Hermes End Day Report", enabled=True, state="running"),
            hermes_job("error-job", "Hermes Broken Worker", enabled=True, state="scheduled", last_status="error", last_error="exact failure"),
            hermes_job(OUTREACH_FOLLOWUP_HERMES_JOB_ID, "Hermes Outreach Followup", enabled=False, state="paused"),
        ])
        db = self.Session()
        try:
            service.sync(db, "qa-admin")
            employees = {employee.hermes_job_id: employee for employee in db.scalars(select(AIEmployee)).all()}
            self.assertEqual(employees["scheduled-job"].status, EmployeeStatus.scheduled)
            self.assertEqual(employees["paused-job"].status, EmployeeStatus.paused)
            self.assertEqual(employees["running-job"].status, EmployeeStatus.running)
            self.assertEqual(employees["error-job"].status, EmployeeStatus.error)
            self.assertEqual(employees["error-job"].last_error, "exact failure")
            self.assertEqual(employees[OUTREACH_FOLLOWUP_HERMES_JOB_ID].paused_reason, SAFETY_LOCK_MESSAGE)
        finally:
            db.close()

    def write_jobs_file(self, root: str, jobs: list[dict]):
        cron = Path(root) / "cron"
        cron.mkdir(parents=True)
        jobs_file = cron / "jobs.json"
        jobs_file.write_text(json.dumps({"jobs": jobs}, indent=2) + "\n", encoding="utf-8")
        return jobs_file

    def read_jobs_file(self, jobs_file: Path):
        return json.loads(jobs_file.read_text(encoding="utf-8"))

    def test_hermes_control_is_idempotent_and_clears_resume_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs_file = self.write_jobs_file(tmp, [
                hermes_job("lead", "Hermes Lead Research", enabled=True, state="scheduled", next_run_at=None),
                hermes_job("draft", "Hermes Outreach Draft", enabled=False, state="paused", next_run_at=None),
                hermes_job("report", "Hermes End Day Report", enabled=False, state="paused", next_run_at=None, paused_at="2026-06-28T12:00:00Z", paused_reason="old pause", last_error="old error", last_delivery_error="old delivery"),
            ])
            service = HermesControlService(data_path=tmp)

            resume_scheduled = service.control("lead", "resume")
            self.assertEqual(resume_scheduled["status"], "ok")
            self.assertTrue(resume_scheduled["no_change"])

            pause_paused = service.control("draft", "pause")
            self.assertEqual(pause_paused["status"], "ok")
            self.assertTrue(pause_paused["no_change"])

            resume_report = service.control("report", "resume")
            self.assertEqual(resume_report["status"], "ok")
            self.assertFalse(resume_report["no_change"])
            report = next(job for job in self.read_jobs_file(jobs_file)["jobs"] if job["id"] == "report")
            self.assertTrue(report["enabled"])
            self.assertEqual(report["state"], "scheduled")
            self.assertIsNone(report["paused_at"])
            self.assertIsNone(report["paused_reason"])
            self.assertIsNone(report["last_error"])
            self.assertIsNone(report["last_delivery_error"])

    def test_safety_locked_followup_control_is_blocked_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = [
                hermes_job(OUTREACH_FOLLOWUP_HERMES_JOB_ID, "Hermes Outreach Followup", enabled=False, state="paused", next_run_at=None),
                hermes_job("0d0c20e25f55", "Hermes Lead Research", enabled=True, state="scheduled", next_run_at="2026-06-29T12:00:00Z"),
                hermes_job("5881b72113ce", "Hermes End Day Report", enabled=True, state="scheduled", next_run_at="2026-06-29T12:00:00Z"),
            ]
            jobs_file = self.write_jobs_file(tmp, copy.deepcopy(jobs))
            service = HermesControlService(data_path=tmp)

            blocked = service.control(OUTREACH_FOLLOWUP_HERMES_JOB_ID, "resume")
            self.assertEqual(blocked["status"], "safety_blocked")
            self.assertFalse(blocked["ok"])
            self.assertIn("real Gmail prospect outreach", blocked["message"])
            self.assertEqual(self.read_jobs_file(jobs_file)["jobs"][0], jobs[0])

            self.assertEqual(service.control("0d0c20e25f55", "run")["status"], "ok")
            self.assertEqual(service.control("5881b72113ce", "resume")["status"], "ok")

    def test_safe_scheduled_employee_run_refreshes_stale_database_state_first(self):
        db = self.Session()
        try:
            user = self.make_user(db)
            employee, _ = self.make_employee_schedule(db, status=EmployeeStatus.stopped, hermes_job_id="5881b72113ce")
            db.commit()
            controls: list[tuple[str, str]] = []

            def fake_sync(sync_db, user_id, force=False):
                refreshed = sync_db.get(AIEmployee, employee.id)
                refreshed.status = EmployeeStatus.scheduled
                refreshed.last_error = None
                refreshed.last_heartbeat_at = datetime(2026, 6, 29, 12, 0, 0)
                sync_db.flush()
                return {"status": "ok", "force": force}

            def fake_control(hermes_job_id, action):
                controls.append((hermes_job_id, action))
                return {"status": "ok", "action": action, "hermes_job_id": hermes_job_id}

            routes._sync_hermes_snapshot = fake_sync
            routes._control_hermes_job = fake_control
            response = routes.employee_action(employee.id, "run", db=db, user=user)
            self.assertTrue(response["ok"])
            self.assertEqual(controls, [("5881b72113ce", "run")])
            db.commit()
            self.assertEqual(db.get(AIEmployee, employee.id).status, EmployeeStatus.scheduled)
            manual = db.scalar(select(Job).where(Job.employee_id == employee.id).order_by(Job.created_at.desc()))
            self.assertEqual(manual.status, JobStatus.queued)
            self.assertNotIn("blocked", response["state"].lower())
        finally:
            db.close()

    def test_blocked_manual_run_does_not_overwrite_employee_health(self):
        db = self.Session()
        try:
            user = self.make_user(db)
            employee, _ = self.make_employee_schedule(db, status=EmployeeStatus.paused, hermes_job_id=None)
            employee.hermes_job_id = None
            employee.daily_limits = {}
            expected_error = employee.last_error
            expected_heartbeat = employee.last_heartbeat_at
            db.commit()

            response = routes.employee_action(employee.id, "run", db=db, user=user)
            self.assertFalse(response["ok"])
            db.commit()
            refreshed = db.get(AIEmployee, employee.id)
            self.assertEqual(refreshed.last_error, expected_error)
            self.assertEqual(refreshed.last_heartbeat_at, expected_heartbeat)
            manual = db.scalar(select(Job).where(Job.employee_id == employee.id).order_by(Job.created_at.desc()))
            self.assertEqual(manual.status, JobStatus.blocked)
            self.assertIn("employee is paused", manual.error_message)
        finally:
            db.close()

    def test_followup_employee_resume_and_run_are_safety_blocked(self):
        db = self.Session()
        try:
            user = self.make_user(db)
            employee, _ = self.make_employee_schedule(db, status=EmployeeStatus.paused, hermes_job_id=OUTREACH_FOLLOWUP_HERMES_JOB_ID)
            db.commit()
            routes._sync_hermes_snapshot = lambda sync_db, user_id, force=False: {"status": "ok"}
            routes._control_hermes_job = lambda hermes_job_id, action: self.fail("safety locked followup must not call Hermes control")

            resume = routes.employee_action(employee.id, "resume", db=db, user=user)
            self.assertEqual(resume["state"], "safety_blocked")
            self.assertFalse(resume["ok"])

            run = routes.employee_action(employee.id, "run", db=db, user=user)
            self.assertEqual(run["state"], "safety_blocked")
            self.assertFalse(run["ok"])
            manual = db.scalar(select(Job).where(Job.employee_id == employee.id).order_by(Job.created_at.desc()))
            self.assertEqual(manual.status, JobStatus.blocked)
            self.assertIn("Safety blocked", manual.error_message)
        finally:
            db.close()

    def test_followup_schedule_actions_are_safety_blocked(self):
        db = self.Session()
        try:
            user = self.make_user(db)
            _, schedule = self.make_employee_schedule(db, status=EmployeeStatus.paused, hermes_job_id=OUTREACH_FOLLOWUP_HERMES_JOB_ID)
            db.commit()
            routes._sync_hermes_snapshot = lambda sync_db, user_id, force=False: {"status": "ok"}
            routes._control_hermes_job = lambda hermes_job_id, action: self.fail("safety locked followup must not call Hermes control")

            for action in ("resume", "run", "dry-run", "test-run"):
                with self.subTest(action=action):
                    response = routes.schedule_action(schedule.id, action, db=db, user=user)
                    self.assertEqual(response["state"], "safety_blocked")
                    self.assertFalse(response["ok"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
