import re
from typing import Any


FLOW_TITLE = 'Voryx Allstate Sales Conversation Flow V2'
LIVE_MODEL = 'gpt-4.1-mini'
POST_CALL_MODEL = 'gpt-4.1-nano'

MANDATORY_STATE_VARIABLES = [
    'permission_to_continue', 'product_interest', 'current_insurance_status',
    'renewal_month', 'renewal_date_if_known', 'last_coverage_review',
    'july_changes_reviewed', 'main_motivation', 'objection_type',
    'objection_count', 'close_attempt_count', 'appointment_interest',
    'appointment_slot', 'callback_requested', 'callback_date', 'callback_time',
    'callback_daypart', 'callback_reason', 'hard_rejection', 'do_not_call',
    'recording_objection', 'call_outcome',
]

REQUIRED_LOGICAL_NODES = {
    'Opening and Permission',
    'Purpose and Product Interest',
    'Current Insurance Status',
    'Renewal Date Capture',
    'Coverage Review Discovery',
    'Ontario July Change Discussion',
    'Objection Classification',
    'Soft Objection Reframe',
    'Neutral Rejection Reframe',
    'Appointment Close',
    'Renewal Callback Close',
    'Busy Callback Close',
    'Trust and Scam Handling',
    'Direct Automation Question',
    'Do Not Call',
    'Appointment Tool Result',
    'Callback Confirmation',
    'Compliant Ending',
}

GLOBAL_PROMPT = """You are Ava, a professional calling assistant for Himanshu Soni, an Allstate Sales Agent in Scarborough, Ontario. Your job is to create interest in a licensed-agent second opinion and arrange a specific appointment or callback.

Be warm, confident, attentive and consultative. Use one question at a time and one or two short sentences per response. Do not sound rushed, aggressive, excessively cheerful or passive. Acknowledge briefly, then advance the active node.

Never quote, bind, interpret a policy, recommend limits, give legal or underwriting advice, guarantee savings, claim a customer is underinsured, or say switching is necessary. Do not claim or imply you are human. Do not proactively announce automation. When directly asked, answer truthfully in the Direct Automation Question node.

For Ontario July 1 information, say only that medical, rehabilitation and attendant-care accident benefits remain mandatory while several other accident benefits became optional, and that existing policies may retain prior selections at renewal unless changes are agreed. Refer individual questions to Himanshu.

Never invent availability. Offer only slots returned by the Voryx appointment tool. A valid conversion is a booked appointment or a specific callback with timing and permission. A vague "later" is incomplete. Use one tailored reframe after a soft objection or first neutral rejection. A second refusal ends politely. A hard rejection ends immediately. A do-not-call request must invoke the Voryx DNC tool and end immediately.

Do not request payment, banking information, government identification or policy credentials. If the customer objects to recording, explain that the internal test may be recorded and transcribed, and end if they do not consent."""


def prompt_edge(edge_id: str, condition: str, destination: str) -> dict:
    return {
        'id': edge_id,
        'transition_condition': {'type': 'prompt', 'prompt': condition},
        'destination_node_id': destination,
    }


def conversation_node(
    node_id: str,
    name: str,
    instruction: str,
    edges: list[dict],
    x: int,
    y: int,
    **extra: Any,
) -> dict:
    return {
        'id': node_id,
        'name': name,
        'type': 'conversation',
        'instruction': {'type': 'prompt', 'text': instruction},
        'edges': edges,
        'display_position': {'x': x, 'y': y},
        **extra,
    }


