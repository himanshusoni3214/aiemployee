import asyncio
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.entities import AIEmployee, Company, EmployeeStatus, Job, JobStatus
from app.workers import job_runner


class DummyConnector:
    async def execute(self, task_type, payload):
        return {'status': 'ok', 'logs': ['dummy execution completed'], 'results': {'task_type': task_type}}


class JobTerminalStateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            'sqlite://',
            connect_args={'check_same_thread': False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.original_session = job_runner.SessionLocal
        self.original_connector = job_runner.get_connector
        job_runner.SessionLocal = self.Session
        job_runner.get_connector = lambda connector: DummyConnector()

    def tearDown(self):
        job_runner.SessionLocal = self.original_session
        job_runner.get_connector = self.original_connector
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def create_job(self, status=EmployeeStatus.running, circuit_open=False):
        db = self.Session()
        try:
            company = Company(name='QA Job State Company')
            db.add(company)
            db.flush()
            employee = AIEmployee(
                company_id=company.id,
                name=f'QA Worker {status.value}',
                employee_type='QA',
                status=status,
                circuit_breaker_open=circuit_open,
            )
            db.add(employee)
            db.flush()
            job = Job(employee_id=employee.id, connector='hermes', task_type='qa_test', payload={}, status=JobStatus.queued)
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

    def test_impossible_employee_states_block_queued_job_terminally(self):
        cases = [
            (EmployeeStatus.stopped, False, 'Employee is not running or scheduled: Stopped'),
            (EmployeeStatus.paused, False, 'Employee is not running or scheduled: Paused'),
            (EmployeeStatus.archived, False, 'Employee is archived'),
            (EmployeeStatus.running, True, 'Employee circuit breaker is open'),
        ]
        for status, circuit_open, expected in cases:
            with self.subTest(status=status, circuit_open=circuit_open):
                job_id = self.create_job(status=status, circuit_open=circuit_open)
                self.assertTrue(asyncio.run(job_runner.run_once()))
                job = self.load_job(job_id)
                self.assertEqual(job.status, JobStatus.blocked)
                self.assertEqual(job.error_message, expected)
                self.assertIsNone(job.retry_after)
                self.assertIsNotNone(job.ended_at)
                self.assertEqual((job.logs or [])[-1], expected)

                self.assertFalse(asyncio.run(job_runner.run_once()))
                job_again = self.load_job(job_id)
                self.assertEqual(job_again.logs, job.logs)

    def test_running_employee_job_completes_and_leaves_no_queued_work(self):
        job_id = self.create_job(status=EmployeeStatus.running)

        self.assertTrue(asyncio.run(job_runner.run_once()))
        job = self.load_job(job_id)
        self.assertEqual(job.status, JobStatus.completed)
        self.assertEqual(job.logs, ['dummy execution completed'])
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.ended_at)

        db = self.Session()
        try:
            queued = db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.queued))
            self.assertEqual(queued, 0)
        finally:
            db.close()

    def test_scheduled_employee_job_completes_and_leaves_no_queued_work(self):
        job_id = self.create_job(status=EmployeeStatus.scheduled)

        self.assertTrue(asyncio.run(job_runner.run_once()))
        job = self.load_job(job_id)
        self.assertEqual(job.status, JobStatus.completed)
        self.assertEqual(job.logs, ['dummy execution completed'])
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.ended_at)


if __name__ == '__main__':
    unittest.main()
