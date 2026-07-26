import asyncio
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.base import Base
from app.models.entities import (
    CallComplianceItem,
    CallScriptVersion,
    Campaign,
    Company,
    ConsentedCallingLead,
    Role,
    User,
)
from app.services.call_script_studio import (
    ALLSTATE_CAMPAIGN_ID,
    ALLSTATE_COMPANY_ID,
    PILOT_CONFIRMATION,
    approve_locked_content,
    approve_pilot_lead,
    create_draft,
    ensure_compliance_items,
    ensure_script_studio,
    evaluate_lead,
    import_consented_leads,
    publish_script,
    recipient_in_calling_window,
    retell_node_patch,
    run_draft_tests,
    update_draft,
)


class CallScriptStudioTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def seed(self, db):
        db.add(Company(id=ALLSTATE_COMPANY_ID, name='Allstate'))
        db.add(Campaign(id=ALLSTATE_CAMPAIGN_ID, company_id=ALLSTATE_COMPANY_ID, name='Quote Calling'))
        user = User(id='admin', email='admin@example.com', password_hash='x', role=Role.admin)
        db.add(user)
        db.flush()
        return user

    def approve_checklist(self, db):
        for item in ensure_compliance_items(db):
            item.status = 'approved'
            item.approver = 'Compliance Owner'
            item.evidence = 'evidence://qa'
            item.effective_at = datetime(2026, 1, 1)

    def eligible_row(self):
        return {
            'first_name': 'QA',
            'phone_number': '+14165550123',
            'timezone': 'America/Toronto',
            'province': 'Ontario',
            'product_interest': 'Auto',
            'consent_status': 'verified',
            'consent_type': 'express_automated_call',
            'consent_source': 'signed QA form',
            'consent_text': 'I agree to an automated call by or on behalf of Allstate.',
            'consent_timestamp': '2026-07-25T12:00:00',
            'consented_number': '+14165550123',
            'automated_or_synthesized_call_consent': True,
            'organization_authorized': True,
            'consent_proof': 'evidence://signed-qa-form',
            'consent_withdrawn': False,
            'dncl_status': 'clear',
            'internal_dnc_clear': True,
            'suppression_clear': True,
        }

    def test_baseline_and_draft_are_versioned(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'), patch.object(settings, 'retell_agent_version', '0'):
            user = self.seed(db)
            published = ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            self.assertEqual((published.version_number, published.status), (1, 'published'))
            self.assertEqual((draft.version_number, draft.status), (2, 'draft'))
            self.assertEqual(draft.retell_agent_id, 'agent-fixed')
            self.assertEqual(len(ensure_compliance_items(db)), 19)

    def test_published_version_cannot_be_edited(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            published = ensure_script_studio(db, user.id)
            with self.assertRaisesRegex(ValueError, 'cannot be edited'):
                update_draft(db, published, {'purpose_statement': 'Changed'}, user)

    def test_node_mapping_changes_only_target_node(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            update_draft(db, draft, {'purpose_statement': 'New approved purpose'}, user)
            live = {'nodes': [
                {'id': 'opening', 'instruction': {'type': 'prompt', 'text': 'old opening'}},
                {'id': 'purpose', 'instruction': {'type': 'prompt', 'text': 'old purpose'}},
            ]}
            patch_payload = retell_node_patch(draft, live)
            nodes = {item['id']: item for item in patch_payload['nodes']}
            self.assertEqual(nodes['opening']['instruction']['text'], 'old opening')
            self.assertEqual(nodes['purpose']['instruction']['text'], 'New approved purpose')

    def test_all_required_draft_scenarios_pass(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            result = run_draft_tests(db, draft, user.id)
            self.assertTrue(result['passed'])
            self.assertEqual(result['required_scenarios_passed'], 15)
            self.assertGreaterEqual(result['sales_score'], 8)
            self.assertEqual(result['missing_retell_tools'], [])
            self.assertEqual(result['missing_dynamic_variables'], [])

    def test_locked_change_requires_separate_compliance_approval(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            update_draft(db, draft, {'compliance_content': {**draft.compliance_content, 'proposed_change': 'QA proposal'}}, user)
            self.assertIsNone(draft.compliance_approved_at)
            approve_locked_content(db, draft, user, 'Compliance QA approval')
            self.assertIsNotNone(draft.compliance_approved_at)

    def test_publish_updates_same_flow_and_never_creates_agent_or_flow(self):
        with self.Session() as db, \
             patch.object(settings, 'retell_agent_id', 'agent-fixed'), \
             patch.object(settings, 'retell_permanent_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            update_draft(db, draft, {'purpose_statement': 'Changed purpose'}, user)
            draft.test_result = {'passed': True}
            draft.status = 'approved'
            provider = AsyncMock()
            provider.get_agent.side_effect = [
                {
                    'agent_id': 'agent-fixed',
                    'version': 0,
                    'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': draft.conversation_flow_id, 'version': 0},
                },
                {
                    'agent_id': 'agent-fixed',
                    'version': 1,
                    'is_published': True,
                    'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': draft.conversation_flow_id, 'version': 1},
                },
            ]
            provider.create_agent_version.return_value = {
                'agent_id': 'agent-fixed',
                'version': 1,
                'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': draft.conversation_flow_id, 'version': 1},
            }
            provider.get_conversation_flow.side_effect = [
                {'conversation_flow_id': draft.conversation_flow_id, 'version': 1, 'nodes': [{'id': 'purpose', 'instruction': {'type': 'prompt', 'text': 'old'}}]},
                {'conversation_flow_id': draft.conversation_flow_id, 'version': 1, 'nodes': [{'id': 'purpose', 'instruction': {'type': 'prompt', 'text': 'Changed purpose'}}]},
            ]
            provider.update_conversation_flow.return_value = {'conversation_flow_id': draft.conversation_flow_id, 'version': 1}
            provider.get_phone_number.return_value = {
                'inbound_agents': [],
                'outbound_agents': [{'agent_id': 'agent-fixed', 'agent_version': 1, 'weight': 1}],
            }
            result = asyncio.run(publish_script(db, draft, user, provider))
            self.assertFalse(result['new_agent_created'])
            self.assertFalse(result['new_conversation_flow_created'])
            provider.update_conversation_flow.assert_awaited_once()
            provider.create_agent_version.assert_awaited_once_with('agent-fixed', 0)
            provider.publish_agent_version.assert_awaited_once_with('agent-fixed', 1)
            provider.update_phone_number_assignment.assert_awaited_once()

    def test_exact_number_and_automated_consent_are_required(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            self.approve_checklist(db)
            row = self.eligible_row()
            row['consented_number'] = '+14165550999'
            row['automated_or_synthesized_call_consent'] = False
            result = import_consented_leads(db, [row], user.id)
            lead = db.get(ConsentedCallingLead, result['lead_ids'][0])
            _, reasons = evaluate_lead(db, lead, now=datetime(2026, 7, 27, 15, 0))
            self.assertIn('Called number does not exactly match consented number', reasons)
            self.assertIn('Automated or synthesized call consent is missing', reasons)

    def test_calling_hours_use_recipient_local_time(self):
        allowed, _ = recipient_in_calling_window('America/Toronto', datetime.fromisoformat('2026-07-27T15:00:00+00:00'))
        blocked_sunday, _ = recipient_in_calling_window('America/Toronto', datetime.fromisoformat('2026-07-26T15:00:00+00:00'))
        self.assertTrue(allowed)
        self.assertFalse(blocked_sunday)

    def test_ready_lead_can_be_individually_approved_but_not_called_automatically(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'), patch.object(settings, 'retell_agent_version', '0'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            self.approve_checklist(db)
            result = import_consented_leads(db, [self.eligible_row()], user.id)
            lead = db.get(ConsentedCallingLead, result['lead_ids'][0])
            status, reasons = evaluate_lead(db, lead, now=datetime.fromisoformat('2026-07-27T15:00:00+00:00'))
            self.assertEqual((status, reasons), ('Ready for pilot', []))
            entry = approve_pilot_lead(db, lead, user, now=datetime.fromisoformat('2026-07-27T15:00:00+00:00'))
            self.assertEqual(entry.status, 'approved')
            self.assertIsNone(entry.call_attempt_id)
            self.assertEqual(PILOT_CONFIRMATION, 'PLACE APPROVED CONSENTED LEAD CALL')


if __name__ == '__main__':
    unittest.main()