def custom_tools(tool_token: str) -> list[dict]:
    header = {'X-Voryx-Retell-Tool-Token': tool_token}
    return [
        {
            'tool_id': 'tool_voryx_slots',
            'type': 'custom',
            'name': 'voryx_get_quote_slots',
            'description': 'Return two real Voryx-supported appointment slots. Call before offering any slot.',
            'url': 'https://ops.themealz.com/api/retell/tools/quote-appointment-slots',
            'method': 'POST',
            'headers': header,
            'args_at_root': True,
            'speak_after_execution': True,
            'parameters': {
                'type': 'object',
                'properties': {'voryx_call_attempt_id': {'type': 'string'}},
                'required': ['voryx_call_attempt_id'],
            },
        },
        {
            'tool_id': 'tool_voryx_book',
            'type': 'custom',
            'name': 'voryx_book_quote_appointment',
            'description': 'Book only a slot the customer accepted. Never claim success until this returns ok.',
            'url': 'https://ops.themealz.com/api/retell/tools/book-quote-appointment',
            'method': 'POST',
            'headers': header,
            'args_at_root': True,
            'speak_after_execution': True,
            'parameters': {
                'type': 'object',
                'properties': {
                    'voryx_call_attempt_id': {'type': 'string'},
                    'appointment_date': {'type': 'string'},
                    'appointment_time': {'type': 'string'},
                    'timezone': {'type': 'string'},
                    'insurance_interest': {'type': 'string'},
                    'notes': {'type': 'string'},
                },
                'required': [
                    'voryx_call_attempt_id',
                    'appointment_date',
                    'appointment_time',
                    'timezone',
                ],
            },
        },
        {
            'tool_id': 'tool_voryx_dnc',
            'type': 'custom',
            'name': 'voryx_mark_do_not_call',
            'description': 'Immediately suppress the called number after any do-not-call request.',
            'url': 'https://ops.themealz.com/api/retell/tools/mark-do-not-call',
            'method': 'POST',
            'headers': header,
            'args_at_root': True,
            'speak_after_execution': False,
            'parameters': {
                'type': 'object',
                'properties': {
                    'voryx_call_attempt_id': {'type': 'string'},
                    'phone_number': {'type': 'string'},
                    'reason': {'type': 'string'},
                },
                'required': ['voryx_call_attempt_id', 'reason'],
            },
        },
    ]


