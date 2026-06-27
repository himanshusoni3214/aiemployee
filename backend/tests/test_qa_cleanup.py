import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts' / 'ops'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from app.core.security import hash_password  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.entities import (  # noqa: E402
    AIEmployee,
    Campaign,
    Company,
    EmployeeStatus,
    Job,
    JobStatus,
    Role,
    Schedule,
    Status,
    User,
)
from qa_cleanup import cleanup_qa_records  # noqa: E402


class QaCleanupTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            'sqlite://',
            connect_args={'check_same_thread': False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def seed(self):
        db = self.Session()
        try:
            admin = User(email='admin@example.com', password_hash=hash_password('pass'), role=Role.admin, is_active=True)
            brew = Company(name='Brew It By Sash', status=Status.active)
            qa = Company(name='QA Audit Company 20260627', status=Status.active)
            db.add_all([admin, brew, qa])
            db.flush()

            brew_campaign = Campaign(company_id=brew.id, name='Brew Outreach', industry='Food', status=Status.active)
            qa_campaign = Campaign(company_id=qa.id, name='QA Audit Campaign 20260627', industry='QA', status=Status.active)
            db.add_all([brew_campaign, qa_campaign])
            db.flush()

            brew_employee = AIEmployee(company_id=brew.id, campaign_id=brew_campaign.id, name='Brew Worker', employee_type='QA', status=EmployeeStatus.running)
            qa_employee = AIEmployee(company_id=qa.id, campaign_id=qa_campaign.id, name='QA Audit Worker 20260627', employee_type='QA', status=EmployeeStatus.running)
            db.add_all([brew_employee, qa_employee])
            db.flush()

            brew_schedule = Schedule(employee_id=brew_employee.id, name='Brew Schedule', cron='0 9 * * *', task_type='brew')
            qa_schedule = Schedule(employee_id=qa_employee.id, name='QA Audit Schedule 20260627', cron='0 9 * * *', task_type='qa')
            brew_job = Job(employee_id=brew_employee.id, campaign_id=brew_campaign.id, task_type='brew', status=JobStatus.queued)
            qa_job = Job(employee_id=qa_employee.id, campaign_id=qa_campaign.id, task_type='qa', status=JobStatus.queued)
            db.add_all([brew_schedule, qa_schedule, brew_job, qa_job])
            db.commit()
            return {'brew': brew.id, 'qa': qa.id, 'brew_job': brew_job.id, 'qa_job': qa_job.id}
        finally:
            db.close()

    def test_cleanup_archives_qa_records_and_does_not_touch_brew(self):
        ids = self.seed()
        db = self.Session()
        try:
            dry_run = cleanup_qa_records(db, 'QA Audit', 'admin@example.com', dry_run=True)
            self.assertTrue(dry_run['dry_run'])
            self.assertEqual(dry_run['counts']['companies'], 1)
            db.rollback()

            result = cleanup_qa_records(db, 'QA Audit', 'admin@example.com', dry_run=False)
            self.assertFalse(result['dry_run'])

            brew = db.get(Company, ids['brew'])
            qa = db.get(Company, ids['qa'])
            self.assertEqual(brew.status, Status.active)
            self.assertEqual(qa.status, Status.archived)

            qa_employee = db.scalar(select(AIEmployee).where(AIEmployee.company_id == qa.id))
            self.assertEqual(qa_employee.status, EmployeeStatus.archived)
            qa_schedule = db.scalar(select(Schedule).where(Schedule.employee_id == qa_employee.id))
            self.assertTrue(qa_schedule.is_paused)
            qa_job = db.get(Job, ids['qa_job'])
            brew_job = db.get(Job, ids['brew_job'])
            self.assertEqual(qa_job.status, JobStatus.cancelled)
            self.assertEqual(brew_job.status, JobStatus.queued)
        finally:
            db.close()

    def test_cleanup_rejects_non_qa_prefix(self):
        self.seed()
        db = self.Session()
        try:
            with self.assertRaises(ValueError):
                cleanup_qa_records(db, 'Brew', 'admin@example.com', dry_run=True)
        finally:
            db.close()


if __name__ == '__main__':
    unittest.main()
