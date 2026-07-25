#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

import httpx

from app.services.allstate_conversation_flow import (
    FLOW_TITLE,
    LIVE_MODEL,
    POST_CALL_MODEL,
    conversation_flow_payload,
)


BASE_URL = 'https://api.retellai.com'
CANDIDATE_NAME = 'Voryx Allstate Quote Appointment Assistant - Conversation Flow Candidate'
PRODUCTION_NAME = 'Voryx Allstate Quote Appointment Assistant'
LEGACY_NAME = 'LEGACY - Voryx Allstate Retell-LLM - DO NOT USE'


class Retell:
    def __init__(self, api_key: str):
        self.client = httpx.Client(
            base_url=BASE_URL,
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            timeout=120,
        )

    def request(self, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
        response = self.client.request(method, path, json=payload, params=params)
        if response.status_code >= 400:
            raise RuntimeError(f'Retell {method} {path} failed with HTTP {response.status_code}')
        return response.json() if response.content else {}

    def list_latest_agents(self) -> list[dict]:
        response = self.request('GET', '/list-agents', params={'is_latest': 'true', 'limit': 1000})
        return response if isinstance(response, list) else response.get('agents', [])

    def list_flows(self) -> list[dict]:
        response = self.request('GET', '/v2/list-conversation-flows')
        return response if isinstance(response, list) else response.get('items', [])


def safe_agent(agent: dict) -> dict:
    return {
        key: agent.get(key)
        for key in (
            'agent_id', 'agent_name', 'version', 'is_published', 'voice_id',
            'voice_speed', 'enable_dynamic_voice_speed', 'responsiveness',
            'enable_dynamic_responsiveness', 'interruption_sensitivity',
            'enable_backchannel', 'backchannel_frequency', 'backchannel_words',
            'begin_message_delay_ms', 'voice_temperature', 'ambient_sound',
            'denoising_mode', 'handbook_config', 'reminder_max_count',
            'max_call_duration_ms', 'post_call_analysis_model', 'response_engine',
        )
    }


def write_receipt(path: Path, receipt: dict) -> None:
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True))