def flow_nodes() -> list[dict]:
    opening = conversation_node(
        'opening',
        'Opening and Permission',
        """Say exactly: "Hi {{customer_name}}, this is Ava calling on behalf of Himanshu Soni, an Allstate Sales Agent in Scarborough. Is now a bad time for a quick conversation?" If {{internal_test}} is true, add: "This is a test of his insurance quote appointment workflow." A bare "no" means it is not a bad time, so continue to Purpose. "Yes," busy, or later routes to Busy Callback. Never end on a bare "no." """,
        [
            prompt_edge('opening_available', "Customer is available, says go ahead, or answers no to 'is now a bad time'", 'purpose'),
            prompt_edge('opening_busy', 'Customer says it is a bad time, is busy, or requests later contact', 'busy_callback'),
            prompt_edge('opening_trust', 'Customer questions legitimacy, identity, or source of number', 'trust'),
        ],
        0,
        0,
    )
    purpose = conversation_node(
        'purpose',
        'Purpose and Product Interest',
        """Say: "The reason for my call is to see whether your current auto or property coverage still fits what you and your family need, and whether a short second-opinion conversation with Himanshu would be useful." Ask: "Is auto, home, condo, tenant insurance, or a combination most relevant?" Stay here until product interest is captured or the customer refuses.""",
        [
            prompt_edge('purpose_captured', 'Customer identifies a relevant insurance product', 'insurance_status'),
            prompt_edge('purpose_objection', 'Customer objects or rejects instead of identifying a product', 'objection_classifier'),
        ],
        300,
        0,
    )
    insurance = conversation_node(
        'insurance_status',
        'Current Insurance Status',
        """Ask only: "Are you currently insured?" Capture yes, no, or legitimately unknown. If the customer says they already have insurance, do not treat that as rejection; route through the soft-objection second-opinion response. Otherwise continue to renewal timing.""",
        [
            prompt_edge('insurance_already', 'Customer frames already having insurance as an objection', 'soft_reframe'),
            prompt_edge('insurance_captured', 'Current insurance status is captured', 'renewal_capture'),
        ],
        600,
        0,
    )
    renewal = conversation_node(
        'renewal_capture',
        'Renewal Date Capture',
        """Ask: "When does the policy normally renew?" Capture a month. Clarify vague answers: fall means ask September, October or November; later this year means ask which month; a relative period means calculate and confirm the approximate month. If they cannot remember, ask whether a reminder closer to renewal would help. A request to call near renewal stays here until renewal month is known, then routes to Renewal Callback.""",
        [
            prompt_edge('renewal_callback', 'Customer requests contact near renewal and renewal month has been captured', 'renewal_callback'),
            prompt_edge('renewal_captured', 'Renewal month is captured or legitimately unavailable after clarification', 'coverage_review'),
            prompt_edge('renewal_objection', 'Customer raises another objection', 'objection_classifier'),
        ],
        900,
        0,
    )
    review = conversation_node(
        'coverage_review',
        'Coverage Review Discovery',
        """Ask: "When was the last time an agent actually walked you through the coverages and available choices, rather than simply sending the renewal?" Capture the answer. If recent, continue to the Ontario July question. If never or uncertain, briefly explain that a second opinion can validate whether protection still matches what they intended, then continue to the appointment close.""",
        [
            prompt_edge('review_recent', 'Customer had a recent coverage review', 'july_review'),
            prompt_edge('review_value', 'Customer says never, cannot remember, or only receives renewals', 'appointment_close'),
            prompt_edge('review_objection', 'Customer resists or raises an objection', 'objection_classifier'),
        ],
        1200,
        0,
    )
    july = conversation_node(
        'july_review',
        'Ontario July Change Discussion',
        """Ask: "Did that review include the Ontario accident-benefit choices affected by the July changes, or was it mainly a price renewal?" Give only the high-level approved facts in the global prompt or knowledge base. Do not interpret coverage, recommend limits, or say coverage was removed. Then continue to the appointment close.""",
        [prompt_edge('july_complete', 'Customer answers or declines the July review question', 'appointment_close')],
        1500,
        0,
    )
    classifier = conversation_node(
        'objection_classifier',
        'Objection Classification',
        """Classify the latest statement without arguing. Route already insured, happy, not switching, later renewal, send information, think about it, price, spouse, or already has an agent as soft. Route first "not interested," "no thanks," or "probably not" as neutral. Route a second refusal or definite rejection to End. Route near-renewal intent to Renewal Capture. Route busy to Busy Callback. Use at most a brief acknowledgement before transitioning.""",
        [
            prompt_edge('classify_renewal', 'Customer requests contact near renewal or provides vague renewal timing', 'renewal_capture'),
            prompt_edge('classify_busy', 'Customer says they are busy or requests another time', 'busy_callback'),
            prompt_edge('classify_soft', 'Customer gives a soft objection', 'soft_reframe'),
            prompt_edge('classify_neutral', 'Customer gives a first neutral rejection', 'neutral_reframe'),
            prompt_edge('classify_end', 'Customer gives a second refusal or hard rejection', 'end'),
        ],
        900,
        300,
        global_node_setting={
            'condition': 'Customer raises a sales objection, neutral rejection, or hard rejection',
            'cool_down': 2,
        },
    )
    soft = conversation_node(
        'soft_reframe',
        'Soft Objection Reframe',
        """Use exactly one tailored reframe, then one close. Already insured: explain that most people are and a second opinion does not require cancelling; ask when coverage was last reviewed. Happy: position review as validation that staying still makes sense. Not switching: say the conversation creates no obligation. Price: review cost and what is included. Spouse: offer a time both can join. Send information: offer a short call before or after. Then route to Appointment Close or a specific callback. Never reframe twice.""",
        [
            prompt_edge('soft_review', 'Customer engages and coverage review timing is not known', 'coverage_review'),
            prompt_edge('soft_close', 'Customer engages and is ready for a specific close', 'appointment_close'),
            prompt_edge('soft_callback', 'Customer prefers later or near renewal', 'renewal_capture'),
            prompt_edge('soft_refused', 'Customer refuses again', 'end'),
        ],
        1200,
        300,
    )
    neutral = conversation_node(
        'neutral_reframe',
        'Neutral Rejection Reframe',
        """For the first neutral rejection only, say: "I understand. Before I let you go, when was the last time someone reviewed the coverages and accident-benefit choices with you?" If they engage, continue to Coverage Review and make one close. If they refuse again, end immediately and politely. Do not add another reframe.""",
        [
            prompt_edge('neutral_engaged', 'Customer answers or agrees to continue', 'coverage_review'),
            prompt_edge('neutral_refused', 'Customer refuses again', 'end'),
        ],
        1200,
        520,
    )
    appointment = {
        'id': 'appointment_close',
        'name': 'Appointment Close',
        'type': 'subagent',
        'instruction': {
            'type': 'prompt',
            'text': """Use: "It sounds like a short review would at least give you a clearer comparison. Would a weekday evening or a weekend morning be easier?" Call voryx_get_quote_slots before naming availability. Offer exactly the two returned slots. If one is accepted, call voryx_book_quote_appointment. Never claim a booking until the tool returns ok. If neither works, ask for a preferred day and time. Final fallback is a specific renewal callback.""",
        },
        'tool_ids': ['tool_voryx_slots', 'tool_voryx_book'],
        'edges': [
            prompt_edge('appointment_result', 'Appointment tool returned a result', 'appointment_result'),
            prompt_edge('appointment_callback', 'Customer prefers a callback or rejects both slots', 'renewal_capture'),
            prompt_edge('appointment_refused', 'Customer clearly refuses the close', 'end'),
        ],
        'display_position': {'x': 1800, 'y': 0},
    }
    renewal_callback = conversation_node(
        'renewal_callback',
        'Renewal Callback Close',
        """Confirm the renewal month. Ask: "Would you prefer Himanshu to reconnect at the beginning of that month or about two weeks before renewal?" Then ask: "Would a weekday evening or weekend morning normally be easier?" Capture callback window, daypart, reason, and permission to reconnect. Do not end with vague "later." """,
        [
            prompt_edge('renewal_callback_ready', 'Month, callback window, daypart, reason, and permission are captured', 'callback_confirmation'),
            prompt_edge('renewal_callback_refused', 'Customer refuses to provide timing again', 'end'),
        ],
        1500,
        380,
    )
    busy_callback = conversation_node(
        'busy_callback',
        'Busy Callback Close',
        """Say: "No problem. Would later today or another day be better?" Then obtain a specific date, time, or at minimum a weekday-evening or weekend-morning daypart. Confirm permission to reconnect and the reason. Do not treat busy as rejection and do not finish without callback timing unless the customer refuses again.""",
        [
            prompt_edge('busy_callback_ready', 'Specific callback timing or daypart and permission are captured', 'callback_confirmation'),
            prompt_edge('busy_callback_refused', 'Customer refuses to provide callback timing', 'end'),
        ],
        600,
        360,
    )
    trust = conversation_node(
        'trust',
        'Trust and Scam Handling',
        """Acknowledge the concern. Identify Ava as calling on behalf of Himanshu Soni, an Allstate Sales Agent. Say you will not request payment, banking details, government identification, or policy credentials. Offer a direct callback with Himanshu. If consent source or date is unavailable, do not invent it; end and flag consent review. If the concern is recording, explain the internal test may be recorded and transcribed, and end if they do not consent.""",
        [
            prompt_edge('trust_continue', 'Customer is reassured and agrees to continue', 'purpose'),
            prompt_edge('trust_callback', 'Customer wants a direct callback', 'busy_callback'),
            prompt_edge('trust_end', 'Customer remains uncomfortable or declines recording', 'end'),
        ],
        300,
        360,
        global_node_setting={
            'condition': 'Customer questions legitimacy, scam risk, number source, consent, or recording',
            'cool_down': 2,
        },
    )
    automation = conversation_node(
        'automation',
        'Direct Automation Question',
        """Answer: "Yes, I'm an automated calling assistant helping Himanshu with initial conversations and scheduling. I can't provide insurance advice or quote prices, but I can arrange a conversation with him." Do not claim to be human. Ask whether the customer is comfortable continuing. Continue only with permission.""",
        [
            prompt_edge('automation_continue', 'Customer agrees to continue', 'purpose'),
            prompt_edge('automation_end', 'Customer declines or remains uncomfortable', 'end'),
        ],
        300,
        580,
        global_node_setting={
            'condition': 'Customer directly asks whether Ava is AI, automated, a robot, or a real person',
            'cool_down': 10,
        },
    )
    dnc = {
        'id': 'dnc',
        'name': 'Do Not Call',
        'type': 'subagent',
        'instruction': {
            'type': 'prompt',
            'text': 'Say: "Understood. I will mark this number not to be contacted again. Thank you." Immediately invoke voryx_mark_do_not_call. Do not ask another question.',
        },
        'tool_ids': ['tool_voryx_dnc'],
        'edges': [prompt_edge('dnc_done', 'DNC tool completed or returned a terminal result', 'end')],
        'global_node_setting': {
            'condition': 'Customer says do not call, stop calling, remove my number, or take me off the list',
            'cool_down': 10,
        },
        'display_position': {'x': 600, 'y': 580},
    }
    appointment_result = conversation_node(
        'appointment_result',
        'Appointment Tool Result',
        """If the booking tool returned ok, confirm the exact booked time and that Himanshu will connect then. If it failed, apologize without claiming a booking and offer a specific callback instead. Never invent or alter the returned slot.""",
        [
            prompt_edge('appointment_confirmed', 'Booking tool returned ok and confirmation was spoken', 'extract_state'),
            prompt_edge('appointment_failed', 'Booking tool failed or did not confirm', 'busy_callback'),
        ],
        2100,
        0,
    )
    callback_confirmation = conversation_node(
        'callback_confirmation',
        'Callback Confirmation',
        """Repeat the exact callback arrangement: month/date or callback window, preferred daypart/time, reason, and permission to reconnect. Ask the customer to confirm. If any element is missing, ask only for that element before ending. A vague callback is not complete.""",
        [
            prompt_edge('callback_confirmed', 'Customer confirms complete callback timing and permission', 'extract_state'),
            prompt_edge('callback_missing', 'A required callback element remains missing', 'renewal_callback'),
            prompt_edge('callback_cancelled', 'Customer withdraws callback permission', 'end'),
        ],
        1800,
        380,
    )
    variable_defs = []
    boolean_names = {
        'permission_to_continue', 'july_changes_reviewed', 'appointment_interest',
        'callback_requested', 'hard_rejection', 'do_not_call', 'recording_objection',
    }
    number_names = {'objection_count', 'close_attempt_count'}
    for name in MANDATORY_STATE_VARIABLES:
        variable_type = 'boolean' if name in boolean_names else ('number' if name in number_names else 'string')
        variable_defs.append({
            'name': name,
            'type': variable_type,
            'description': f'Final structured call state for {name.replace("_", " ")}.',
            'required': False,
        })
    extract = {
        'id': 'extract_state',
        'name': 'Extract Structured Sales State',
        'type': 'extract_dynamic_variables',
        'variables': variable_defs,
        'model_choice': {
            'type': 'cascading',
            'model': POST_CALL_MODEL,
            'high_priority': False,
        },
        'else_edge': prompt_edge('extract_end', 'Else', 'end'),
        'display_position': {'x': 2350, 'y': 180},
    }
    end = {
        'id': 'end',
        'name': 'Compliant Ending',
        'type': 'end',
        'speak_during_execution': True,
        'instruction': {'type': 'static_text', 'text': 'Thank you for your time.'},
        'display_position': {'x': 2600, 'y': 180},
    }
    return [
        opening, purpose, insurance, renewal, review, july, classifier, soft,
        neutral, appointment, renewal_callback, busy_callback, trust, automation,
        dnc, appointment_result, callback_confirmation, extract, end,
    ]


