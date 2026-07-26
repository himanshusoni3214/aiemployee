#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

import httpx


BASE_URL = 'https://api.retellai.com'
MODELS = ('gpt-4.1-mini', 'gpt-4.1')
MODEL_COST_PER_MINUTE = {
    'gpt-4.1-nano': 0.004,
    'gpt-4.1-mini': 0.016,
    'gpt-4.1': 0.045,
}


def transcript_messages(transcript: str) -> list[dict]:
    messages = []
    for line in (transcript or '').splitlines():
        if line.lower().startswith('agent:'):
            messages.append({'role': 'agent', 'content': line.split(':', 1)[1].strip()})
        elif line.lower().startswith('user:'):
            messages.append({'role': 'user', 'content': line.split(':', 1)[1].strip()})
    return messages


def base_variables() -> dict[str, str]:
    return {
        'customer_name': 'Himanshu',
        'assistant_name': 'Ava',
        'agent_name': 'Himanshu Soni',
        'agent_role': 'Allstate Sales Agent',
        'company_name': 'Allstate',
        'agency_location': 'Scarborough, Ontario',
        'campaign_name': 'Allstate Quote Appointment Calling',
        'call_purpose': 'Internal playground test with no phone call',
        'insurance_interest': 'Auto and home insurance',
        'consent_source': 'Internal self-test',
        'consent_date': '2026-07-25',
        'booking_timezone': 'America/Toronto',
        'internal_test': 'true',
        'recording_disclosure_enabled': 'true',
        'recording_disclosure': 'This internal test may be recorded and transcribed.',
        'consent_validated_for_called_number': 'true',
        'voryx_call_attempt_id': 'playground-no-live-tools',
    }


def tool_mocks() -> list[dict]:
    return [
        {
            'tool_name': 'voryx_get_quote_slots',
            'input_match_rule': {'type': 'any'},
            'output': json.dumps({
                'ok': True,
                'slot_one': 'Wednesday, July 29 at 6:30 PM',
                'slot_two': 'Saturday, August 1 at 10:30 AM',
                'slots': [
                    {'date': '2026-07-29', 'time': '18:30', 'timezone': 'America/Toronto'},
                    {'date': '2026-08-01', 'time': '10:30', 'timezone': 'America/Toronto'},
                ],
            }),
        },
        {
            'tool_name': 'voryx_book_quote_appointment',
            'input_match_rule': {'type': 'any'},
            'output': json.dumps({'ok': True, 'appointment_id': 'playground-only', 'status': 'requested'}),
        },
        {
            'tool_name': 'voryx_mark_do_not_call',
            'input_match_rule': {'type': 'any'},
            'output': json.dumps({'ok': True, 'suppressed': True, 'playground_only': True}),
        },
    ]