def create(args: argparse.Namespace, retell: Retell) -> dict:
    receipt_path = Path(args.receipt)
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    baseline = json.loads(Path(args.baseline).read_text())
    baseline_ids = {item['agent_id'] for item in baseline['agents']}
    baseline_flows = {item['conversation_flow_id'] for item in baseline['flows']}

    if not receipt.get('conversation_flow_id'):
        current_ids = {item.get('agent_id') for item in retell.list_latest_agents()}
        current_flows = {item.get('conversation_flow_id') for item in retell.list_flows()}
        if current_ids != baseline_ids or current_flows != baseline_flows:
            raise RuntimeError('Retell agent or flow inventory changed after baseline; refusing creation')
        legacy = retell.request('GET', f'/get-agent/{args.legacy_agent_id}', params={'version': args.legacy_version})
        if legacy.get('agent_id') != args.legacy_agent_id or not legacy.get('is_published'):
            raise RuntimeError('Legacy published agent baseline is unavailable')
        engine = legacy.get('response_engine') or {}
        if engine.get('type') != 'retell-llm':
            raise RuntimeError('Legacy response engine changed unexpectedly')
        llm = retell.request(
            'GET',
            f"/get-retell-llm/{engine['llm_id']}",
            params={'version': engine.get('version')},
        )
        knowledge_base_ids = list(llm.get('knowledge_base_ids') or [])
        if not knowledge_base_ids:
            raise RuntimeError('Legacy Allstate knowledge base is missing')
        flow = retell.request(
            'POST',
            '/create-conversation-flow',
            conversation_flow_payload(os.environ['RETELL_TOOL_TOKEN'], knowledge_base_ids),
        )
        receipt = {
            'status': 'flow_created',
            'flow_title': FLOW_TITLE,
            'conversation_flow_id': flow['conversation_flow_id'],
            'conversation_flow_version': int(flow['version']),
            'knowledge_base_ids': knowledge_base_ids,
            'legacy_agent_id': args.legacy_agent_id,
            'legacy_version': args.legacy_version,
            'baseline_unique_agent_count': len(baseline_ids),
            'baseline_flow_count': len(baseline_flows),
            'baseline_call_count': baseline['call_count'],
        }
        write_receipt(receipt_path, receipt)

    if not receipt.get('successor_agent_id'):
        current_ids = {item.get('agent_id') for item in retell.list_latest_agents()}
        current_flows = {item.get('conversation_flow_id') for item in retell.list_flows()}
        expected_flows = baseline_flows | {receipt['conversation_flow_id']}
        if current_ids != baseline_ids or current_flows != expected_flows:
            raise RuntimeError('Creation guard failed before successor voice agent creation')
        agent_payload = {
            'response_engine': {
                'type': 'conversation-flow',
                'conversation_flow_id': receipt['conversation_flow_id'],
                'version': receipt['conversation_flow_version'],
            },
            'voice_id': 'retell-Della',
            'agent_name': CANDIDATE_NAME,
            'version_title': FLOW_TITLE,
            'version_description': 'Authorized unassigned no-PSTN Conversation Flow migration candidate.',
            'language': 'en-US',
            'voice_speed': 0.96,
            'enable_dynamic_voice_speed': True,
            'responsiveness': 0.72,
            'enable_dynamic_responsiveness': True,
            'interruption_sensitivity': 0.80,
            'enable_backchannel': True,
            'backchannel_frequency': 0.25,
            'backchannel_words': ['mm-hmm', 'okay', 'I understand'],
            'begin_message_delay_ms': 600,
            'voice_temperature': 1.05,
            'ambient_sound': None,
            'denoising_mode': 'noise-cancellation',
            'boosted_keywords': [
                'Himanshu Soni', 'Allstate', 'Scarborough', 'Ontario',
                'renewal', 'accident benefits',
            ],
            'handbook_config': {
                'ai_disclosure': True,
                'default_personality': False,
                'natural_filler_words': False,
                'high_empathy': False,
                'speech_normalization': True,
                'scope_boundaries': True,
            },
            'reminder_max_count': 1,
            'max_call_duration_ms': 240000,
            'post_call_analysis_model': POST_CALL_MODEL,
            'webhook_url': os.environ['RETELL_WEBHOOK_URL'],
            'webhook_timeout_ms': 10000,
        }
        agent = retell.request('POST', '/create-agent', agent_payload)
        receipt['successor_agent_id'] = agent['agent_id']
        receipt['successor_version'] = int(agent['version'])
        receipt['status'] = 'candidate_created'
        receipt['candidate'] = safe_agent(agent)
        write_receipt(receipt_path, receipt)

    current_ids = {item.get('agent_id') for item in retell.list_latest_agents()}
    current_flows = {item.get('conversation_flow_id') for item in retell.list_flows()}
    expected_ids = baseline_ids | {receipt['successor_agent_id']}
    expected_flows = baseline_flows | {receipt['conversation_flow_id']}
    if current_ids != expected_ids:
        raise RuntimeError('Exactly one successor voice agent was not preserved')
    if current_flows != expected_flows:
        raise RuntimeError('Exactly one Conversation Flow was not preserved')
    number = retell.request('GET', f'/get-phone-number/{args.phone_number}')
    assignments = number.get('outbound_agents') or []
    if any(item.get('agent_id') == receipt['successor_agent_id'] for item in assignments):
        raise RuntimeError('Successor must remain unassigned during playground QA')
    if number.get('inbound_agents'):
        raise RuntimeError('Inbound assignment must remain empty')
    calls = retell.request('POST', '/v2/list-calls', {'limit': 100})
    call_items = calls if isinstance(calls, list) else calls.get('calls', [])
    if len(call_items) != receipt['baseline_call_count']:
        raise RuntimeError('Call count changed during no-call candidate creation')
    receipt['verified_unique_agent_count'] = len(current_ids)
    receipt['verified_flow_count'] = len(current_flows)
    receipt['verified_call_count'] = len(call_items)
    write_receipt(receipt_path, receipt)
    return receipt