def conversation_flow_payload(tool_token: str, knowledge_base_ids: list[str], model: str = LIVE_MODEL) -> dict:
    return {
        'model_choice': {'type': 'cascading', 'model': model, 'high_priority': False},
        'model_temperature': 0.2,
        'tool_call_strict_mode': True,
        'start_speaker': 'agent',
        'global_prompt': GLOBAL_PROMPT,
        'knowledge_base_ids': knowledge_base_ids,
        'kb_config': {'top_k': 3, 'filter_score': 0.6},
        'tools': custom_tools(tool_token),
        'nodes': flow_nodes(),
        'start_node_id': 'opening',
        'flex_mode': False,
        'default_dynamic_variables': {name: '' for name in MANDATORY_STATE_VARIABLES},
        'notes': [{
            'id': 'flow_title',
            'content': FLOW_TITLE,
            'display_position': {'x': 0, 'y': -180},
            'size': {'width': 360, 'height': 90},
        }],
    }


def classify_objection(text: str) -> str | None:
    value = text.lower()
    if any(term in value for term in ('do not call', "don't call", 'remove my number', 'take me off')):
        return 'dnc'
    if any(term in value for term in ('definitely no', 'leave me alone', 'stop selling', 'stop calling')):
        return 'hard'
    if any(term in value for term in ('not interested', 'no thanks', 'probably not')):
        return 'neutral'
    soft_terms = (
        'already insured', 'already have insurance', 'happy with', 'not looking to switch',
        'busy', 'send information', 'send me information', 'speak to my wife',
        'speak to my spouse', 'already have an agent', 'only care about price',
        'call me when', 'near renewal', 'later',
    )
    return 'soft' if any(term in value for term in soft_terms) else None