def standard_scenarios(last_two_calls: list[dict]) -> list[dict]:
    exact_one = transcript_messages(last_two_calls[0].get('transcript') or '')
    exact_two = transcript_messages(last_two_calls[1].get('transcript') or '')
    if exact_one and exact_one[-1].get('role') == 'agent':
        exact_one = exact_one[:-1]
    return [
        {
            'name': 'exact_failed_call_one_renewal',
            'starting_node': 'renewal_capture',
            'messages': exact_one,
            'expected_nodes': {'renewal_capture', 'renewal_callback'},
            'required_terms': {'month', 'renew'},
            'critical': True,
        },
        {
            'name': 'exact_failed_call_two_opening',
            'starting_node': 'opening',
            'messages': exact_two[:2],
            'expected_nodes': {'purpose'},
            'required_terms': {'reason', 'coverage', 'auto', 'property'},
            'critical': True,
        },
        {
            'name': 'opening_no_means_continue',
            'starting_node': 'opening',
            'messages': [
                {'role': 'agent', 'content': 'Is now a bad time for a quick conversation?'},
                {'role': 'user', 'content': 'No.'},
            ],
            'expected_nodes': {'purpose'},
            'required_terms': {'reason', 'coverage', 'auto', 'property'},
            'critical': True,
        },
        {
            'name': 'purpose_product_interest',
            'starting_node': 'purpose',
            'messages': [{'role': 'user', 'content': 'Auto insurance.'}],
            'expected_nodes': {'insurance_status'},
            'required_terms': {'insured'},
        },
        {
            'name': 'call_near_renewal',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'Call me when my policy renews.'}],
            'expected_nodes': {'renewal_capture'},
            'required_terms': {'month', 'renew'},
            'critical': True,
        },
        {
            'name': 'renewal_month_omitted',
            'starting_node': 'renewal_capture',
            'messages': [{'role': 'user', 'content': 'It is sometime in the fall.'}],
            'expected_nodes': {'renewal_capture'},
            'required_terms': {'september', 'october', 'november', 'month'},
            'critical': True,
        },
        {
            'name': 'renewal_month_captured',
            'starting_node': 'renewal_capture',
            'messages': [{'role': 'user', 'content': 'It renews in October.'}],
            'expected_nodes': {'coverage_review'},
            'required_terms': {'last time', 'coverage', 'review'},
        },
        {
            'name': 'already_insured',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'I already have insurance.'}],
            'expected_nodes': {'soft_reframe'},
            'required_terms': {'coverage', 'coverages', 'review', 'second opinion'},
            'critical': True,
        },
        {
            'name': 'happy_with_insurer',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'I am happy with my current insurer.'}],
            'expected_nodes': {'soft_reframe'},
            'required_terms': {'review', 'staying', 'second opinion'},
            'critical': True,
        },
        {
            'name': 'not_switching',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'I am not looking to switch.'}],
            'expected_nodes': {'soft_reframe'},
            'required_terms': {'no obligation', 'second opinion', 'review'},
        },
        {
            'name': 'busy',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'I am busy.'}],
            'expected_nodes': {'busy_callback'},
            'required_terms': {'later today', 'another day', 'time'},
            'critical': True,
        },
        {
            'name': 'send_information',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'Send me information.'}],
            'expected_nodes': {'soft_reframe'},
            'required_terms': {'short call', 'before', 'after', 'information'},
        },
        {
            'name': 'speak_to_spouse',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'I need to speak to my wife.'}],
            'expected_nodes': {'soft_reframe'},
            'required_terms': {'both', 'join', 'time'},
        },
        {
            'name': 'already_has_agent',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'I already have an agent.'}],
            'expected_nodes': {'soft_reframe'},
            'required_terms': {'last time', 'review', 'coverage'},
        },
        {
            'name': 'price_only',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'I only care about price.'}],
            'expected_nodes': {'soft_reframe'},
            'required_terms': {'cost', 'included', 'protect', 'price'},
        },
        {
            'name': 'first_neutral_rejection',
            'starting_node': 'objection_classifier',
            'messages': [{'role': 'user', 'content': 'No thanks, I am not interested.'}],
            'expected_nodes': {'neutral_reframe'},
            'required_terms': {'before i let you go', 'last time', 'review'},
            'critical': True,
        },
        {
            'name': 'second_clear_refusal',
            'starting_node': 'neutral_reframe',
            'messages': [
                {'role': 'agent', 'content': 'Before I let you go, when was the last coverage review?'},
                {'role': 'user', 'content': 'No, I am definitely not interested.'},
            ],
            'expected_nodes': {'end'},
            'required_terms': {'himanshu', 'contact', 'thank'},
            'critical': True,
        },
        {
            'name': 'dnc',
            'starting_node': 'purpose',
            'messages': [{'role': 'user', 'content': 'Do not call me again. Remove my number.'}],
            'expected_nodes': {'dnc', 'end'},
            'required_terms': {'not to be contacted', 'mark this number', 'understood'},
            'critical': True,
            'required_tool': 'voryx_mark_do_not_call',
        },
        {
            'name': 'appointment_accepted',
            'starting_node': 'appointment_close',
            'messages': [{'role': 'user', 'content': 'A weekday evening is easier.'}],
            'expected_nodes': {'appointment_close', 'appointment_result'},
            'required_terms': {'wednesday', 'saturday', 'which works'},
            'required_tool': 'voryx_get_quote_slots',
            'critical': True,
        },
        {
            'name': 'both_slots_rejected',
            'starting_node': 'appointment_close',
            'messages': [
                {'role': 'agent', 'content': 'Himanshu has Wednesday at 6:30 PM or Saturday at 10:30 AM. Which works better?'},
                {'role': 'user', 'content': 'Neither works for me.'},
            ],
            'expected_nodes': {'appointment_close', 'renewal_capture'},
            'required_terms': {'day', 'time', 'callback', 'renew'},
        },
        {
            'name': 'specific_renewal_callback',
            'starting_node': 'renewal_callback',
            'messages': [{'role': 'user', 'content': 'October, about two weeks before, weekday evening. Yes, call me then.'}],
            'expected_nodes': {'callback_confirmation', 'extract_state'},
            'required_terms': {'october', 'two weeks', 'weekday evening', 'confirm'},
            'critical': True,
        },
        {
            'name': 'scam_concern',
            'starting_node': 'purpose',
            'messages': [{'role': 'user', 'content': 'How do I know this is not a scam?'}],
            'expected_nodes': {'trust'},
            'required_terms': {'himanshu', 'payment', 'banking', 'callback'},
            'critical': True,
        },
        {
            'name': 'direct_automation_question',
            'starting_node': 'opening',
            'messages': [{'role': 'user', 'content': 'Are you an AI or a real person?'}],
            'expected_nodes': {'automation'},
            'required_terms': {'automated', 'assistant', 'cannot provide insurance advice'},
            'critical': True,
        },
        {
            'name': 'july_change_boundary',
            'starting_node': 'july_review',
            'messages': [{'role': 'user', 'content': 'Does this mean I am underinsured?'}],
            'expected_nodes': {'july_review', 'appointment_close'},
            'required_terms': {'himanshu', 'cannot', 'review', 'choices'},
            'forbidden_terms': {'you are definitely underinsured', 'your coverage is inadequate'},
            'critical': True,
        },
        {
            'name': 'recording_objection',
            'starting_node': 'purpose',
            'messages': [{'role': 'user', 'content': 'I do not want to be recorded.'}],
            'expected_nodes': {'trust', 'end'},
            'required_terms': {'recorded', 'transcribed', 'end', 'stop'},
            'critical': True,
        },
        {
            'name': 'interruption_recovery',
            'starting_node': 'purpose',
            'messages': [
                {'role': 'agent', 'content': 'The reason for my call is to see whether your current auto or property coverage...'},
                {'role': 'user', 'content': 'Wait, who is Himanshu?'},
            ],
            'expected_nodes': {'trust', 'purpose'},
            'required_terms': {'himanshu soni', 'allstate', 'scarborough'},
        },
        {
            'name': 'silence_reminder_contract',
            'starting_node': 'opening',
            'messages': [{'role': 'user', 'content': '[silence]'}],
            'expected_nodes': {'opening'},
            'required_terms': {'there', 'hear', 'time'},
        },
    ]