def publish(args: argparse.Namespace, retell: Retell) -> dict:
    receipt_path = Path(args.receipt)
    receipt = json.loads(receipt_path.read_text())
    result = json.loads(Path(args.test_result).read_text())
    if not result.get('passed') or result.get('selected_model') not in {'gpt-4.1-mini', 'gpt-4.1'}:
        raise RuntimeError('Passing playground result is required before publish')
    if result.get('sales_score', 0) < 8 or result.get('critical_failures'):
        raise RuntimeError('Playground sales score or compliance gate failed')
    agent_id = receipt['successor_agent_id']
    version = receipt['successor_version']
    selected_model = result['selected_model']
    retell.request(
        'PATCH',
        f"/update-conversation-flow/{receipt['conversation_flow_id']}",
        {'model_choice': {'type': 'cascading', 'model': selected_model, 'high_priority': False}},
        params={'version': receipt['conversation_flow_version']},
    )
    flow = retell.request(
        'GET',
        f"/get-conversation-flow/{receipt['conversation_flow_id']}",
        params={'version': receipt['conversation_flow_version']},
    )
    if (flow.get('model_choice') or {}).get('model') != selected_model:
        raise RuntimeError('Selected tested model was not preserved on the Conversation Flow')
    number = retell.request('GET', f'/get-phone-number/{args.phone_number}')
    if any(item.get('agent_id') == agent_id for item in (number.get('outbound_agents') or [])):
        raise RuntimeError('Successor must remain unassigned before publish')
    retell.request('PATCH', f'/update-agent/{agent_id}', {
        'agent_name': PRODUCTION_NAME,
        'version_title': FLOW_TITLE,
    }, params={'version': version})
    if not receipt.get('published'):
        retell.request('POST', f'/publish-agent-version/{agent_id}', {'version': version})
    published = retell.request('GET', f'/get-agent/{agent_id}', params={'version': version})
    if not published.get('is_published'):
        raise RuntimeError('Successor publish verification failed')
    engine = published.get('response_engine') or {}
    if engine.get('type') != 'conversation-flow' or engine.get('conversation_flow_id') != receipt['conversation_flow_id']:
        raise RuntimeError('Published successor response engine verification failed')
    number_after = retell.request('GET', f'/get-phone-number/{args.phone_number}')
    if any(item.get('agent_id') == agent_id for item in (number_after.get('outbound_agents') or [])):
        raise RuntimeError('Successor became assigned during publish')
    if number_after.get('inbound_agents'):
        raise RuntimeError('Inbound assignment changed during publish')
    calls_after = retell.request('POST', '/v2/list-calls', {'limit': 100})
    call_items = calls_after if isinstance(calls_after, list) else calls_after.get('calls', [])
    if len(call_items) != receipt['baseline_call_count']:
        raise RuntimeError('Call count changed during no-call publish')
    receipt['status'] = 'published_unassigned'
    receipt['published'] = True
    receipt['selected_model'] = selected_model
    receipt['playground_sales_score'] = result['sales_score']
    receipt['published_agent'] = safe_agent(published)
    receipt['verified_call_count_after_publish'] = len(call_items)
    write_receipt(receipt_path, receipt)
    return receipt


def rename_legacy(args: argparse.Namespace, retell: Retell) -> dict:
    receipt_path = Path(args.receipt)
    receipt = json.loads(receipt_path.read_text())
    number = retell.request('GET', f'/get-phone-number/{args.phone_number}')
    assignments = number.get('outbound_agents') or []
    if len(assignments) != 1 or assignments[0].get('agent_id') != receipt['successor_agent_id']:
        raise RuntimeError('Legacy rename requires completed successor-only cutover')
    version_response = retell.client.get(
        f'/get-agent/{args.legacy_agent_id}',
        params={'version': args.legacy_version + 1},
    )
    if version_response.status_code == 200:
        draft = version_response.json()
    elif version_response.status_code == 404:
        draft = retell.request(
            'POST',
            f'/create-agent-version/{args.legacy_agent_id}',
            {'base_version': args.legacy_version},
        )
    else:
        raise RuntimeError(f'Legacy draft lookup failed with HTTP {version_response.status_code}')
    legacy_version = int(draft['version'])
    retell.request('PATCH', f'/update-agent/{args.legacy_agent_id}', {
        'agent_name': LEGACY_NAME,
        'version_title': 'Legacy Retell-LLM retained for rollback',
    }, params={'version': legacy_version})
    updated = retell.request('GET', f'/get-agent/{args.legacy_agent_id}', params={'version': legacy_version})
    if not updated.get('is_published'):
        retell.request('POST', f'/publish-agent-version/{args.legacy_agent_id}', {'version': legacy_version})
    legacy = retell.request('GET', f'/get-agent/{args.legacy_agent_id}', params={'version': legacy_version})
    if legacy.get('agent_name') != LEGACY_NAME or not legacy.get('is_published'):
        raise RuntimeError('Legacy rename verification failed')
    receipt['legacy_published_version'] = legacy_version
    receipt['legacy_name'] = legacy.get('agent_name')
    receipt['status'] = 'cutover_and_legacy_rename_complete'
    write_receipt(receipt_path, receipt)
    return receipt


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument('--action', choices=('create', 'publish', 'rename-legacy'), required=True)
    value.add_argument('--receipt', required=True)
    value.add_argument('--baseline')
    value.add_argument('--test-result')
    value.add_argument('--legacy-agent-id', required=True)
    value.add_argument('--legacy-version', type=int, required=True)
    value.add_argument('--phone-number', required=True)
    return value


def main() -> None:
    args = parser().parse_args()
    api_key = os.environ.get('RETELL_API_KEY')
    if not api_key:
        raise RuntimeError('RETELL_API_KEY is required')
    retell = Retell(api_key)
    if args.action == 'create':
        if not args.baseline:
            raise RuntimeError('--baseline is required for create')
        result = create(args, retell)
    elif args.action == 'publish':
        if not args.test_result:
            raise RuntimeError('--test-result is required for publish')
        result = publish(args, retell)
    else:
        result = rename_legacy(args, retell)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