def expected_next_action(customer_text: str, stage: str = 'opening', objection_count: int = 0) -> dict:
    text = customer_text.lower().strip()
    objection = classify_objection(text)
    if objection == 'dnc':
        return {'next_node': 'dnc', 'action': 'suppress_and_end', 'captures': ['do_not_call']}
    if objection == 'hard' or (objection in {'neutral', 'soft'} and objection_count >= 1):
        return {'next_node': 'end', 'action': 'end_without_reframe', 'captures': ['hard_rejection']}
    if stage == 'opening' and text in {'no', 'no.', 'go ahead', 'sure', 'okay'}:
        return {'next_node': 'purpose', 'action': 'explain_purpose', 'captures': ['permission_to_continue']}
    if objection == 'soft' and any(term in text for term in ('busy',)):
        return {'next_node': 'busy_callback', 'action': 'capture_specific_callback', 'captures': ['callback_requested']}
    if objection == 'soft' and any(term in text for term in ('call me', 'near renewal', 'later')):
        return {'next_node': 'renewal_capture', 'action': 'capture_renewal_then_callback', 'captures': ['callback_requested']}
    if objection == 'soft':
        return {'next_node': 'soft_reframe', 'action': 'one_reframe_then_close', 'captures': ['objection_type']}
    if objection == 'neutral':
        return {'next_node': 'neutral_reframe', 'action': 'one_permission_reframe_then_close', 'captures': ['objection_type']}
    if 'october' in text:
        return {'next_node': 'coverage_review', 'action': 'store_renewal_month', 'captures': ['renewal_month']}
    if 'appointment' in text or 'slot' in text:
        return {'next_node': 'appointment_close', 'action': 'offer_verified_slots', 'captures': ['appointment_interest']}
    return {'next_node': stage, 'action': 'continue_required_question', 'captures': []}


