import asyncio
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.base import Base
from app.models.entities import (
    CallAppointment,
    CallAttempt,
    CallCampaignSettings,
    CallComplianceItem,
    CallDisposition,
    CallQueueItem,
    CallScriptVersion,
    CallTranscript,
    ConsentSourceProfile,
    ConsentedCallingLead,
    Role,
    SuppressionEntry,
    User,
)
from app.services.call_script_studio import ensure_compliance_items, ensure_script_studio
from app.services.calling import ensure_allstate_calling_campaign, mark_do_not_call, process_retell_webhook
from app.services.calling_campaign import (
    START_CONFIRMATION,
    control_campaign,
    primary_csv_template,
    process_next_queue_item,
    queue_eligible_contacts,
    reconcile_active_calls,
    reconcile_queue_from_attempt,
    start_campaign,
    update_campaign_limits,
    upload_contacts,
)
from app.services.calling_eligibility import calling_window, evaluate_calling_lead, is_canadian_e164


class CallingCampaignTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def seed(self, db):
        user = User(id='admin', email='admin@example.com', password_hash='x', role=Role.admin)
        db.add(user)
        ensure_allstate_calling_campaign(db, user.id)
        script = ensure_script_studio(db, user.id)
        script.version_number = 8
        script.retell_agent_version = 8
        script.retell_flow_version = 8
        script.retell_agent_id = 'agent-v8'
        for item in ensure_compliance_items(db):
            item.status = 'approved'
            item.approver = 'QA approver'
            item.evidence = 'qa://approved'
            item.effective_at = datetime(2026, 8, 1)
        campaign = db.scalar(select(CallCampaignSettings))
        campaign.provider_agent_id = 'agent-v8'
        campaign.from_number = '+14377475010'
        campaign.daily_call_limit = 20
        campaign.concurrent_call_limit = 1
        campaign.baseline_version = 'v8'
        profile = ConsentSourceProfile(
            id='profile-qa', company_id='company-allstate-himanshu',
            campaign_id='campaign-allstate-quote-calling', name='Approved QA source',
            organization_represented='Allstate',
            approved_consent_language='I agree to an automated call from Allstate.',
            organization_authorized=True, automated_call_permission=True,
            consent_proof_method='signed form', default_province='Ontario',
            default_timezone='America/Toronto', source_approval_evidence='qa://source',
            approval_date=datetime(2026, 8, 1), created_by=user.id,
        )
        db.add(profile)
        db.flush()
        return user, profile, campaign, script

    def upload(self, db, user, profile, rows):
        columns = ['first_name', 'phone_number', 'consent_timestamp', 'consent_reference', 'is_test']
        content = ','.join(columns) + '\n' + '\n'.join(','.join(str(row.get(key, '')) for key in columns) for row in rows)
        return upload_contacts(db, profile=profile, content=content, filename='qa.csv', user=user)

    def test_primary_template_is_small_and_business_facing(self):
        header = primary_csv_template().splitlines()[0].split(',')
        self.assertEqual(header[:4], ['first_name', 'phone_number', 'consent_timestamp', 'consent_reference'])
        self.assertNotIn('retell_agent_id', header)
        self.assertNotIn('dncl_status', header)

    def test_canadian_numbers_are_normalized_and_non_canadian_blocked(self):
        self.assertTrue(is_canadian_e164('+16479169693'))
        self.assertFalse(is_canadian_e164('+12125550123'))
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, _, _ = self.seed(db)
            batch = self.upload(db, user, profile, [
                {'first_name': 'Ready', 'phone_number': '(647) 916-9693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'},
                {'first_name': 'Duplicate', 'phone_number': '647-916-9693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-2', 'is_test': 'true'},
                {'first_name': 'US', 'phone_number': '212-555-0123', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-3', 'is_test': 'true'},
                {'first_name': 'Review', 'phone_number': '416-555-0188', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': '', 'is_test': 'true'},
            ])
            self.assertEqual((batch.ready_count, batch.review_count, batch.blocked_count), (1, 1, 2))
            lead = db.scalar(select(ConsentedCallingLead))
            self.assertEqual(lead.phone_number, '+16479169693')
            self.assertTrue(lead.is_test)
            self.assertEqual(batch.reason_counts['DUPLICATE_IN_UPLOAD'], 1)
            self.assertEqual(batch.reason_counts['PHONE_INVALID'], 1)
            self.assertEqual(batch.reason_counts['CONSENT_REFERENCE_MISSING'], 1)

    def test_queue_creation_is_idempotent_and_mock_never_calls_provider(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, campaign, _ = self.seed(db)
            self.upload(db, user, profile, [{'first_name': 'QA', 'phone_number': '6479169693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'}])
            health = {'api_authenticated': True, 'outbound_agent_correctly_assigned': True}
            started = start_campaign(db, user, health, START_CONFIRMATION, execution_mode='mock')
            self.assertEqual(started['queued'], 1)
            self.assertEqual(queue_eligible_contacts(db, execution_mode='mock'), (0, 1))
            provider = AsyncMock()
            self.assertTrue(asyncio.run(process_next_queue_item(db, provider)))
            provider.place_call.assert_not_awaited()
            item = db.scalar(select(CallQueueItem))
            self.assertEqual(item.status, 'no_answer')
            self.assertEqual(item.attempts, 1)
            self.assertFalse(asyncio.run(process_next_queue_item(db, provider)))

    def test_pause_resume_stop_and_limits_persist(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, campaign, _ = self.seed(db)
            self.upload(db, user, profile, [{'first_name': 'QA', 'phone_number': '6479169693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'}])
            start_campaign(db, user, {}, START_CONFIRMATION, execution_mode='mock')
            self.assertEqual(control_campaign(db, user, 'pause')['status'], 'paused')
            self.assertFalse(campaign.automated_queue_enabled)
            self.assertEqual(control_campaign(db, user, 'resume')['status'], 'running')
            update_campaign_limits(db, {'daily_call_limit': 20, 'concurrent_call_limit': 3})
            self.assertEqual((campaign.daily_call_limit, campaign.concurrent_call_limit), (20, 3))
            self.assertEqual(control_campaign(db, user, 'stop')['status'], 'stopped')
            self.assertEqual(db.scalar(select(CallQueueItem)).status, 'cancelled')
            with self.assertRaisesRegex(ValueError, '1, 2, 3 or 5'):
                update_campaign_limits(db, {'concurrent_call_limit': 4})

    def test_callback_creates_one_durable_future_item(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, _, _ = self.seed(db)
            self.upload(db, user, profile, [{'first_name': 'QA', 'phone_number': '6479169693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'}])
            start_campaign(db, user, {}, START_CONFIRMATION, execution_mode='mock')
            asyncio.run(process_next_queue_item(db, AsyncMock()))
            item = db.scalar(select(CallQueueItem).where(CallQueueItem.dedupe_key.like('%:initial')))
            item.status = 'calling'
            attempt = db.get(__import__('app.models.entities', fromlist=['CallAttempt']).CallAttempt, item.call_attempt_id)
            attempt.status = 'ended'
            db.add(CallDisposition(call_attempt_id=attempt.id, disposition='callback', callback_requested=True))
            db.add(CallTranscript(call_attempt_id=attempt.id, extracted_fields={
                'callback_date': '2026-08-12', 'callback_time': '14:30',
                'callback_timezone': 'America/Toronto', 'callback_consent': True,
                'callback_reason': 'Renewal discussion',
            }))
            db.flush()
            reconcile_queue_from_attempt(db, attempt)
            reconcile_queue_from_attempt(db, attempt)
            callbacks = db.scalars(select(CallQueueItem).where(CallQueueItem.dedupe_key.like('%:callback:%'))).all()
            self.assertEqual(len(callbacks), 1)
            self.assertTrue(callbacks[0].callback_consent)
            self.assertEqual(callbacks[0].callback_reason, 'Renewal discussion')

    def test_dnc_immediately_suppresses_and_cancels_queued_items(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, _, script = self.seed(db)
            self.upload(db, user, profile, [{'first_name': 'QA', 'phone_number': '6479169693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'}])
            lead = db.scalar(select(ConsentedCallingLead))
            item = CallQueueItem(company_id=lead.company_id, campaign_id=lead.campaign_id, canonical_lead_id=lead.id, phone_number=lead.phone_number, dedupe_key='dnc-item', script_version_id=script.id, script_version=8, provider_agent_id='agent-v8', provider_agent_version=8, consent_snapshot={}, status='queued')
            db.add(item); db.flush()
            from app.models.entities import CallAttempt
            attempt = CallAttempt(company_id=lead.company_id, campaign_id=lead.campaign_id, consented_calling_lead_id=lead.id, to_number=lead.phone_number, mode='consented_campaign')
            db.add(attempt); db.flush()
            result = mark_do_not_call(db, {'voryx_call_attempt_id': attempt.id, 'reason': 'Please stop calling'})
            self.assertTrue(result['suppressed'])
            self.assertEqual(item.status, 'dnc')
            self.assertTrue(lead.consent_withdrawn)
            self.assertIsNotNone(db.scalar(select(SuppressionEntry).where(SuppressionEntry.value == lead.phone_number)))

    def test_appointment_marks_queue_terminal(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, _, _ = self.seed(db)
            self.upload(db, user, profile, [{'first_name': 'QA', 'phone_number': '6479169693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'}])
            start_campaign(db, user, {}, START_CONFIRMATION, execution_mode='mock')
            asyncio.run(process_next_queue_item(db, AsyncMock()))
            item = db.scalar(select(CallQueueItem))
            db.add(CallAppointment(call_attempt_id=item.call_attempt_id, start_time='2026-08-12 18:30', status='requested'))
            db.flush()
            attempt = db.get(__import__('app.models.entities', fromlist=['CallAttempt']).CallAttempt, item.call_attempt_id)
            attempt.status = 'ended'
            reconcile_queue_from_attempt(db, attempt)
            self.assertEqual((item.status, item.outcome), ('appointment', 'appointment'))

    def test_calling_window_waits_without_failure(self):
        sunday = datetime(2026, 8, 9, 15, 0)
        allowed, next_window, _ = calling_window('America/Toronto', now=sunday)
        self.assertFalse(allowed)
        self.assertIsNotNone(next_window)
        monday = datetime(2026, 8, 10, 15, 0)
        self.assertTrue(calling_window('America/Toronto', now=monday)[0])

    def test_terminal_outcomes_are_preserved_truthfully(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, _, script = self.seed(db)
            self.upload(db, user, profile, [
                {'first_name': outcome, 'phone_number': f'4165550{100 + index}', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': f'ref-{outcome}', 'is_test': 'true'}
                for index, outcome in enumerate(('no_answer', 'voicemail', 'not_interested', 'completed'))
            ])
            for index, outcome in enumerate(('no_answer', 'voicemail', 'not_interested', 'completed')):
                lead = db.scalar(select(ConsentedCallingLead).where(ConsentedCallingLead.first_name == outcome))
                attempt = CallAttempt(
                    company_id=lead.company_id, campaign_id=lead.campaign_id,
                    consented_calling_lead_id=lead.id, to_number=lead.phone_number,
                    mode='consented_campaign', status='ended', ended_at=datetime(2026, 8, 10, 15),
                )
                db.add(attempt); db.flush()
                item = CallQueueItem(
                    company_id=lead.company_id, campaign_id=lead.campaign_id,
                    canonical_lead_id=lead.id, phone_number=lead.phone_number,
                    dedupe_key=f'outcome-{outcome}', script_version_id=script.id, script_version=8,
                    provider_agent_id='agent-v8', provider_agent_version=8, consent_snapshot={},
                    status='calling', call_attempt_id=attempt.id,
                )
                db.add(item)
                if outcome != 'completed':
                    db.add(CallDisposition(call_attempt_id=attempt.id, disposition=outcome))
                db.flush()
                reconcile_queue_from_attempt(db, attempt)
                self.assertEqual((item.status, item.outcome), (outcome, outcome))

    def test_provider_failure_is_not_marked_completed_or_retried(self):
        monday = datetime(2026, 8, 10, 15, 0)
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, _, _ = self.seed(db)
            self.upload(db, user, profile, [{'first_name': 'QA', 'phone_number': '6479169693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'}])
            start_campaign(db, user, {}, START_CONFIRMATION, execution_mode='mock')
            item = db.scalar(select(CallQueueItem))
            item.execution_mode = 'live'
            provider = AsyncMock()
            provider.place_call.side_effect = RuntimeError('provider unavailable')
            ready = SimpleNamespace(ready=True, blockers=[], next_window_at=None)
            with patch('app.services.calling_campaign._now', return_value=monday), patch('app.services.calling_campaign.evaluate_calling_lead', return_value=ready):
                self.assertTrue(asyncio.run(process_next_queue_item(db, provider)))
            self.assertEqual(item.status, 'provider_failed')
            self.assertEqual(item.attempts, 1)
            self.assertEqual(db.get(CallAttempt, item.call_attempt_id).status, 'provider_failed')
            self.assertFalse(db.scalar(select(CallCampaignSettings)).automatic_retry_enabled)

    def test_daily_and_concurrency_limits_defer_without_calling(self):
        monday = datetime(2026, 8, 10, 15, 0)
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, campaign, script = self.seed(db)
            self.upload(db, user, profile, [{'first_name': 'QA', 'phone_number': '6479169693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'}])
            start_campaign(db, user, {}, START_CONFIRMATION, execution_mode='mock')
            item = db.scalar(select(CallQueueItem))
            item.execution_mode = 'live'
            campaign.daily_call_limit = 1
            db.add(CallAttempt(
                company_id=item.company_id, campaign_id=item.campaign_id,
                to_number='+14165550199', mode='consented_campaign', status='ended',
                requested_at=monday - timedelta(hours=1), internal_test=False,
            ))
            db.flush()
            provider = AsyncMock()
            ready = SimpleNamespace(ready=True, blockers=[], next_window_at=None)
            with patch('app.services.calling_campaign._now', return_value=monday), patch('app.services.calling_campaign.evaluate_calling_lead', return_value=ready):
                self.assertTrue(asyncio.run(process_next_queue_item(db, provider)))
            self.assertIsNotNone(item.scheduled_after)
            provider.place_call.assert_not_awaited()

            item.scheduled_after = None
            campaign.daily_call_limit = 20
            db.add(CallQueueItem(
                company_id=item.company_id, campaign_id=item.campaign_id,
                canonical_lead_id=item.canonical_lead_id, phone_number=item.phone_number,
                dedupe_key='already-active', script_version_id=script.id, script_version=8,
                provider_agent_id='agent-v8', provider_agent_version=8, consent_snapshot={}, status='calling',
            ))
            db.flush()
            with patch('app.services.calling_campaign._now', return_value=monday), patch('app.services.calling_campaign.evaluate_calling_lead', return_value=ready):
                self.assertFalse(asyncio.run(process_next_queue_item(db, provider)))
            provider.place_call.assert_not_awaited()

    def test_restart_reconciliation_updates_existing_item_without_duplicate_call(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, _, _ = self.seed(db)
            self.upload(db, user, profile, [{'first_name': 'QA', 'phone_number': '6479169693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'}])
            start_campaign(db, user, {}, START_CONFIRMATION, execution_mode='mock')
            asyncio.run(process_next_queue_item(db, AsyncMock()))
            item = db.scalar(select(CallQueueItem))
            attempt = db.get(CallAttempt, item.call_attempt_id)
            item.status = 'calling'
            item.provider_call_id = 'call-existing'
            item.completed_at = None
            attempt.status = 'initiated'
            attempt.provider_call_id = 'call-existing'
            provider = AsyncMock()
            provider.get_call.return_value = {
                'call_id': 'call-existing', 'call_status': 'ended',
                'end_timestamp': 1786375800000, 'duration_ms': 30000,
                'call_analysis': {'custom_analysis_data': {'call_outcome': 'voicemail'}},
            }
            self.assertEqual(asyncio.run(reconcile_active_calls(db, provider)), 1)
            self.assertEqual((item.status, item.outcome), ('voicemail', 'voicemail'))
            self.assertEqual(db.scalar(select(func.count(CallAttempt.id))), 1)
            provider.get_call.assert_awaited_once_with('call-existing')
            provider.place_call.assert_not_awaited()

    def test_signed_webhook_callback_reconciles_with_production_autoflush_disabled(self):
        ProductionSession = sessionmaker(bind=self.engine, autoflush=False)
        with ProductionSession() as db, patch.object(settings, 'retell_agent_id', 'agent-v8'):
            user, profile, _, _ = self.seed(db)
            self.upload(db, user, profile, [{'first_name': 'QA', 'phone_number': '6479169693', 'consent_timestamp': '2026-08-08T12:00:00', 'consent_reference': 'ref-1', 'is_test': 'true'}])
            db.flush()
            start_campaign(db, user, {}, START_CONFIRMATION, execution_mode='mock')
            db.flush()
            asyncio.run(process_next_queue_item(db, AsyncMock()))
            item = db.scalar(select(CallQueueItem))
            attempt = db.get(CallAttempt, item.call_attempt_id)
            item.status = 'calling'
            item.completed_at = None
            item.provider_call_id = 'call-callback-webhook'
            attempt.status = 'initiated'
            attempt.provider_call_id = 'call-callback-webhook'
            payload = {
                'event': 'call_ended',
                'call': {
                    'call_id': 'call-callback-webhook', 'call_status': 'ended',
                    'call_analysis': {'custom_analysis_data': {
                        'call_outcome': 'callback', 'callback_requested': True,
                        'callback_date': '2026-08-15', 'callback_time': '10:00',
                        'callback_timezone': 'America/Toronto', 'callback_consent': True,
                        'callback_reason': 'Renewal follow-up', 'renewal_month': 'October',
                    }},
                },
            }
            process_retell_webhook(db, b'qa-callback-event', payload)
            self.assertEqual((item.status, item.outcome, item.callback_consent), ('callback', 'callback', True))
            db.flush()
            callbacks = db.scalars(select(CallQueueItem).where(CallQueueItem.dedupe_key.like('%:callback:%'))).all()
            self.assertEqual(len(callbacks), 1)


if __name__ == '__main__':
    unittest.main()
