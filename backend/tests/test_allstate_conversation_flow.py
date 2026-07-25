import unittest

from app.services.allstate_conversation_flow import (
    FLOW_TITLE,
    GLOBAL_PROMPT,
    LIVE_MODEL,
    MANDATORY_STATE_VARIABLES,
    POST_CALL_MODEL,
    REQUIRED_LOGICAL_NODES,
    conversation_flow_payload,
    expected_next_action,
    flow_nodes,
    score_sales_transcript,
)


FAILED_CALL_RENEWAL = """Agent: Hi Himanshu, this is Ava calling on behalf of Himanshu Soni, an Allstate Sales Agent in Scarborough. Is now a bad time for a quick conversation?
User: No.
Agent: Understood. Thank you for your time.
User: Go ahead.
Agent: Which type of insurance are you interested in?
User: Auto.
Agent: Do you have an idea when your policy is up for renewal?
User: Next year.
Agent: When was the last time an agent explained the coverage options?
User: Few months back.
Agent: Would you be open to a brief conversation with Himanshu?
User: How about next year?
Agent: Would you prefer now or later closer to renewal?
User: Later.
Agent: I will note to reconnect closer to your renewal date. Thank you for your time."""

FAILED_CALL_OPENING = """Agent: Hi Himanshu, this is Ava calling on behalf of Himanshu Soni, an Allstate Sales Agent in Scarborough. Is now a bad time for a quick conversation?
User: No.
Agent: Understood. Thank you for your time."""


class AllstateConversationFlowTests(unittest.TestCase):
    def test_global_prompt_and_node_instructions_stay_bounded(self):
        self.assertLessEqual(len(GLOBAL_PROMPT.split()), 900)
        for node in flow_nodes():
            instruction = node.get('instruction') or {}
            self.assertLessEqual(len(str(instruction.get('text') or '').split()), 180, node.get('name'))

    def test_flow_contains_required_nodes_models_tools_and_state(self):
        payload = conversation_flow_payload('not-a-real-secret', ['kb-1'])
        names = {node['name'] for node in payload['nodes']}
        self.assertEqual(payload['model_choice']['model'], LIVE_MODEL)
        self.assertFalse(payload['model_choice']['high_priority'])
        self.assertFalse(payload['flex_mode'])
        self.assertTrue(REQUIRED_LOGICAL_NODES.issubset(names))
        self.assertIn('Extract Structured Sales State', names)
        self.assertEqual(len(MANDATORY_STATE_VARIABLES), 22)
        self.assertIn('callback_daypart', MANDATORY_STATE_VARIABLES)
        self.assertEqual(set(payload['default_dynamic_variables']), set(MANDATORY_STATE_VARIABLES))
        extracted = next(node for node in payload['nodes'] if node['id'] == 'extract_state')
        self.assertEqual({item['name'] for item in extracted['variables']}, set(MANDATORY_STATE_VARIABLES))
        self.assertEqual(extracted['model_choice']['model'], POST_CALL_MODEL)
        self.assertEqual({tool['name'] for tool in payload['tools']}, {'voryx_get_quote_slots', 'voryx_book_quote_appointment', 'voryx_mark_do_not_call'})
        self.assertEqual(payload['notes'][0]['content'], FLOW_TITLE)

    def test_exact_failed_opening_no_means_available(self):
        self.assertEqual(expected_next_action('No.', 'opening')['next_node'], 'purpose')
        score = score_sales_transcript(FAILED_CALL_OPENING)
        self.assertLess(score['overall_score'], 8)

    def test_exact_failed_renewal_transcript_is_capped(self):
        score = score_sales_transcript(FAILED_CALL_RENEWAL)
        self.assertLessEqual(score['overall_score'], 4)
        self.assertIn('callback intent ended without renewal month', score['critical_failures'])

    def test_required_objection_and_callback_routes(self):
        cases = [
            ('Call me when my policy renews.', 'renewal_capture', 'capture_renewal_then_callback'),
            ('My renewal is sometime in October.', 'coverage_review', 'store_renewal_month'),
            ('I already have insurance.', 'soft_reframe', 'one_reframe_then_close'),
            ("I'm happy with my current company.", 'soft_reframe', 'one_reframe_then_close'),
            ("I'm not looking to switch.", 'soft_reframe', 'one_reframe_then_close'),
            ("I'm busy.", 'busy_callback', 'capture_specific_callback'),
            ('Send me information.', 'soft_reframe', 'one_reframe_then_close'),
            ('I need to speak to my wife.', 'soft_reframe', 'one_reframe_then_close'),
            ('I already have an agent.', 'soft_reframe', 'one_reframe_then_close'),
            ('I only care about price.', 'soft_reframe', 'one_reframe_then_close'),
            ('No thanks.', 'neutral_reframe', 'one_permission_reframe_then_close'),
            ("Do not call me again.", 'dnc', 'suppress_and_end'),
            ('I want an appointment.', 'appointment_close', 'offer_verified_slots'),
            ('Call me later near renewal.', 'renewal_capture', 'capture_renewal_then_callback'),
        ]
        for text, node, action in cases:
            with self.subTest(text=text):
                result = expected_next_action(text, 'current_situation')
                self.assertEqual((result['next_node'], result['action']), (node, action))

    def test_second_and_hard_rejection_end_without_reframe(self):
        self.assertEqual(expected_next_action("No, I'm definitely not interested.", 'objection', 1)['next_node'], 'end')
        self.assertEqual(expected_next_action('Leave me alone.', 'objection')['next_node'], 'end')

    def test_flow_does_not_create_voice_agent(self):
        source = conversation_flow_payload('token', ['kb'])
        self.assertNotIn('create-agent', str(source))

    def test_live_model_can_be_compared_without_changing_flow_contract(self):
        mini = conversation_flow_payload('token', ['kb'], model='gpt-4.1-mini')
        full = conversation_flow_payload('token', ['kb'], model='gpt-4.1')
        self.assertEqual(mini['model_choice']['model'], 'gpt-4.1-mini')
        self.assertEqual(full['model_choice']['model'], 'gpt-4.1')
        self.assertEqual(mini['nodes'], full['nodes'])


if __name__ == '__main__':
    unittest.main()
