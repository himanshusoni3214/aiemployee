import hashlib
import hmac
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.core.config import settings
from app.models.entities import CallAppointment, CallAttempt, CallCampaignSettings, Company, Campaign, Role, User
from app.services.calling import (
    ALLSTATE_CAMPAIGN_ID,
    ALLSTATE_COMPANY_ID,
    ALLSTATE_BEGIN_MESSAGE,
    ALLSTATE_RECORDING_DISCLOSURE,
    ALLSTATE_REFINED_PROMPT,
    CONVERSATION_FLOW_INTERNAL_CONFIRMATION,
    REQUIRED_DYNAMIC_VARIABLES,
    MockCallingProvider,
    RetellCallingProvider,
    authorize_internal_test_call,
    ensure_allstate_calling_campaign,
    internal_test_dynamic_variables,
    internal_test_preview_payload,
    normalize_phone,
    quote_appointment_slots,
    valid_us_ca_e164,
    _sync_attempt_from_call_payload,
)


class CallingRetellTests(unittest.TestCase):
    def provider_request(self, response: httpx.Response):
        provider = RetellCallingProvider(api_key='secret-test-key')
        with patch('app.services.calling.httpx.AsyncClient') as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.request = AsyncMock(return_value=response)
            return __import__('asyncio').run(provider._request('POST', '/test'))

    def test_retell_request_parses_json_success(self):
        response = httpx.Response(
            200,
            json={'ok': True},
            headers={'content-type': 'application/json'},
            request=httpx.Request('POST', 'https://api.retellai.com/test'),
        )
        self.assertEqual(self.provider_request(response), {'ok': True})

    def test_retell_request_accepts_empty_200(self):
        response = httpx.Response(
            200,
            content=b'',
            request=httpx.Request('POST', 'https://api.retellai.com/test'),
        )
        self.assertEqual(self.provider_request(response), {})

    def test_retell_request_accepts_empty_204(self):
        response = httpx.Response(
            204,
            content=b'',
            request=httpx.Request('POST', 'https://api.retellai.com/test'),
        )
        self.assertEqual(self.provider_request(response), {})

    def test_retell_request_returns_safe_text_success(self):
        response = httpx.Response(
            200,
            text='published',
            headers={'content-type': 'text/plain'},
            request=httpx.Request('POST', 'https://api.retellai.com/test'),
        )
        result = self.provider_request(response)
        self.assertEqual(result['status_code'], 200)
        self.assertEqual(result['response_text'], 'published')

    def test_retell_request_preserves_json_error(self):
        response = httpx.Response(
            400,
            json={'error': 'invalid version'},
            headers={'content-type': 'application/json'},
            request=httpx.Request('POST', 'https://api.retellai.com/test'),
        )
        with self.assertRaisesRegex(Exception, 'invalid version'):
            self.provider_request(response)

    def test_retell_request_preserves_non_json_error(self):
        response = httpx.Response(
            500,
            text='upstream unavailable',
            headers={'content-type': 'text/plain'},
            request=httpx.Request('POST', 'https://api.retellai.com/test'),
        )
        with self.assertRaisesRegex(Exception, 'upstream unavailable'):
            self.provider_request(response)

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
        self.assertIn("Ontario's auto insurance rules changed on July 1, 2026", ALLSTATE_REFINED_PROMPT)
        self.assertIn('Allow only one respectful reframe', ALLSTATE_REFINED_PROMPT)
        self.assertIn('second-opinion conversation', ALLSTATE_REFINED_PROMPT)

    def test_conversation_flow_confirmation_replaces_legacy_gate(self):
        self.assertEqual(CONVERSATION_FLOW_INTERNAL_CONFIRMATION, 'PLACE CONVERSATION-FLOW INTERNAL TEST CALL')

    def test_atomic_allowlist_authorization_requires_exact_confirmation_without_placing_call(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        provider = MockCallingProvider()
        with session_factory() as db, patch.object(settings, 'retell_internal_test_mode', True):
            user = User(
                id='admin',
                email='admin@example.com',
                password_hash='x',
                role=Role.admin,
            )
            db.add(user)
            ensure_allstate_calling_campaign(db, user.id)
            db.flush()
            blocked, blockers, _ = __import__('asyncio').run(authorize_internal_test_call(
                db,
                user,
                '+14165550123',
                'wrong confirmation',
                provider,
                allow_atomic_allowlist=True,
            ))
            allowed, allowed_blockers, _ = __import__('asyncio').run(authorize_internal_test_call(
                db,
                user,
                '+14165550123',
                CONVERSATION_FLOW_INTERNAL_CONFIRMATION,
                provider,
                allow_atomic_allowlist=True,
            ))
            not_allowlisted, allowlist_blockers, _ = __import__('asyncio').run(authorize_internal_test_call(
                db,
                user,
                '+14165550123',
                CONVERSATION_FLOW_INTERNAL_CONFIRMATION,
                provider,
                allow_atomic_allowlist=False,
            ))
            self.assertFalse(blocked)
            self.assertIn('Confirmation must exactly match', ' '.join(blockers))
            self.assertTrue(allowed)
            self.assertEqual(allowed_blockers, [])
            self.assertFalse(not_allowlisted)
            self.assertIn('Phone number is not on the internal-test allowlist', allowlist_blockers)
            self.assertEqual(db.scalars(select(CallAttempt)).all(), [])

    def test_provider_has_no_agent_creation_method(self):
        self.assertFalse(hasattr(RetellCallingProvider, 'create_agent'))

    def test_sync_persists_retell_provider_cost(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as db:
            db.add(Company(id='co', name='Company'))
            db.add(Campaign(id='ca', company_id='co', name='Campaign'))
            attempt = CallAttempt(company_id='co', campaign_id='ca', to_number='+14165551234')
            db.add(attempt)
            db.flush()
            _sync_attempt_from_call_payload(db, attempt, {
                'call_status': 'ended', 'agent_id': 'agent-fixed', 'agent_version': 4,
                'duration_ms': 120000, 'end_timestamp': 1784600000000,
                'voice_id': 'retell-Della',
                'call_cost': {'combined_cost': 24.2, 'currency': 'USD', 'products': [{'product': 'telephony', 'cost': 6.0}]},
            })
            self.assertEqual(attempt.provider_agent_version, 4)
            self.assertEqual(attempt.provider_cost_cents, 24.2)
            self.assertTrue(attempt.provider_cost_final)
            self.assertEqual(attempt.provider_voice_id, 'retell-Della')
            self.assertEqual(attempt.provider_cost_breakdown['products'][0]['product'], 'telephony')

    def test_health_blocks_agent_id_drift(self):
        provider = RetellCallingProvider(api_key='test')
        provider.get_agent = AsyncMock(return_value={'agent_id': 'changed', 'agent_name': 'Call Agent'})
        provider.get_phone_number = AsyncMock(return_value={'outbound_agents': [{'agent_id': 'changed', 'weight': 1}]})
        with patch.object(settings, 'retell_agent_id', 'changed'), \
             patch.object(settings, 'retell_permanent_agent_id', 'permanent'), \
             patch.object(settings, 'retell_from_number', '+14377475010'), \
             patch.object(settings, 'retell_internal_test_mode', True), \
             patch.object(settings, 'retell_tool_token', 'tool'), \
             patch.object(settings, 'retell_webhook_api_key', 'webhook'):
            health = __import__('asyncio').run(provider.health())
        self.assertFalse(health['internal_test_ready'])
        self.assertIn('RETELL_AGENT_ID does not match the locked permanent Retell agent', health['blockers'])

    def test_health_reads_the_exact_assigned_agent_version(self):
        provider = RetellCallingProvider(api_key='test')
        provider.get_agent = AsyncMock(side_effect=[
            {'agent_id': 'permanent', 'agent_name': 'Voryx Allstate Quote Appointment Assistant', 'version': 8, 'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': 'flow', 'version': 8}},
            {'agent_id': 'legacy', 'agent_name': 'Legacy'},
        ])
        provider.get_phone_number = AsyncMock(return_value={'inbound_agents': [], 'outbound_agents': [{'agent_id': 'permanent', 'agent_version': 8, 'weight': 1}]})
        with patch.object(settings, 'retell_agent_id', 'permanent'), \
             patch.object(settings, 'retell_permanent_agent_id', 'permanent'), \
             patch.object(settings, 'retell_legacy_agent_id', 'legacy'), \
             patch.object(settings, 'retell_from_number', '+14377475010'), \
             patch.object(settings, 'retell_tool_token', 'tool'), \
             patch.object(settings, 'retell_webhook_api_key', 'webhook'):
            health = __import__('asyncio').run(provider.health(8))
        provider.get_agent.assert_any_await('permanent', 8)
        self.assertEqual(health['response_engine']['version'], 8)
        self.assertTrue(health['outbound_agent_correctly_assigned'])

    def test_successor_health_requires_conversation_flow(self):
        provider = RetellCallingProvider(api_key='test')
        provider.get_agent = AsyncMock(side_effect=[
            {
                'agent_id': 'permanent', 'agent_name': 'Voryx Allstate Quote Appointment Assistant',
                'version': 0, 'response_engine': {'type': 'retell-llm', 'llm_id': 'wrong'},
            },
            {'agent_id': 'legacy', 'agent_name': 'LEGACY - Voryx Allstate Retell-LLM - DO NOT USE'},
        ])
        provider.get_phone_number = AsyncMock(return_value={'outbound_agents': [{'agent_id': 'permanent', 'weight': 1}]})
        with patch.object(settings, 'retell_agent_id', 'permanent'), \
             patch.object(settings, 'retell_permanent_agent_id', 'permanent'), \
             patch.object(settings, 'retell_legacy_agent_id', 'legacy'), \
             patch.object(settings, 'retell_agent_version', '0'), \
             patch.object(settings, 'retell_from_number', '+14377475010'), \
             patch.object(settings, 'retell_internal_test_mode', True), \
             patch.object(settings, 'retell_tool_token', 'tool'), \
             patch.object(settings, 'retell_webhook_api_key', 'webhook'):
            health = __import__('asyncio').run(provider.health())
        self.assertFalse(health['internal_test_ready'])
        self.assertIn('Published Retell agent is not using the required Conversation Flow response engine', health['blockers'])

    def test_successor_health_requires_legacy_agent_to_remain_available(self):
        provider = RetellCallingProvider(api_key='test')
        provider.get_agent = AsyncMock(side_effect=[
            {
                'agent_id': 'successor',
                'agent_name': 'Voryx Allstate Quote Appointment Assistant',
                'version': 0,
                'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': 'flow'},
            },
            {'agent_id': 'legacy', 'agent_name': 'LEGACY - Voryx Allstate Retell-LLM - DO NOT USE'},
        ])
        provider.get_phone_number = AsyncMock(return_value={
            'inbound_agents': [],
            'outbound_agents': [{'agent_id': 'successor', 'agent_version': 0, 'weight': 1}],
        })
        with patch.object(settings, 'retell_agent_id', 'successor'), \
             patch.object(settings, 'retell_permanent_agent_id', 'successor'), \
             patch.object(settings, 'retell_legacy_agent_id', 'legacy'), \
             patch.object(settings, 'retell_agent_version', '0'), \
             patch.object(settings, 'retell_from_number', '+14377475010'), \
             patch.object(settings, 'retell_internal_test_mode', True), \
             patch.object(settings, 'retell_tool_token', 'tool'), \
             patch.object(settings, 'retell_webhook_api_key', 'webhook'):
            health = __import__('asyncio').run(provider.health())
        self.assertTrue(health['internal_test_ready'])
        self.assertTrue(health['legacy_agent_exists'])
        self.assertEqual(health['inbound_agents'], [])
        self.assertEqual(len(health['outbound_agents']), 1)

    def test_successor_health_rejects_inbound_or_version_drift(self):
        provider = RetellCallingProvider(api_key='test')
        provider.get_agent = AsyncMock(side_effect=[
            {
                'agent_id': 'successor',
                'agent_name': 'Voryx Allstate Quote Appointment Assistant',
                'version': 0,
                'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': 'flow'},
            },
            {'agent_id': 'legacy', 'agent_name': 'Legacy'},
        ])
        provider.get_phone_number = AsyncMock(return_value={
            'inbound_agents': [{'agent_id': 'successor', 'agent_version': 0, 'weight': 1}],
            'outbound_agents': [{'agent_id': 'successor', 'agent_version': 2, 'weight': 1}],
        })
        with patch.object(settings, 'retell_agent_id', 'successor'), \
             patch.object(settings, 'retell_permanent_agent_id', 'successor'), \
             patch.object(settings, 'retell_legacy_agent_id', 'legacy'), \
             patch.object(settings, 'retell_agent_version', '0'), \
             patch.object(settings, 'retell_from_number', '+14377475010'), \
             patch.object(settings, 'retell_internal_test_mode', True), \
             patch.object(settings, 'retell_tool_token', 'tool'), \
             patch.object(settings, 'retell_webhook_api_key', 'webhook'):
            health = __import__('asyncio').run(provider.health())
        self.assertFalse(health['internal_test_ready'])
        self.assertIn('Retell outbound number does not exactly match the configured agent, version, and weight', health['blockers'])
        self.assertIn('Retell inbound number assignment must remain empty', health['blockers'])

    def test_quote_slots_are_allstate_internal_only_and_skip_booked_slot(self):
        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        with session_factory() as db:
            db.add(Company(id=ALLSTATE_COMPANY_ID, name='Allstate'))
            db.add(Campaign(id=ALLSTATE_CAMPAIGN_ID, company_id=ALLSTATE_COMPANY_ID, name='Calling'))
            attempt = CallAttempt(
                id='attempt-1', company_id=ALLSTATE_COMPANY_ID, campaign_id=ALLSTATE_CAMPAIGN_ID,
                to_number='+14165551234', internal_test=True,
            )
            db.add(attempt)
            db.add(CallAppointment(
                call_attempt_id='attempt-1', start_time='2026-07-22 18:30',
                timezone='America/Toronto', status='confirmed',
            ))
            db.flush()
            result = quote_appointment_slots(db, {'voryx_call_attempt_id': 'attempt-1'}, datetime(2026, 7, 21, 12, 0))
            self.assertTrue(result['ok'])
            self.assertEqual(result['slots'][0]['date'], '2026-07-23')
            self.assertEqual(len(result['slots']), 2)

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
