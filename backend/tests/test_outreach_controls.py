import unittest
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
    controlled_batch_preview,
    create_internal_test_event,
    default_outreach_settings,
    generate_draft_for_item,
    review_items_from_rows,
    send_blockers,
    upsert_approval,
    validate_outreach_settings,
    outreach_readiness,
    prepare_controlled_batch,
    sender_verification,
)


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
            self.assertIn('Reply STOP', draft.body)
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
            draft = OutreachDraft(company_id=company.id, campaign_id=campaign.id, lead_key='lead-1', lead_email='owner@example.com', business='Cafe One', subject='Hello Cafe One', body='Draft body. Reply STOP.', status='draft_approved')
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
