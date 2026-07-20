import hashlib
import hmac
import unittest

from app.services.calling import (
    ALLSTATE_BEGIN_MESSAGE,
    REQUIRED_DYNAMIC_VARIABLES,
    MockCallingProvider,
    RetellCallingProvider,
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

    def test_preview_begin_message_is_not_generic(self):
        preview = internal_test_preview_payload('attempt-1')
        self.assertEqual(preview['begin_message'], ALLSTATE_BEGIN_MESSAGE)
        self.assertIn('Ava', preview['begin_message'])
        self.assertIn('Himanshu Soni', preview['begin_message'])
        self.assertIn('Allstate Sales Agent', preview['begin_message'])
        self.assertIn('internal test call', preview['begin_message'])
        self.assertEqual(preview['missing_dynamic_variables'], [])


if __name__ == '__main__':
    unittest.main()
