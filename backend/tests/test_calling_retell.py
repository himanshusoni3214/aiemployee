import hashlib
import hmac
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.entities import CallCampaignSettings
from app.services.calling import (
    ALLSTATE_BEGIN_MESSAGE,
    ALLSTATE_RECORDING_DISCLOSURE,
    ALLSTATE_REFINED_PROMPT,
    REQUIRED_DYNAMIC_VARIABLES,
    MockCallingProvider,
    RetellCallingProvider,
    ensure_allstate_calling_campaign,
    internal_test_dynamic_variables,
    internal_test_preview_payload,
    normalize_phone,
    valid_us_ca_e164,
)


class CallingRetellTests(unittest.TestCase):
    def test_normalizes_us_canada_number(self):
        self.assertEqual(normalize_phone('(416) 555-1234'), '+14165551234')
        self.assertEqual(normalize_phone('1-647-555-9999'), '+16475559999')

    def test_rejects_non_us_canada_e164(self):
        self.assertTrue(valid_us_ca_e164('+14165551234'))
        self.assertFalse(valid_us_ca_e164('+911234567890'))
        self.assertFalse(valid_us_ca_e164('+10165551234'))

    def test_retell_signature_accepts_hex_and_sha_prefixed(self):
        raw = b'{"event":"call_started","call":{"call_id":"call_test"}}'
        key = 'webhook-secret'
        digest = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
        provider = RetellCallingProvider(api_key='', webhook_key=key)
        self.assertTrue(provider.verify_webhook(raw, digest))
        self.assertTrue(provider.verify_webhook(raw, f'sha256={digest}'))
        self.assertFalse(provider.verify_webhook(raw, 'bad-signature'))

    def test_mock_provider_is_explicitly_mocked(self):
        provider = MockCallingProvider()
        self.assertTrue(provider.verify_webhook(b'{}', 'test-valid'))
        self.assertFalse(provider.verify_webhook(b'{}', 'wrong'))

    def test_internal_test_dynamic_variables_are_allstate_specific(self):
        values = internal_test_dynamic_variables('attempt-1', {'recipient_name': 'Himanshu'})
        self.assertEqual(sorted(values), sorted(REQUIRED_DYNAMIC_VARIABLES))
        self.assertEqual(values['assistant_name'], 'Ava')
        self.assertEqual(values['agent_name'], 'Himanshu Soni')
        self.assertEqual(values['agent_role'], 'Allstate Sales Agent')
        self.assertEqual(values['agency_location'], 'Scarborough, Ontario')
        self.assertIn('insurance quote appointment', values['call_purpose'])
        self.assertEqual(values['internal_test'], 'true')
        self.assertEqual(values['recording_disclosure_enabled'], 'true')
        self.assertEqual(values['recording_disclosure'], ALLSTATE_RECORDING_DISCLOSURE)
        self.assertEqual(values['consent_validated_for_called_number'], 'true')

    def test_preview_begin_message_is_not_generic(self):
        preview = internal_test_preview_payload('attempt-1')
        self.assertEqual(preview['begin_message'], ALLSTATE_BEGIN_MESSAGE)
        self.assertIn('Ava', preview['begin_message'])
        self.assertIn('Himanshu Soni', preview['begin_message'])
        self.assertIn('Allstate Sales Agent', preview['begin_message'])
        self.assertIn('test of his insurance quote appointment workflow', preview['begin_message'])
        self.assertNotIn('AI assistant', preview['begin_message'])
        self.assertEqual(preview['missing_dynamic_variables'], [])

    def test_refined_prompt_answers_automation_truthfully(self):
        self.assertIn("I'm an automated calling assistant", ALLSTATE_REFINED_PROMPT)
        self.assertIn('Do not claim or imply that you are human', ALLSTATE_REFINED_PROMPT)
        self.assertIn('{{consent_validated_for_called_number}}', ALLSTATE_REFINED_PROMPT)
        self.assertIn('recording_objection', ALLSTATE_REFINED_PROMPT)
        self.assertIn('Do not say', ALLSTATE_REFINED_PROMPT)

    def test_existing_calling_campaign_provisioning_is_read_idempotent(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as db:
            ensure_allstate_calling_campaign(db)
            db.commit()
            row = db.scalar(select(CallCampaignSettings))
            initial_updated_at = row.updated_at

            ensure_allstate_calling_campaign(db)
            db.flush()

            self.assertEqual(row.updated_at, initial_updated_at)


if __name__ == '__main__':
    unittest.main()