def response_text(messages: list[dict]) -> str:
    return ' '.join(
        str(message.get('content') or '')
        for message in messages
        if message.get('role') in {'agent', 'tool_call_result'}
    ).lower()


def visited_nodes(response: dict) -> set[str]:
    nodes = {str(response.get('current_node_id') or '')}
    for message in response.get('messages') or []:
        if message.get('role') == 'node_transition':
            nodes.add(str(message.get('new_node_id') or ''))
    return {value for value in nodes if value}


def invoked_tools(response: dict) -> set[str]:
    return {
        str(message.get('name') or '')
        for message in response.get('messages') or []
        if message.get('role') == 'tool_call_invocation'
    }


def scenario_passed(scenario: dict, response: dict) -> tuple[bool, list[str]]:
    failures = []
    nodes = visited_nodes(response)
    text = response_text(response.get('messages') or [])
    if not nodes.intersection(scenario['expected_nodes']):
        failures.append(f"expected node {sorted(scenario['expected_nodes'])}, got {sorted(nodes)}")
    required = scenario.get('required_terms') or set()
    if required and not any(term in text for term in required):
        failures.append(f"missing response term from {sorted(required)}")
    forbidden = scenario.get('forbidden_terms') or set()
    if any(term in text for term in forbidden):
        failures.append('forbidden insurance claim')
    required_tool = scenario.get('required_tool')
    if required_tool and required_tool not in invoked_tools(response):
        failures.append(f'missing mocked tool invocation {required_tool}')
    return not failures, failures


