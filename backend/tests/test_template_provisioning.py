import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes
from app.core.config import settings
from app.models.base import Base
from app.models.entities import AIEmployee, Campaign, Company, EmployeeStatus, Job, Schedule, Status, User, Role
from app.services.template_provisioning import APPROVED_INTERNAL_RECIPIENT, provision_campaign_template


class TemplateProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_data_path = settings.hermes_data_path
        self.tmp = tempfile.TemporaryDirectory()
        settings.hermes_data_path = self.tmp.name
        cron = Path(self.tmp.name) / "cron"
        cron.mkdir(parents=True)
        (cron / "jobs.json").write_text(json.dumps({"jobs": []}), encoding="utf-8")

    def tearDown(self):
        settings.hermes_data_path = self.original_data_path
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        self.tmp.cleanup()

    def make_base(self, db):
        company = Company(id="company-template-qa", name="Template QA", status=Status.active)
        user = User(id="qa-admin", email="qa@example.invalid", password_hash="hash", role=Role.admin, is_active=True)
        db.add_all([company, user])
        db.flush()
        return company, user

    def jobs_json(self):
        return json.loads((Path(self.tmp.name) / "cron" / "jobs.json").read_text(encoding="utf-8"))

    def test_lead_research_template_provisions_disabled_hermes_job_and_paused_schedule(self):
        db = self.Session()
        try:
            company, user = self.make_base(db)
            campaign = Campaign(
                company_id=company.id,
                name="Template Lead Research",
                industry="cafes",
                geographic_area="Toronto",
                target_audience="independent cafe owners",
                description="Exclude franchises and chains",
                campaign_type="lead_research",
                daily_lead_goal=25,
            )
            db.add(campaign)
            db.flush()

            result = provision_campaign_template(db, campaign, user.id)
            db.commit()

            self.assertTrue(result["provisioned"])
            self.assertEqual(campaign.provisioning_state, "Provisioned")
            employee = db.scalar(select(AIEmployee).where(AIEmployee.campaign_id == campaign.id))
            schedule = db.scalar(select(Schedule).where(Schedule.employee_id == employee.id))
            self.assertEqual(employee.status, EmployeeStatus.paused)
            self.assertTrue(employee.hermes_job_id)
            self.assertTrue(schedule.is_paused)
            hermes_job = self.jobs_json()["jobs"][0]
            self.assertEqual(hermes_job["id"], employee.hermes_job_id)
            self.assertFalse(hermes_job["enabled"])
            self.assertEqual(hermes_job["state"], "paused")
            self.assertFalse(hermes_job["safety"]["email_sending"])
            self.assertIn("voryx_generic_lead_research.py", hermes_job["command"])
            self.assertNotIn("brew_it_by_sash.py", hermes_job["command"])
            self.assertIn("--no-email", hermes_job["command"])
            self.assertEqual(hermes_job["safety"]["config"]["industry"], "cafes")
            self.assertEqual(hermes_job["safety"]["config"]["location"], "Toronto")
            config_path = Path(self.tmp.name) / "home" / "voryx_workspaces" / "company-template-qa" / campaign.id / "lead_research_config.json"
            self.assertTrue(config_path.exists())
        finally:
            db.close()

    def test_lead_research_template_requires_industry_and_location(self):
        db = self.Session()
        try:
            company, user = self.make_base(db)
            campaign = Campaign(
                company_id=company.id,
                name="Missing Lead Config",
                industry="cafes",
                geographic_area="",
                campaign_type="lead_research",
                daily_lead_goal=5,
            )
            db.add(campaign)
            db.flush()

            with self.assertRaises(ValueError) as ctx:
                provision_campaign_template(db, campaign, user.id)

            self.assertIn("City / region", str(ctx.exception))
        finally:
            db.close()

    def test_duplicate_provisioning_does_not_duplicate_jobs(self):
        db = self.Session()
        try:
            company, user = self.make_base(db)
            campaign = Campaign(company_id=company.id, name="Template Duplicate", campaign_type="daily_reporting", report_recipient=APPROVED_INTERNAL_RECIPIENT)
            db.add(campaign)
            db.flush()
            first = provision_campaign_template(db, campaign, user.id)
            second = provision_campaign_template(db, campaign, user.id)
            db.commit()

            self.assertEqual(first["hermes_job_id"], second["hermes_job_id"])
            self.assertEqual(len(self.jobs_json()["jobs"]), 1)
            self.assertEqual(db.query(AIEmployee).count(), 1)
            self.assertEqual(db.query(Schedule).count(), 1)
        finally:
            db.close()

    def test_employee_cannot_start_without_hermes_job_id(self):
        db = self.Session()
        try:
            company, user = self.make_base(db)
            employee = AIEmployee(company_id=company.id, name="No Hermes", employee_type="Custom", status=EmployeeStatus.stopped)
            db.add(employee)
            db.commit()

            with self.assertRaises(HTTPException) as ctx:
                routes.employee_action(employee.id, "resume", db=db, user=user)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("Hermes job ID", ctx.exception.detail)
        finally:
            db.close()

    def test_template_sample_actions_send_no_email_and_restrict_recipient(self):
        db = self.Session()
        try:
            company, user = self.make_base(db)
            lead_campaign = Campaign(company_id=company.id, name="Sample Leads", campaign_type="lead_research", industry="cafes", geographic_area="Toronto", daily_lead_goal=20)
            report_campaign = Campaign(company_id=company.id, name="Sample Report", campaign_type="daily_reporting", report_recipient=APPROVED_INTERNAL_RECIPIENT)
            draft_campaign = Campaign(company_id=company.id, name="Sample Draft", campaign_type="outreach_drafting")
            db.add_all([lead_campaign, report_campaign, draft_campaign])
            db.flush()
            for campaign in (lead_campaign, report_campaign, draft_campaign):
                provision_campaign_template(db, campaign, user.id)

            lead_job = routes.campaign_template_action(lead_campaign.id, "generate-sample", db=db, user=user)
            report_job = routes.campaign_template_action(report_campaign.id, "send-internal-test", db=db, user=user)
            draft_job = routes.campaign_template_action(draft_campaign.id, "generate-sample-draft", db=db, user=user)
            db.commit()

            self.assertEqual(db.query(Job).count(), 3)
            self.assertLessEqual(lead_job["result"]["leads_generated"], 5)
            self.assertFalse(report_job["result"]["email_sent"])
            self.assertEqual(report_job["result"]["recipient"], APPROVED_INTERNAL_RECIPIENT)
            self.assertFalse(draft_job["result"]["email_sent"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
