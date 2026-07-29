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
    ConsentSourceProfile,
    Role,
    User,
)
from app.services.call_script_studio import (
    ALLSTATE_CAMPAIGN_ID,
    ALLSTATE_COMPANY_ID,
    DEFAULT_CONFIRMED_CONSENTED_INTRODUCTION,
    DEFAULT_CONFIRMED_INTERNAL_INTRODUCTION,
    DEFAULT_WRONG_PERSON_RESPONSE,
    OPENING_STYLE_CONFIRM_FIRST,
    PILOT_CONFIRMATION,
    approve_locked_content,
    approve_pilot_lead,
    apply_compliance_package,
    compliance_blocker_details,
    create_draft,
    create_consent_source_profile,
    ensure_compliance_items,
    ensure_script_studio,
    evaluate_lead,
    import_consented_leads,
    preview_simple_consent_rows,
    publish_script_changes,
    publish_script,
    recipient_in_calling_window,
    retell_node_patch,
    expected_retell_node_texts,
    verify_retell_node_texts,
    run_retell_opening_playground,
    run_draft_tests,
    script_content_hash,
    update_draft,
    validate_script_content,
)
from app.services.calling import MockCallingProvider


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

    def flow_payload(self, row, version=1, override=None):
        texts = expected_retell_node_texts(row)
        if override:
            texts.update(override)
        return {
            'conversation_flow_id': row.conversation_flow_id,
            'version': version,
            'nodes': [
                {'id': node_id, 'instruction': {'type': 'prompt', 'text': text}}
                for node_id, text in texts.items()
            ],
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
            live = self.flow_payload(draft, override={'opening': 'old opening', 'purpose': 'old purpose'})
            patch_payload = retell_node_patch(draft, live)
            nodes = {item['id']: item for item in patch_payload['nodes']}
            self.assertEqual(nodes['opening']['instruction']['text'], expected_retell_node_texts(draft)['opening'])
            self.assertEqual(nodes['purpose']['instruction']['text'], 'New approved purpose')

    def configure_confirm_first(self, draft):
        draft.opening_internal = 'Hi, is this {{customer_name}}?'
        draft.opening_consented = 'Hi, is this {{customer_name}}?'
        draft.voice_settings = {
            **(draft.voice_settings or {}),
            'opening_style': OPENING_STYLE_CONFIRM_FIRST,
            'confirmed_person_internal': DEFAULT_CONFIRMED_INTERNAL_INTRODUCTION,
            'confirmed_person_consented': DEFAULT_CONFIRMED_CONSENTED_INTRODUCTION,
            'wrong_person_response': DEFAULT_WRONG_PERSON_RESPONSE,
        }

    def test_confirm_first_node_patch_adds_stateful_identity_and_separate_endings(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            self.configure_confirm_first(draft)
            live = self.flow_payload(draft)
            live['nodes'] = [
                item for item in live['nodes']
                if item['id'] not in {'wrong_person_end', 'voicemail_end'}
            ]
            patched = retell_node_patch(draft, live)
            nodes = {item['id']: item for item in patched['nodes']}
            self.assertIn('confirmed-person introduction', nodes['purpose']['instruction']['text'])
            self.assertEqual(
                nodes['wrong_person_end']['instruction']['text'],
                DEFAULT_WRONG_PERSON_RESPONSE,
            )
            self.assertEqual(
                nodes['voicemail_end']['instruction']['text'],
                draft.voicemail_content,
            )
            edge_destinations = {
                item['destination_node_id']
                for item in nodes['opening']['edges']
            }
            self.assertIn('wrong_person_end', edge_destinations)
            self.assertIn('voicemail_end', edge_destinations)

    def test_stateful_playground_accepts_two_step_opening_without_literal_turn_one_identity(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            self.configure_confirm_first(draft)
            result = asyncio.run(
                run_retell_opening_playground(draft, MockCallingProvider(), agent_version=8)
            )
            self.assertTrue(result['passed'], result)
            internal = result['modes']['internal_test']
            self.assertEqual(internal['turns'][0]['text'], 'Hi, is this Himanshu?')
            self.assertNotIn('Ava', internal['turns'][0]['text'])
            checks = {item['key']: item['passed'] for item in result['checks']}
            self.assertTrue(checks['recipient_confirmation_present'])
            self.assertTrue(checks['assistant_identity_present_after_confirmation'])
            self.assertTrue(checks['himanshu_identity_present_after_confirmation'])
            self.assertTrue(checks['allstate_role_present_after_confirmation'])
            self.assertTrue(checks['reason_for_call_present'])
            self.assertEqual(result['real_phone_calls'], 0)
            self.assertEqual(result['live_tools_executed'], 0)

    def test_stateful_playground_reports_individual_missing_identity_check(self):
        class MissingAllstateProvider(MockCallingProvider):
            async def playground_completion(self, agent_id, version, payload):
                response = await super().playground_completion(agent_id, version, payload)
                messages = payload.get('messages') or []
                last_user = next(
                    (
                        str(item.get('content') or '').lower()
                        for item in reversed(messages)
                        if item.get('role') == 'user'
                    ),
                    '',
                )
                if 'yes, speaking' in last_user:
                    response['messages'] = [{
                        'role': 'agent',
                        'content': 'Hi Himanshu, this is Ava calling for Himanshu Soni. Do you have thirty seconds?',
                    }]
                return response

        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            self.configure_confirm_first(draft)
            result = asyncio.run(
                run_retell_opening_playground(draft, MissingAllstateProvider(), agent_version=8)
            )
            self.assertFalse(result['passed'])
            failed = {
                item['key']: item['failure']
                for item in result['modes']['internal_test']['checks']
                if not item['passed']
            }
            self.assertIn('allstate_role_present_after_confirmation', failed)
            self.assertIn('Allstate Sales Agent', failed['allstate_role_present_after_confirmation'])

    def test_recoverable_content_edit_preserves_existing_provider_draft_versions(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            draft.status = 'failed_recoverable'
            draft.publish_state = {
                'draft_agent_version': 8,
                'flow_version': 8,
                'base_agent_version': 7,
                'stage': 'flow_verified',
            }
            voice = {
                **draft.voice_settings,
                'opening_style': OPENING_STYLE_CONFIRM_FIRST,
            }
            update_draft(db, draft, {'voice_settings': voice}, user)
            self.assertEqual(draft.publish_state['draft_agent_version'], 8)
            self.assertEqual(draft.publish_state['flow_version'], 8)
            self.assertEqual(
                draft.publish_state['prior_partial_publish']['stage'],
                'flow_verified',
            )

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

    def test_content_hash_is_deterministic_and_changes_with_publishable_text(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            published = ensure_script_studio(db, user.id)
            first = script_content_hash(published)
            second = script_content_hash(published)
            values = {
                field: getattr(published, field)
                for field in (
                    'opening_internal', 'opening_consented', 'purpose_statement',
                    'discovery_content', 'objection_library', 'closing_library',
                    'voicemail_content', 'voice_settings', 'talking_points',
                    'compliance_content',
                )
            }
            values['opening_internal'] = (
                'Hi {{customer_name}}, this is Ava calling for an internal test. '
                'Do you have thirty seconds?'
            )
            self.assertEqual(first, second)
            self.assertNotEqual(first, script_content_hash(values))

    def test_edit_invalidates_stale_test_and_approval_hashes(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            result = run_draft_tests(db, draft, user.id)
            self.assertTrue(result['passed'])
            tested_hash = draft.tested_content_hash
            draft.approved_content_hash = tested_hash
            update_draft(db, draft, {
                'opening_internal': (
                    'Hi {{customer_name}}, this is Ava calling for an internal test. '
                    'Is this a good time for a brief check?'
                ),
            }, user)
            self.assertNotEqual(draft.content_hash, tested_hash)
            self.assertIsNone(draft.tested_content_hash)
            self.assertIsNone(draft.approved_content_hash)
            self.assertEqual(draft.test_result, {})
            self.assertEqual(draft.status, 'draft')

    def test_malformed_customer_name_placeholder_is_a_field_error(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            published = ensure_script_studio(db, user.id)
            values = {
                field: getattr(published, field)
                for field in (
                    'opening_internal', 'opening_consented', 'purpose_statement',
                    'discovery_content', 'objection_library', 'closing_library',
                    'voicemail_content', 'voice_settings', 'talking_points',
                    'compliance_content',
                )
            }
            values['opening_internal'] = 'Hi {customer_name}, this is Ava calling.)'
            errors = validate_script_content(values)
            self.assertIn('opening_internal', errors)
            self.assertIn(
                'Customer-name variable is malformed. Use {{customer_name}}, not {customer_name}.',
                errors['opening_internal'],
            )
            self.assertIn(
                'Internal-test opening contains an extra closing parenthesis.',
                errors['opening_internal'],
            )

    def test_one_click_publish_is_idempotent_for_exact_content_hash(self):
        with self.Session() as db, \
             patch.object(settings, 'retell_agent_id', 'agent-fixed'), \
             patch.object(settings, 'retell_permanent_agent_id', 'agent-fixed'):
            user = self.seed(db)
            published = ensure_script_studio(db, user.id)
            values = {
                field: getattr(published, field)
                for field in (
                    'opening_internal', 'opening_consented', 'purpose_statement',
                    'discovery_content', 'objection_library', 'closing_library',
                    'voicemail_content', 'voice_settings', 'talking_points',
                    'compliance_content',
                )
            }
            values['opening_internal'] = (
                'Hi {{customer_name}}, this is Ava calling on behalf of Himanshu Soni '
                'for an internal workflow test. Do you have thirty seconds?'
            )
            expected_hash = script_content_hash(values)
            provider = AsyncMock()
            provider.get_agent.return_value = {'agent_id': 'agent-fixed'}

            async def fake_publish(session, row, actor, selected_provider, **kwargs):
                self.assertTrue(kwargs.get('require_playground'))
                session.execute(
                    __import__('sqlalchemy').update(CallScriptVersion)
                    .where(
                        CallScriptVersion.campaign_id == row.campaign_id,
                        CallScriptVersion.status == 'published',
                        CallScriptVersion.id != row.id,
                    )
                    .values(status='archived')
                )
                row.status = 'published'
                row.retell_agent_version = 4
                row.retell_flow_version = 4
                row.published_content_hash = row.content_hash
                row.publish_state = {
                    **(row.publish_state or {}),
                    'node_text_verification': {'passed': True},
                }
                session.commit()
                return {
                    'agent_id': row.retell_agent_id,
                    'agent_version': 4,
                    'conversation_flow_id': row.conversation_flow_id,
                    'conversation_flow_version': 4,
                    'node_text_verification': {'passed': True},
                    'playground_validation': playground,
                }

            playground = {
                'passed': True,
                'mode': 'retell_agent_playground_no_phone_call',
                'real_phone_calls': 0,
            }
            with patch(
                'app.services.call_script_studio.publish_script',
                new=AsyncMock(side_effect=fake_publish),
            ) as publish_mock:
                first = asyncio.run(publish_script_changes(
                    db,
                    user,
                    base_published_version_id=published.id,
                    form_values=values,
                    current_content_hash=expected_hash,
                    idempotency_key='qa-publish-idempotency-0001',
                    provider=provider,
                ))
                second = asyncio.run(publish_script_changes(
                    db,
                    user,
                    base_published_version_id=published.id,
                    form_values=values,
                    current_content_hash=expected_hash,
                    idempotency_key='qa-publish-idempotency-0001',
                    provider=provider,
                ))
            self.assertFalse(first['idempotent_replay'])
            self.assertTrue(second['idempotent_replay'])
            self.assertEqual(publish_mock.await_count, 1)
            self.assertEqual(
                len(db.scalars(select(CallScriptVersion).where(
                    CallScriptVersion.status == 'published',
                )).all()),
                1,
            )

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
            draft.tested_content_hash = draft.content_hash
            draft.approved_content_hash = draft.content_hash
            draft.status = 'approved'
            provider = AsyncMock()
            provider.get_agent.side_effect = [
                {
                    'agent_id': 'agent-fixed',
                    'version': 0,
                    'is_published': True,
                    'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': draft.conversation_flow_id, 'version': 0},
                },
                {
                    'agent_id': 'agent-fixed',
                    'version': 1,
                    'is_published': False,
                    'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': draft.conversation_flow_id, 'version': 1},
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
                self.flow_payload(draft, version=0, override={'purpose': 'old'}),
                self.flow_payload(draft, version=1, override={'purpose': 'old'}),
                self.flow_payload(draft, version=1),
            ]
            provider.update_conversation_flow.return_value = {'conversation_flow_id': draft.conversation_flow_id, 'version': 1}
            provider.get_phone_number.side_effect = [
                {'inbound_agents': [], 'outbound_agents': [{'agent_id': 'agent-fixed', 'agent_version': 0, 'weight': 1}]},
                {'inbound_agents': [], 'outbound_agents': [{'agent_id': 'agent-fixed', 'agent_version': 1, 'weight': 1}]},
            ]
            result = asyncio.run(publish_script(db, draft, user, provider))
            self.assertFalse(result['new_agent_created'])
            self.assertFalse(result['new_conversation_flow_created'])
            old = db.scalar(select(CallScriptVersion).where(CallScriptVersion.version_number == 1))
            self.assertEqual(old.status, 'archived')
            self.assertEqual(draft.status, 'published')
            provider.update_conversation_flow.assert_awaited_once()
            provider.create_agent_version.assert_awaited_once_with('agent-fixed', 0)
            provider.publish_agent_version.assert_awaited_once_with('agent-fixed', 1)
            provider.update_phone_number_assignment.assert_awaited_once()

    def test_publish_retry_reuses_verified_provider_version(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'), patch.object(settings, 'retell_permanent_agent_id', 'agent-fixed'):
            user = self.seed(db)
            ensure_script_studio(db, user.id)
            draft = create_draft(db, user.id)
            update_draft(db, draft, {'purpose_statement': 'Recovered purpose'}, user)
            draft.test_result = {'passed': True}
            draft.tested_content_hash = draft.content_hash
            draft.approved_content_hash = draft.content_hash
            draft.status = 'failed_recoverable'
            draft.publish_state = {'draft_agent_version': 1, 'flow_version': 1, 'stage': 'agent_published'}
            provider = AsyncMock()
            provider.get_agent.side_effect = [
                {'agent_id': 'agent-fixed', 'version': 1, 'is_published': True, 'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': draft.conversation_flow_id, 'version': 1}},
                {'agent_id': 'agent-fixed', 'version': 1, 'is_published': True, 'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': draft.conversation_flow_id, 'version': 1}},
                {'agent_id': 'agent-fixed', 'version': 1, 'is_published': True, 'response_engine': {'type': 'conversation-flow', 'conversation_flow_id': draft.conversation_flow_id, 'version': 1}},
            ]
            provider.get_conversation_flow.side_effect = [self.flow_payload(draft), self.flow_payload(draft)]
            provider.get_phone_number.return_value = {
                'inbound_agents': [],
                'outbound_agents': [{'agent_id': 'agent-fixed', 'agent_version': 1, 'weight': 1}],
            }
            result = asyncio.run(publish_script(db, draft, user, provider))
            self.assertEqual(result['agent_version'], 1)
            provider.create_agent_version.assert_not_awaited()
            provider.publish_agent_version.assert_not_awaited()
            provider.update_conversation_flow.assert_not_awaited()

    def test_exact_node_text_verification_detects_drift(self):
        with self.Session() as db, patch.object(settings, 'retell_agent_id', 'agent-fixed'):
            user = self.seed(db)
            row = ensure_script_studio(db, user.id)
            self.assertTrue(verify_retell_node_texts(row, self.flow_payload(row))['passed'])
            drifted = self.flow_payload(row, override={'purpose': 'wrong'})
            result = verify_retell_node_texts(row, drifted)
            self.assertFalse(result['passed'])
            self.assertEqual(result['mismatches'][0]['node_id'], 'purpose')

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

    def test_grouped_approval_preserves_existing_evidence(self):
        with self.Session() as db:
            user = self.seed(db)
            items = ensure_compliance_items(db)
            target = next(item for item in items if item.item_key == 'allstate_ai_approval')
            target.approver = 'Original approver'
            target.evidence = 'original evidence'
            target.effective_at = datetime(2026, 7, 1)
            target.status = 'approved'
            apply_compliance_package(db, 'allstate', {
                'approver': 'Package approver',
                'evidence': 'package evidence',
                'effective_at': '2026-07-26T12:00:00',
            }, user)
            self.assertEqual(target.status, 'approved')
            self.assertEqual(target.evidence, 'original evidence')
            self.assertEqual(target.approver, 'Original approver')
            self.assertEqual(len(ensure_compliance_items(db)), 19)
            self.assertEqual(compliance_blocker_details(ensure_compliance_items(db))[0]['missing_fields'], ['approval decision', 'approver', 'evidence', 'effective date'])

    def test_simple_consent_profile_preview_requires_four_columns_and_does_not_verify(self):
        with self.Session() as db:
            user = self.seed(db)
            profile = create_consent_source_profile(db, {
                'name': 'QA consent source',
                'approved_consent_language': 'I agree to an automated call from Allstate.',
                'organization_authorized': True,
                'automated_call_permission': True,
                'consent_proof_method': 'signed form',
                'source_approval_evidence': 'evidence://qa',
                'approval_date': '2026-07-26T12:00:00',
            }, user)
            preview = preview_simple_consent_rows(db, [{
                'first_name': 'QA',
                'phone_number': '+14165550123',
                'consent_timestamp': '2026-07-26T12:30:00',
                'consent_reference': 'qa-ref',
            }], profile)
            self.assertEqual(preview['valid_rows'], 1)
            normalized = preview['import_rows'][0]
            self.assertEqual(normalized['consent_status'], 'under_review')
            self.assertEqual(normalized['consented_number'], '+14165550123')
            self.assertEqual(normalized['product_interest'], 'Auto and property insurance')
            self.assertEqual(normalized['source_profile_id'], profile.id)

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