def score_results(results: list[dict]) -> int:
    passed = {item['name']: item['passed'] for item in results}
    points = 0
    points += int(passed.get('opening_no_means_continue', False))
    points += int(passed.get('busy', False))
    points += int(passed.get('purpose_product_interest', False))
    points += int(passed.get('renewal_month_captured', False) and passed.get('call_near_renewal', False))
    points += int(passed.get('already_has_agent', False))
    points += 2 if all(passed.get(name, False) for name in ('already_insured', 'happy_with_insurer', 'first_neutral_rejection')) else 0
    points += int(passed.get('appointment_accepted', False))
    points += int(passed.get('specific_renewal_callback', False))
    points += int(passed.get('dnc', False) and passed.get('second_clear_refusal', False))
    return points


def run_model(client: httpx.Client, receipt: dict, model: str, scenarios: list[dict]) -> dict:
    flow_id = receipt['conversation_flow_id']
    flow_version = receipt['conversation_flow_version']
    current_flow = client.get(
        f'/get-conversation-flow/{flow_id}',
        params={'version': flow_version},
    )
    current_flow.raise_for_status()
    current_model = ((current_flow.json().get('model_choice') or {}).get('model'))
    if current_model != model:
        update = client.patch(
            f'/update-conversation-flow/{flow_id}',
            params={'version': flow_version},
            json={'model_choice': {'type': 'cascading', 'model': model, 'high_priority': False}},
        )
        update.raise_for_status()
    results = []
    for scenario in scenarios:
        shared_payload = {
            'current_node_id': scenario['starting_node'],
            'dynamic_variables': base_variables(),
            'tool_mocks': tool_mocks(),
        }
        warmup = client.post(
            f"/agent-playground-completion/{receipt['successor_agent_id']}",
            params={'version': receipt['successor_version']},
            json={**shared_payload, 'messages': []},
        )
        if warmup.status_code >= 400:
            results.append({
                'name': scenario['name'],
                'starting_node': scenario['starting_node'],
                'passed': False,
                'failures': [f'warmup HTTP {warmup.status_code}'],
                'critical': bool(scenario.get('critical')),
            })
            continue
        warmup_response = warmup.json()
        warmup_messages = warmup_response.get('messages') or []
        completion = client.post(
            f"/agent-playground-completion/{receipt['successor_agent_id']}",
            params={'version': receipt['successor_version']},
            json={
                **shared_payload,
                'messages': [*warmup_messages, *scenario['messages']],
            },
        )
        if completion.status_code >= 400:
            results.append({
                'name': scenario['name'],
                'starting_node': scenario['starting_node'],
                'passed': False,
                'failures': [f'HTTP {completion.status_code}'],
                'critical': bool(scenario.get('critical')),
            })
            continue
        response = completion.json()
        trace_response = {
            **response,
            'messages': [*warmup_messages, *(response.get('messages') or [])],
        }
        passed, failures = scenario_passed(scenario, trace_response)
        trace_text = response_text(trace_response['messages'])
        results.append({
            'name': scenario['name'],
            'starting_node': scenario['starting_node'],
            'detected_intent': scenario['name'].replace('_', ' '),
            'next_nodes': sorted(visited_nodes(trace_response)),
            'variable_captured': scenario.get('variable_captured'),
            'sales_response': trace_text[:500],
            'close_attempted': any(term in trace_text for term in ('weekday', 'weekend', 'callback', 'which works')),
            'final_outcome': 'ended' if response.get('call_ended') else 'continued',
            'tools_invoked': sorted(invoked_tools(trace_response)),
            'api_current_node_id': response.get('current_node_id'),
            'api_warmup_node_id': warmup_response.get('current_node_id'),
            'api_warmup_messages': warmup_response.get('messages') or [],
            'api_dynamic_variables': response.get('dynamic_variables') or {},
            'api_messages': response.get('messages') or [],
            'api_call_ended': bool(response.get('call_ended')),
            'passed': passed,
            'failures': failures,
            'critical': bool(scenario.get('critical')),
        })
    score = score_results(results)
    critical_failures = [item['name'] for item in results if item['critical'] and not item['passed']]
    return {
        'model': model,
        'sales_score': score,
        'passed': score >= 8 and not critical_failures,
        'critical_failures': critical_failures,
        'scenario_count': len(results),
        'scenario_pass_count': sum(item['passed'] for item in results),
        'scenarios': results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt', required=True)
    parser.add_argument('--last-two-calls', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model', action='append', choices=MODELS)
    parser.add_argument('--scenario', action='append')
    args = parser.parse_args()
    receipt = json.loads(Path(args.receipt).read_text())
    last_two = json.loads(Path(args.last_two_calls).read_text())
    scenarios = standard_scenarios(last_two)
    if args.scenario:
        selected_scenarios = set(args.scenario)
        scenarios = [scenario for scenario in scenarios if scenario['name'] in selected_scenarios]
        missing = selected_scenarios - {scenario['name'] for scenario in scenarios}
        if missing:
            raise RuntimeError(f"Unknown scenarios: {', '.join(sorted(missing))}")
    models = tuple(args.model or MODELS)
    headers = {
        'Authorization': f"Bearer {os.environ['RETELL_API_KEY']}",
        'Content-Type': 'application/json',
    }
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=180) as client:
        model_results = [run_model(client, receipt, model, scenarios) for model in models]
        passing = [result for result in model_results if result['passed']]
        selected = next((result for result in passing if result['model'] == 'gpt-4.1-mini'), None)
        selected = selected or (passing[0] if passing else None)
        if selected:
            current_flow = client.get(
                f"/get-conversation-flow/{receipt['conversation_flow_id']}",
                params={'version': receipt['conversation_flow_version']},
            )
            current_flow.raise_for_status()
            current_model = ((current_flow.json().get('model_choice') or {}).get('model'))
            if current_model != selected['model']:
                final_update = client.patch(
                    f"/update-conversation-flow/{receipt['conversation_flow_id']}",
                    params={'version': receipt['conversation_flow_version']},
                    json={'model_choice': {'type': 'cascading', 'model': selected['model'], 'high_priority': False}},
                )
                final_update.raise_for_status()
    result = {
        'passed': bool(selected),
        'selected_model': selected['model'] if selected else None,
        'sales_score': selected['sales_score'] if selected else 0,
        'critical_failures': selected['critical_failures'] if selected else ['no_model_passed'],
        'model_results': model_results,
        'projected_model_cost_per_minute_usd': MODEL_COST_PER_MINUTE,
        'real_phone_calls': 0,
        'live_tools_executed': 0,
    }
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps({
        'passed': result['passed'],
        'selected_model': result['selected_model'],
        'sales_score': result['sales_score'],
        'critical_failures': result['critical_failures'],
        'models': [
            {
                'model': item['model'],
                'score': item['sales_score'],
                'passed': item['passed'],
                'scenarios': f"{item['scenario_pass_count']}/{item['scenario_count']}",
                'critical_failures': item['critical_failures'],
            }
            for item in model_results
        ],
    }, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
