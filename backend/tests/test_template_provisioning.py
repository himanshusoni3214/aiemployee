import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes
from app.core.config import settings
from app.models.base import Base
from app.models.entities import AIEmployee, Campaign, Company, EmployeeStatus, Job, Schedule, Status, User, Role
from app.services.template_provisioning import APPROVED_INTERNAL_RECIPIENT, provision_campaign_template, provision_employee_template, validate_campaign_blueprint, provision_sales_campaign_defaults


class TemplateProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.model_guard = patch('app.services.hermes_jobs_json_executor._model_policy_guard', return_value={"allowed": True, "decision": {"status": "allowed"}, "policy": {}})
        self.model_guard.start()
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
        self.model_guard.stop()
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

    def write_seed_csv(self):
        path = Path(self.tmp.name) / "home" / "leads" / "seed.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("business_name,website,phone,email,city,category,source_url\nReal Cafe,http://realcafe.example,416-555-0101,hello@realcafe.example,Toronto,cafes,http://source.example/real-cafe\n", encoding="utf-8")
        return "/opt/data/home/leads/seed.csv"

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


    def test_campaign_blueprint_does_not_auto_provision_but_employee_templates_do(self):
        db = self.Session()
        try:
            company, user = self.make_base(db)
            campaign = Campaign(
                company_id=company.id,
                name="Cafe Outreach QA",
                campaign_type="sales_outreach",
                industry="cafes",
                geographic_area="Toronto",
                target_audience="independent cafe owners",
                description="Offer: coffee concentrate pilot; Tone: helpful; Exclude franchises",
                daily_lead_goal=5,
                report_recipient=APPROVED_INTERNAL_RECIPIENT,
                dry_run_mode=True,
                provisioning_result={"lead_source": {"type": "uploaded_seed_csv", "file": self.write_seed_csv()}},
            )
            db.add(campaign)
            db.flush()
            validate_campaign_blueprint(campaign)
            self.assertEqual(db.query(AIEmployee).count(), 0)

            lead_employee = AIEmployee(company_id=company.id, campaign_id=campaign.id, name="QA Lead Researcher", employee_type="Lead Researcher")
            report_employee = AIEmployee(company_id=company.id, campaign_id=campaign.id, name="QA Daily Reporter", employee_type="CRM Manager")
            db.add_all([lead_employee, report_employee])
            db.flush()
            lead_result = provision_employee_template(db, lead_employee, user.id)
            report_result = provision_employee_template(db, report_employee, user.id)
            db.commit()

            self.assertTrue(lead_result["provisioned"])
            self.assertTrue(report_result["provisioned"])
            self.assertEqual(db.query(AIEmployee).count(), 2)
            self.assertEqual(db.query(Schedule).count(), 2)
            jobs = self.jobs_json()["jobs"]
            self.assertEqual(len(jobs), 2)
            lead_job = next(job for job in jobs if job["id"] == lead_employee.hermes_job_id)
            self.assertIn("voryx_generic_lead_research.py", lead_job["command"])
            self.assertIn("--employee-id", lead_job["command"])
            self.assertIn("--hermes-job-id", lead_job["command"])
            self.assertFalse(lead_job["enabled"])
            self.assertFalse(report_employee.daily_limits["safety"]["prospect_outreach"])
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


    def test_generate_sample_without_internet_provider_fails_without_fake_csv(self):
        db = self.Session()
        try:
            company, user = self.make_base(db)
            campaign = Campaign(company_id=company.id, name="No Source Leads", campaign_type="lead_research", industry="cafes", geographic_area="Toronto", target_audience="independent cafe owners", daily_lead_goal=5)
            db.add(campaign)
            db.flush()
            provision_campaign_template(db, campaign, user.id)
            result = routes.campaign_template_action(campaign.id, "generate-sample", db=db, user=user)
            db.commit()
            self.assertEqual(result["status"], "failed")
            self.assertIn("internet_research_provider_not_configured", json.dumps(result["result"]))
            output_dir = Path(self.tmp.name) / "home" / "voryx_workspaces" / "company-template-qa" / campaign.id / "leads"
            self.assertFalse(list(output_dir.glob("*.csv")))
        finally:
            db.close()

    def test_template_sample_actions_send_no_email_and_restrict_recipient(self):
        db = self.Session()
        try:
            company, user = self.make_base(db)
            lead_campaign = Campaign(company_id=company.id, name="Sample Leads", campaign_type="lead_research", industry="cafes", geographic_area="Toronto", target_audience="independent cafe owners", daily_lead_goal=20, provisioning_result={"lead_source": {"type": "uploaded_seed_csv", "file": self.write_seed_csv()}})
            report_campaign = Campaign(company_id=company.id, name="Sample Report", campaign_type="daily_reporting", report_recipient=APPROVED_INTERNAL_RECIPIENT)
            draft_campaign = Campaign(company_id=company.id, name="Sample Draft", campaign_type="outreach_drafting", target_audience="independent cafe owners", description="Offer: 14-day pilot; Tone: helpful")
            db.add_all([lead_campaign, report_campaign, draft_campaign])
            db.flush()
            for campaign in (lead_campaign, report_campaign, draft_campaign):
                provision_campaign_template(db, campaign, user.id)

            lead_job = routes.campaign_template_action(lead_campaign.id, "generate-sample", db=db, user=user)
            report_job = routes.campaign_template_action(report_campaign.id, "send-internal-test", db=db, user=user)
            draft_job = routes.campaign_template_action(draft_campaign.id, "generate-sample-draft", db=db, user=user)
            db.commit()

            self.assertEqual(db.query(Job).count(), 3)
            self.assertLessEqual(lead_job["result"]["lead_count"], 5)
            self.assertIn("output_path", lead_job["result"])
            self.assertFalse(lead_job["result"]["email_sending"])
            output_path = Path(str(lead_job["result"]["output_path"]).replace("/opt/data/", f"{self.tmp.name}/"))
            csv_text = output_path.read_text(encoding="utf-8")
            self.assertIn("Real Cafe", csv_text)
            self.assertNotIn("Prospect 1", csv_text)
            self.assertFalse(report_job["result"]["email_sent"])
            self.assertEqual(report_job["result"]["recipient"], APPROVED_INTERNAL_RECIPIENT)
            self.assertFalse(draft_job["result"]["email_sent"])
        finally:
            db.close()


    def test_sales_outreach_auto_provisions_default_employee_set(self):
        db = self.Session()
        try:
            company = Company(id="company-sales", name="Sales Co", status=Status.active)
            campaign = Campaign(
                id="campaign-sales",
                company_id=company.id,
                name="Sales Campaign",
                campaign_type="sales_outreach",
                industry="cafes",
                geographic_area="Toronto",
                target_audience="independent cafe owners",
                description="Offer: cold brew wholesale",
                daily_lead_goal=10,
                daily_email_goal=5,
                daily_email_limit=5,
                dry_run_mode=True,
                internal_test_recipient="himanshusoni3214@gmail.com",
                report_recipient="himanshusoni3214@gmail.com",
                provisioning_result={"lead_source": {"type": "real_directory"}},
            )
            db.add_all([company, campaign]); db.flush()
            result = provision_sales_campaign_defaults(db, campaign, "admin")
            employee_types = {item["employee_type"] for item in result["employees"]}
            self.assertTrue({"Lead Researcher", "Lead Verifier", "Email Outreach", "Email Sender", "Reply Monitor", "Follow-up Manager", "CRM Manager"}.issubset(employee_types))
            self.assertEqual(result["channels"]["email"], "enabled")
            self.assertEqual(result["channels"]["calling"], "not_connected")
            self.assertEqual(campaign.provisioning_state, "Provisioned")
        finally:
            db.close()

if __name__ == "__main__":
    unittest.main()
