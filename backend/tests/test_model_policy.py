import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models.base import Base
from app.models.entities import AIEmployee, Campaign, Company, EmployeeStatus, Status
from app.services import model_policy
from app.services.hermes_jobs_json_executor import _model_policy_blocked


class ModelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.tmp = tempfile.TemporaryDirectory()
        self.original_data_path = settings.hermes_data_path
        settings.hermes_data_path = self.tmp.name
        cron = Path(self.tmp.name) / "cron"
        cron.mkdir(parents=True)
        (cron / "jobs.json").write_text(json.dumps({"jobs": [{"id": "job-policy", "enabled": True, "state": "scheduled"}]}), encoding="utf-8")

    def tearDown(self):
        settings.hermes_data_path = self.original_data_path
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        self.tmp.cleanup()

    def test_default_policy_allows_openrouter_nemotron(self):
        db = self.Session()
        try:
            policy = model_policy.effective_policy(db)
            decision = model_policy.validate_policy(policy)
            self.assertTrue(decision["allowed"])
            self.assertEqual(decision["normalized_model"], model_policy.DEFAULT_NORMALIZED_MODEL)
            self.assertFalse(policy["fallback_enabled"])
        finally:
            db.close()

    def test_blocks_gpt_and_gemini_models(self):
        db = self.Session()
        try:
            policy = model_policy.effective_policy(db)
            for name in ["openai/gpt-4o", "openai/gpt-4o-mini", "google/gemini-1.5-pro", "gemini"]:
                decision = model_policy.validate_policy(policy, requested_model=name)
                self.assertFalse(decision["allowed"])
                self.assertEqual(decision["status"], "model_blocked")
        finally:
            db.close()

    def test_unknown_model_is_unavailable_not_fallback(self):
        db = self.Session()
        try:
            policy = model_policy.effective_policy(db)
            decision = model_policy.validate_policy(policy, requested_model="nvidia/nonexistent-model")
            self.assertFalse(decision["allowed"])
            self.assertEqual(decision["status"], "model_unavailable")
        finally:
            db.close()

    def test_budget_guard_blocks_positive_limit_exceeded(self):
        db = self.Session()
        try:
            policy = model_policy.effective_policy(db)
            policy["max_cost_per_run_usd"] = 1
            decision = model_policy.validate_policy(policy, estimated_cost_usd=2)
            self.assertFalse(decision["allowed"])
            self.assertEqual(decision["status"], "budget_blocked")
        finally:
            db.close()

    def test_syncs_effective_policy_to_jobs_json(self):
        db = self.Session()
        try:
            company = Company(id="company-policy", name="Policy Co", status=Status.active)
            campaign = Campaign(id="campaign-policy", company_id=company.id, name="Policy Campaign")
            employee = AIEmployee(id="employee-policy", company_id=company.id, campaign_id=campaign.id, name="Policy Worker", employee_type="Lead Researcher", hermes_job_id="job-policy", status=EmployeeStatus.scheduled)
            db.add_all([company, campaign, employee])
            db.flush()
            result = model_policy.sync_model_policy_to_jobs_json(db, hermes_job_id="job-policy", employee_id=employee.id)
            self.assertTrue(result["ok"])
            jobs = json.loads((Path(self.tmp.name) / "cron" / "jobs.json").read_text(encoding="utf-8"))["jobs"]
            self.assertEqual(jobs[0]["model_policy"]["normalized_model"], model_policy.DEFAULT_NORMALIZED_MODEL)
            self.assertFalse(jobs[0]["model_policy"]["fallback_enabled"])
        finally:
            db.close()

    def test_runtime_guard_records_blocked_audit_before_execution(self):
        db = self.Session()
        try:
            company = Company(id="company-policy", name="Policy Co", status=Status.active)
            campaign = Campaign(id="campaign-policy", company_id=company.id, name="Policy Campaign")
            employee = AIEmployee(id="employee-policy", company_id=company.id, campaign_id=campaign.id, name="Policy Worker", employee_type="Lead Researcher", hermes_job_id="job-policy", status=EmployeeStatus.scheduled)
            db.add_all([company, campaign, employee])
            db.flush()
            result = model_policy.guard_hermes_execution(db, task_type="Generate Leads", payload={"hermes_job_id": "job-policy", "model_policy": {"model": "openai/gpt-4o"}})
            self.assertFalse(result["allowed"])
            audits = db.execute(select(model_policy.ModelUsageAudit)).scalars().all()
            self.assertEqual(audits[-1].status, "model_blocked")
        finally:
            db.close()

    def test_executor_block_result_is_truthful_blocked_status(self):
        with patch('app.services.hermes_jobs_json_executor.CRON_OUTPUT_DIR', Path(self.tmp.name) / 'cron' / 'output'):
            result = _model_policy_blocked('job-policy', 'Generate Leads', {'hermes_job_id': 'job-policy'}, {'decision': {'status': 'model_blocked', 'reason': 'GPT blocked', 'normalized_model': 'openai/gpt-4o'}, 'policy': {}})
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('GPT blocked', result['error'])
        self.assertEqual(result['results']['prospect_emails_sent'], 0)


if __name__ == '__main__':
    unittest.main()
