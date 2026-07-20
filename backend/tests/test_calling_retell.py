import hashlib
import hmac
import unittest

from app.services.calling import MockCallingProvider, RetellCallingProvider, normalize_phone, valid_us_ca_e164


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


if __name__ == '__main__':
    unittest.main()