def score_sales_transcript(transcript: str | None) -> dict:
    text = transcript or ''
    lower = text.lower()
    user_lines = [line[5:].strip() for line in text.splitlines() if line.lower().startswith('user:')]
    agent_lines = [line[6:].strip() for line in text.splitlines() if line.lower().startswith('agent:')]
    user_text = ' '.join(user_lines).lower()
    agent_text = ' '.join(agent_lines).lower()
    objection = next((classify_objection(line) for line in user_lines if classify_objection(line)), None)
    months = (
        'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december',
    )
    checks = {
        'identity_and_reason': 'himanshu soni' in agent_text and 'allstate' in agent_text,
        'permission_or_timing': 'bad time' in agent_text or any(term in agent_text for term in ('later today', 'another day', 'weekday evening', 'weekend morning')),
        'product_interest': any(term in user_text for term in ('auto', 'home', 'condo', 'tenant', 'bundle', 'property')),
        'renewal_month': any(month in user_text for month in months) or "don't remember" in user_text,
        'coverage_review': 'last time' in agent_text and ('coverage' in agent_text or 'coverages' in agent_text),
        'objection_handled': not objection or any(term in agent_text for term in ('second opinion', 'review', 'reconnect', 'callback')),
        'specific_close_attempted': any(term in agent_text for term in ('weekday evening', 'weekend morning', 'two weeks before', 'which works better')),
        'conversion_captured': bool(re.search(r'(appointment|reconnect|callback).{0,100}(january|february|march|april|may|june|july|august|september|october|november|december|weekday|weekend|\d{1,2}:\d{2})', lower)),
        'compliant_ending': any(term in agent_text for term in ('thank you for your time', 'not to be contacted again', 'mark this number')),
    }
    points = sum(1 for key, passed in checks.items() if passed and key != 'objection_handled')
    points += 2 if checks['objection_handled'] else 0
    critical = []
    if any(term in user_text for term in ('near renewal', 'next year', 'later')) and not checks['renewal_month']:
        critical.append('callback intent ended without renewal month')
    if 'busy' in user_text and not any(term in agent_text for term in ('later today', 'another day', 'weekday', 'weekend')):
        critical.append('busy intent ended without callback timing')
    if objection == 'soft' and not checks['specific_close_attempted']:
        critical.append('soft objection had no specific close')
    if any(term in agent_text for term in ('you are underinsured', 'definitely save', 'your insurer removed')):
        critical.append('unsupported insurance claim')
    if any(term in user_text for term in ('do not call', "don't call", 'remove my number')) and not any(term in agent_text for term in ('not to be contacted', 'mark this number')):
        critical.append('DNC not acknowledged')
    if any(term in agent_text for term in ("i'm human", 'i am human', 'real person speaking')):
        critical.append('false human representation')
    if critical:
        points = min(points, 4)
    missed = [
        key.replace('_', ' ')
        for key, passed in checks.items()
        if not passed and key not in {'objection_handled', 'compliant_ending'}
    ]
    return {
        'overall_score': min(points, 10),
        'target': 8,
        'passed': points >= 8 and not critical,
        'stage_scores': checks,
        'critical_failures': critical,
        'missed_questions': missed,
        'objection_detected': objection,
        'close_attempted': checks['specific_close_attempted'],
        'improvement_recommendation': (
            'Capture missing fields and use the next required close.'
            if missed or critical
            else 'No sales-process correction required.'
        ),
    }
