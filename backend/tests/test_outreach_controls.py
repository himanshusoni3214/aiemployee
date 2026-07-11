import unittest
import json
import tempfile
from datetime import datetime, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.entities import (
    Campaign,
    Company,
    CompanyOutreachSettings,
    Job,
    LeadApproval,
    OutreachDraft,
    OutreachEvent,
    Role,
    Status,
    SuppressionEntry,
    User,
)
from app.services.outreach import (
    APPROVED_INTERNAL_RECIPIENT,
    body_with_unsubscribe,
    controlled_batch_preview,
    bulk_update_drafts,
    create_internal_test_event,
    default_outreach_settings,
    generate_draft_for_item,
    review_items_from_rows,
    send_blockers,
    upsert_approval,
    validate_outreach_settings,
    outreach_readiness,
    prepare_controlled_batch,
    schedule_controlled_batch_next_window,
    send_real_controlled_batch,
    sender_verification,
)
from app.services.internal_mail_queue import enqueue_controlled_outreach_delivery, ingest_internal_mail_receipts, queue_root


class OutreachControlsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def seed(self, db):
        user = User(id='admin', email='admin@example.com', password_hash='x', role=Role.admin, is_active=True)
        company = Company(id='company-a', name='Company A', status=Status.active)
        other = Company(id='company-b', name='Company B', status=Status.active)
        campaign = Campaign(id='campaign-a', company_id=company.id, name='Cafe Outreach', industry='cafes', geographic_area='Toronto', target_audience='owners', description='14-day pilot', status=Status.active)
        other_campaign = Campaign(id='campaign-b', company_id=other.id, name='Other Campaign', industry='cafes', status=Status.active)
        db.add_all([user, company, other, campaign, other_campaign])
        db.flush()
        return user, company, campaign, other, other_campaign

    def test_review_queue_blocks_missing_duplicates_and_suppression(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            db.add(SuppressionEntry(company_id=company.id, kind='email', value='blocked@blocked-cafe.example', reason='test'))
            db.flush()
            rows = [
                {'Business Name': 'A Cafe', 'Public Email': 'a@a-cafe.example'},
                {'Business Name': 'Missing Cafe', 'Public Email': ''},
                {'Business Name': 'Dup One', 'Public Email': 'dup@dup-cafe.example'},
                {'Business Name': 'Dup Two', 'Public Email': 'dup@dup-cafe.example'},
                {'Business Name': 'Blocked', 'Public Email': 'blocked@blocked-cafe.example'},
            ]
            items = review_items_from_rows(db, campaign, rows, 'source')
            states = {item['business']: item['state'] for item in items}
            self.assertEqual(states['A Cafe'], 'new')
            self.assertEqual(states['Missing Cafe'], 'missing_email')
            self.assertEqual(states['Dup One'], 'duplicate')
            self.assertEqual(states['Blocked'], 'do_not_contact')
            approved = upsert_approval(db, campaign, items[0], 'approved_for_outreach', user.id, 'approved')
            self.assertEqual(approved.state, 'approved_for_outreach')
            with self.assertRaises(ValueError):
                upsert_approval(db, campaign, items[1], 'approved_for_outreach', user.id)
        finally:
            db.close()

    def test_draft_generation_and_send_blockers_are_company_scoped(self):
        db = self.Session()
        try:
            user, company, campaign, other, other_campaign = self.seed(db)
            rows = [{'Business Name': 'A Cafe', 'Public Email': 'a@a-cafe.example'}]
            item = review_items_from_rows(db, campaign, rows, 'source')[0]
            upsert_approval(db, campaign, item, 'approved_for_outreach', user.id)
            draft = generate_draft_for_item(db, campaign, company, {**item, 'state': 'approved_for_outreach', 'can_send': True})
            db.flush()
            self.assertEqual(draft.status, 'draft_created')
            self.assertIn('Reply STOP to opt out.', draft.body)
            self.assertIn('prospect_sending_enabled', send_blockers(db, campaign, draft, internal_test=False))

            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.sender_name = 'QA Sender'
            settings.sender_email = APPROVED_INTERNAL_RECIPIENT
            settings.reply_to_email = APPROVED_INTERNAL_RECIPIENT
            settings.physical_mailing_address = '123 QA St, Toronto'
            settings.approved_sender_connected = True
            settings.compliance_acknowledged = True
            db.add(settings)
            draft.status = 'draft_approved'
            event = create_internal_test_event(db, campaign, draft, user.id)
            self.assertEqual(event.recipient, APPROVED_INTERNAL_RECIPIENT)
            self.assertTrue(event.dry_run)
            self.assertEqual(event.status, 'internal_test_prepared')

            other_draft = OutreachDraft(company_id=other.id, campaign_id=other_campaign.id, lead_key=draft.lead_key, lead_email=draft.lead_email, business='Other', subject='x', body='x', status='draft_approved')
            db.add(other_draft); db.flush()
            self.assertIn('draft_company_campaign_mismatch', send_blockers(db, campaign, other_draft, internal_test=True))
        finally:
            db.close()


    def test_sender_verification_and_readiness_use_human_blockers(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.sender_name = 'Voryx'
            settings.sender_email = 'voryxio@gmail.com'
            settings.reply_to_email = 'voryxio@gmail.com'
            settings.physical_mailing_address = '123 QA St'
            settings.compliance_acknowledged = True
            db.add(settings); db.flush()
            readiness = outreach_readiness(db, campaign, settings, [])
            self.assertTrue(sender_verification('voryxio@gmail.com')['verified'])
            self.assertFalse(readiness['can_enable_prospect_sending'])
            self.assertIn('Prospect sending is OFF', ' '.join(readiness['human_blockers']))
        finally:
            db.close()


    def test_controlled_batch_preview_and_prepare_are_dry_run_only(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.sender_name = 'Voryx'
            settings.sender_email = 'voryxio@gmail.com'
            settings.reply_to_email = 'voryxio@gmail.com'
            settings.physical_mailing_address = '123 QA St'
            settings.compliance_acknowledged = True
            settings.prospect_sending_enabled = True
            settings.allowed_sending_days = []
            settings.allowed_sending_hours = {'start': '00:00', 'end': '23:59'}
            db.add(settings)
            approval = LeadApproval(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', email='owner@example.com', business='Cafe One', state='approved_for_outreach')
            draft = OutreachDraft(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', lead_email='owner@example.com', business='Cafe One', subject='Hello Cafe One', body='Draft body. Reply STOP to opt out.', status='draft_approved')
            db.add_all([approval, draft]); db.flush()

            preview = controlled_batch_preview(db, campaign)
            self.assertEqual(preview['coverage']['approved_leads'], 1)
            self.assertEqual(preview['coverage']['approved_drafts'], 1)
            self.assertEqual(preview['coverage']['ready_to_send'], 1)
            self.assertEqual(preview['prospect_emails_sent'], 0)
            self.assertTrue(preview['can_send_controlled_batch'])

            prepared = prepare_controlled_batch(db, campaign, user.id, dry_run=True)
            self.assertEqual(prepared['prospect_emails_sent'], 0)
            self.assertEqual(db.query(OutreachEvent).filter(OutreachEvent.status == 'prepared_dry_run').count(), 1)
            self.assertEqual(db.query(Job).filter(Job.task_type == 'Controlled Outreach Batch').count(), 1)
            with self.assertRaises(ValueError):
                prepare_controlled_batch(db, campaign, user.id, dry_run=False)
            with self.assertRaises(ValueError) as missing_confirmation:
                send_real_controlled_batch(db, campaign, user.id, confirmation='', send_one=True, process_now=False)
            self.assertIn('real_send_confirmed', str(missing_confirmation.exception))
        finally:
            db.close()

    def test_dry_run_prepare_works_outside_approved_sending_window(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.sender_name = 'Voryx'
            settings.sender_email = 'voryxio@gmail.com'
            settings.reply_to_email = 'voryxio@gmail.com'
            settings.physical_mailing_address = '123 QA St'
            settings.compliance_acknowledged = True
            settings.prospect_sending_enabled = True
            settings.allowed_sending_days = ['Sunday']
            settings.allowed_sending_hours = {'start': '00:00', 'end': '00:01'}
            db.add(settings)
            db.add(LeadApproval(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', email='owner@example.com', business='Cafe One', state='approved_for_outreach'))
            db.add(OutreachDraft(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', lead_email='owner@example.com', business='Cafe One', subject='Hello Cafe One', body='Draft body. Reply STOP to opt out.', status='draft_approved'))
            db.flush()

            preview = controlled_batch_preview(db, campaign)
            self.assertFalse(preview['can_send_controlled_batch'])
            self.assertIn('Outside the approved sending day/hour window.', preview['blockers'])
            prepared = prepare_controlled_batch(db, campaign, user.id, dry_run=True)

            self.assertEqual(prepared['prospect_emails_sent'], 0)
            self.assertEqual(prepared['coverage']['selected_for_batch'], 1)
            self.assertEqual(db.query(OutreachEvent).filter(OutreachEvent.status == 'prepared_dry_run').count(), 1)
            self.assertEqual(db.query(Job).filter(Job.task_type == 'Controlled Outreach Batch').count(), 1)
        finally:
            db.close()

    def test_real_send_blocks_outside_approved_sending_date_range(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.sender_name = 'Voryx'
            settings.sender_email = 'voryxio@gmail.com'
            settings.reply_to_email = 'voryxio@gmail.com'
            settings.physical_mailing_address = '123 QA St'
            settings.compliance_acknowledged = True
            settings.prospect_sending_enabled = True
            settings.allowed_sending_days = []
            settings.allowed_sending_hours = {'start': '00:00', 'end': '23:59'}
            settings.allowed_sending_start_date = '2099-01-01'
            settings.allowed_sending_end_date = '2099-12-31'
            db.add(settings)
            db.add(LeadApproval(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', email='owner@example.com', business='Cafe One', state='approved_for_outreach'))
            db.add(OutreachDraft(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', lead_email='owner@example.com', business='Cafe One', subject='Hello Cafe One', body='Draft body. Reply STOP to opt out.', status='draft_approved'))
            db.flush()

            preview = controlled_batch_preview(db, campaign)

            self.assertFalse(preview['can_send_controlled_batch'])
            self.assertIn('Outside the approved sending date range.', preview['blockers'])
            self.assertEqual(preview['window']['window']['dates']['start'], '2099-01-01')
            self.assertEqual(preview['prospect_emails_sent'], 0)
        finally:
            db.close()

    def test_invalid_approved_sending_date_fails_closed(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.sender_name = 'Voryx'
            settings.sender_email = 'voryxio@gmail.com'
            settings.reply_to_email = 'voryxio@gmail.com'
            settings.physical_mailing_address = '123 QA St'
            settings.compliance_acknowledged = True
            settings.prospect_sending_enabled = True
            settings.allowed_sending_start_date = 'not-a-date'
            db.add(settings)
            db.add(LeadApproval(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', email='owner@example.com', business='Cafe One', state='approved_for_outreach'))
            db.add(OutreachDraft(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', lead_email='owner@example.com', business='Cafe One', subject='Hello Cafe One', body='Draft body. Reply STOP to opt out.', status='draft_approved'))
            db.flush()

            preview = controlled_batch_preview(db, campaign)

            self.assertFalse(preview['can_send_controlled_batch'])
            self.assertIn('Allowed sending dates are invalid.', preview['blockers'])
            self.assertEqual(preview['prospect_emails_sent'], 0)
        finally:
            db.close()

    def test_schedule_next_window_records_job_and_evidence_without_sending(self):
        db = self.Session()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                user, company, campaign, _other, _other_campaign = self.seed(db)
                settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
                settings.sender_name = 'Voryx'
                settings.sender_email = 'voryxio@gmail.com'
                settings.reply_to_email = 'voryxio@gmail.com'
                settings.physical_mailing_address = '123 QA St'
                settings.compliance_acknowledged = True
                settings.prospect_sending_enabled = True
                settings.allowed_sending_days = []
                settings.allowed_sending_hours = {'start': '00:00', 'end': '23:59'}
                db.add(settings)
                db.add(LeadApproval(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', email='owner@example.com', business='Cafe One', state='approved_for_outreach'))
                db.add(OutreachDraft(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', lead_email='owner@example.com', business='Cafe One', subject='Hello Cafe One', body='Draft body. Reply STOP to opt out.', status='draft_approved'))
                db.flush()

                from app.core.config import settings as app_settings
                original_path = app_settings.hermes_data_path
                app_settings.hermes_data_path = tmp
                try:
                    result = schedule_controlled_batch_next_window(db, campaign, user.id, limit=5)
                finally:
                    app_settings.hermes_data_path = original_path

                self.assertEqual(result['prospect_emails_sent'], 0)
                self.assertEqual(result['selected_recipients'], 1)
                job = db.get(Job, result['job_id'])
                self.assertEqual(job.status.value, 'Queued')
                self.assertEqual(job.evidence_type, 'controlled_batch_schedule')
                self.assertTrue(job.source_output_path)
            finally:
                db.close()

    def test_bulk_approve_generated_drafts_for_approved_leads(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            approval = LeadApproval(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', email='owner@example.com', business='Cafe One', state='approved_for_outreach')
            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.unsubscribe_text = 'Reply STOP to opt out.'
            db.add(settings)
            draft = OutreachDraft(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', lead_email='owner@example.com', business='Cafe One', subject='Hello Cafe One', body='Draft body without footer.', status='draft_created')
            db.add_all([approval, draft]); db.flush()

            result = bulk_update_drafts(db, campaign, user.id, action='approve_all_generated')

            self.assertEqual(result['updated'], 1)
            self.assertEqual(draft.status, 'draft_approved')
            self.assertEqual(draft.approved_by, user.id)
            self.assertIn('Reply STOP to opt out.', draft.body)
            self.assertEqual(result['prospect_emails_sent'], 0)
        finally:
            db.close()

    def test_bulk_approve_repairs_already_approved_draft_footer(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.unsubscribe_text = 'Reply STOP to opt out.'
            approval = LeadApproval(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', email='owner@example.com', business='Cafe One', state='approved_for_outreach')
            draft = OutreachDraft(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', lead_email='owner@example.com', business='Cafe One', subject='Hello Cafe One', body='Approved body missing footer.', status='draft_approved', approved_by=user.id, approved_at=datetime.utcnow())
            db.add_all([settings, approval, draft]); db.flush()

            result = bulk_update_drafts(db, campaign, user.id, action='approve_all_generated')

            self.assertEqual(result['updated'], 1)
            self.assertEqual(draft.status, 'draft_approved')
            self.assertIn('Reply STOP to opt out.', draft.body)
            self.assertEqual(result['prospect_emails_sent'], 0)
            second = bulk_update_drafts(db, campaign, user.id, action='approve_all_generated')
            self.assertEqual(second['updated'], 0)
        finally:
            db.close()

    def test_draft_generation_uses_company_unsubscribe_text(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.unsubscribe_text = 'Reply STOP to opt out.'
            db.add(settings)
            item = review_items_from_rows(db, campaign, [{'Business Name': 'A Cafe', 'Public Email': 'a@a-cafe.example'}], 'source')[0]
            upsert_approval(db, campaign, item, 'approved_for_outreach', user.id)

            draft = generate_draft_for_item(db, campaign, company, {**item, 'state': 'approved_for_outreach', 'can_send': True})

            self.assertIn('Reply STOP to opt out.', draft.body)
            self.assertNotIn('Reply STOP and I will not contact you again.', draft.body)
        finally:
            db.close()

    def test_batch_preview_blocks_approved_draft_missing_configured_unsubscribe(self):
        db = self.Session()
        try:
            user, company, campaign, _other, _other_campaign = self.seed(db)
            settings = CompanyOutreachSettings(company_id=company.id, **{k: v for k, v in default_outreach_settings(company.id).items() if k != 'company_id'})
            settings.sender_name = 'Voryx'
            settings.sender_email = 'voryxio@gmail.com'
            settings.reply_to_email = 'voryxio@gmail.com'
            settings.physical_mailing_address = '123 QA St'
            settings.compliance_acknowledged = True
            settings.prospect_sending_enabled = True
            settings.allowed_sending_days = []
            settings.allowed_sending_hours = {'start': '00:00', 'end': '23:59'}
            settings.unsubscribe_text = 'Reply STOP to opt out.'
            db.add(settings)
            db.add(LeadApproval(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', email='owner@example.com', business='Cafe One', state='approved_for_outreach'))
            db.add(OutreachDraft(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', lead_email='owner@example.com', business='Cafe One', subject='Hello Cafe One', body='Draft body without required footer.', status='draft_approved'))
            db.flush()

            preview = controlled_batch_preview(db, campaign)

            self.assertFalse(preview['can_send_controlled_batch'])
            self.assertEqual(preview['coverage']['ready_to_send'], 0)
            self.assertEqual(preview['blocked_recipients'][0]['reasons'], ['draft_missing_unsubscribe_text'])
            self.assertEqual(preview['prospect_emails_sent'], 0)
        finally:
            db.close()

    def test_body_with_unsubscribe_is_idempotent(self):
        body = body_with_unsubscribe('Hello', 'Reply STOP to opt out.')
        self.assertEqual(body, 'Hello\n\nReply STOP to opt out.')
        self.assertEqual(body_with_unsubscribe(body, 'Reply STOP to opt out.'), body)

    def test_controlled_outreach_receipt_required_before_sent_state(self):
        db = self.Session()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                user, company, campaign, _other, _other_campaign = self.seed(db)
                approval = LeadApproval(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', email='owner@example.com', business='Cafe One', state='approved_for_outreach')
                event = OutreachEvent(event_id='event-1', company_id=company.id, campaign_id=campaign.id, recipient='owner@example.com', business='Cafe One', subject='Hello', status='queued_by_provider', provider='himalaya', dry_run=False)
                db.add_all([approval, event]); db.flush()
                job, queued = enqueue_controlled_outreach_delivery(
                    db,
                    campaign_id=campaign.id,
                    company_id=company.id,
                    employee_id=None,
                    lead_key='lead-1',
                    draft_id='draft-1',
                    recipient='owner@example.com',
                    business='Cafe One',
                    subject='Hello',
                    body='Body. Reply STOP to opt out.',
                    sender_email='voryxio@gmail.com',
                    reply_to_email='voryxio@gmail.com',
                    unsubscribe_text='Reply STOP to opt out.',
                    requested_by=user.id,
                    batch_id='batch-1',
                    event_id=event.event_id,
                    data_path=tmp,
                )
                self.assertIsNone(event.message_id)
                self.assertNotEqual(event.status, 'sent')
                receipt = {
                    'request_id': queued['request']['request_id'],
                    'job_id': job.id,
                    'event_id': event.event_id,
                    'status': 'sent',
                    'delivery_status': 'sent',
                    'recipient': 'owner@example.com',
                    'provider_message_id': '<receipt-1@voryx.ca>',
                    'sent_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'evidence_type': 'rfc_message_id',
                }
                (queue_root(tmp) / 'receipts').mkdir(parents=True, exist_ok=True)
                (queue_root(tmp) / 'receipts' / f"{queued['request']['request_id']}.json").write_text(json.dumps(receipt), encoding='utf-8')

                ingest_internal_mail_receipts(db, data_path=tmp)

                self.assertEqual(job.provider_message_id, '<receipt-1@voryx.ca>')
                self.assertEqual(event.status, 'sent')
                self.assertEqual(event.message_id, '<receipt-1@voryx.ca>')
                self.assertEqual(approval.state, 'sent')
            finally:
                db.close()

    def test_outreach_settings_validate_sender_and_compliance(self):
        missing = validate_outreach_settings(None, prospect=True)
        self.assertIn('sender_name', missing)
        data = default_outreach_settings('company-a')
        data.update({
            'sender_name': 'QA',
            'sender_email': 'not-approved@example.com',
            'reply_to_email': APPROVED_INTERNAL_RECIPIENT,
            'physical_mailing_address': '123 QA St',
            'approved_sender_connected': True,
            'compliance_acknowledged': True,
        })
        blockers = validate_outreach_settings(data, prospect=True)
        self.assertIn('sender_email_not_approved', blockers)
        self.assertIn('prospect_sending_enabled', blockers)


if __name__ == '__main__':
    unittest.main()
