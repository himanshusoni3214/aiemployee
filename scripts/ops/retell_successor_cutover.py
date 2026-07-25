#!/usr/bin/env python3
import argparse
import json
import os
import stat
from datetime import datetime
from pathlib import Path

import httpx


RETELL_BASE_URL = 'https://api.retellai.com'
AUTHORIZATION = (
    'User explicitly authorized one controlled Retell Conversation Flow agent '
    'migration on 2026-07-25 with zero development phone calls.'
)


def parse_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text().splitlines()
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip()
    return lines, values


def update_env(path: Path, updates: dict[str, str]) -> None:
    lines, _ = parse_env(path)
    found: set[str] = set()
    output: list[str] = []
    for line in lines:
        if '=' not in line or line.lstrip().startswith('#'):
            output.append(line)
            continue
        key = line.split('=', 1)[0].strip()
        if key in updates:
            found.add(key)
            if updates[key]:
                output.append(f'{key}={updates[key]}')
            continue
        output.append(line)
    for key, value in updates.items():
        if key not in found and value:
            output.append(f'{key}={value}')
    temporary = path.with_name(path.name + '.retell-cutover.tmp')
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary.write_text('\n'.join(output) + '\n')
    temporary.chmod(mode)
    os.replace(temporary, path)


class Retell:
    def __init__(self, api_key: str):
        self.client = httpx.Client(
            base_url=RETELL_BASE_URL,
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            timeout=60,
        )

    def request(self, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
        response = self.client.request(method, path, json=payload, params=params)
        if response.status_code >= 400:
            raise RuntimeError(f'Retell {method} {path} failed with HTTP {response.status_code}')
        return response.json() if response.content else {}

    def get_agent(self, agent_id: str) -> dict:
        return self.request('GET', f'/get-agent/{agent_id}')

    def get_number(self, phone_number: str) -> dict:
        return self.request('GET', f'/get-phone-number/{phone_number}')

    def assign(self, phone_number: str, agent_id: str, version: int) -> dict:
        return self.request('PATCH', f'/update-phone-number/{phone_number}', {
            'inbound_agents': [],
            'outbound_agents': [{'agent_id': agent_id, 'agent_version': version, 'weight': 1}],
        })


def normalized_assignments(number: dict, direction: str = 'outbound_agents') -> list[dict]:
    return [
        {
            'agent_id': str(item.get('agent_id') or ''),
            'agent_version': int(item.get('agent_version') or 0),
            'weight': float(item.get('weight') or 0),
        }
        for item in (number.get(direction) or [])
        if isinstance(item, dict)
    ]


def exact_assignment(number: dict, agent_id: str, version: int) -> bool:
    return normalized_assignments(number) == [{
        'agent_id': agent_id,
        'agent_version': version,
        'weight': 1.0,
    }]


def persist_migration(receipt: dict, rollback_status: str) -> None:
    from app.core.db import SessionLocal
    from app.models.entities import RetellAgentMigration

    with SessionLocal() as db:
        row = db.query(RetellAgentMigration).filter(
            RetellAgentMigration.successor_agent_id == receipt['successor_agent_id']
        ).one_or_none()
        if not row:
            row = RetellAgentMigration(
                legacy_agent_id=receipt['legacy_agent_id'],
                successor_agent_id=receipt['successor_agent_id'],
                conversation_flow_id=receipt['conversation_flow_id'],
                user_authorization=AUTHORIZATION,
                old_number_assignment=receipt['old_number_assignment'],
                new_number_assignment=receipt.get('new_number_assignment') or {},
                test_result=receipt.get('test_result') or {},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(row)
        row.cutover_at = datetime.fromisoformat(receipt['cutover_at']) if receipt.get('cutover_at') else None
        row.rollback_status = rollback_status
        row.new_number_assignment = receipt.get('new_number_assignment') or {}
        row.test_result = receipt.get('test_result') or {}
        row.updated_at = datetime.utcnow()
        db.commit()


def validate_agent(agent: dict, agent_id: str, flow_id: str, published_version: int) -> None:
    if agent.get('agent_id') != agent_id or int(agent.get('version') or 0) != published_version:
        raise RuntimeError('Successor identity or published version does not match the cutover request')
    if not agent.get('is_published'):
        raise RuntimeError('Successor must be published before cutover')
    engine = agent.get('response_engine') or {}
    if engine.get('type') != 'conversation-flow' or engine.get('conversation_flow_id') != flow_id:
        raise RuntimeError('Successor is not attached to the approved Conversation Flow')


def cutover(args: argparse.Namespace, retell: Retell, env_values: dict[str, str]) -> dict:
    receipt_path = Path(args.receipt)
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
    else:
        current = retell.get_number(args.phone_number)
        if normalized_assignments(current, 'inbound_agents'):
            raise RuntimeError('Inbound assignment must be empty before cutover')
        if not exact_assignment(current, args.legacy_agent_id, args.legacy_version):
            raise RuntimeError('Legacy agent must be the only outbound assignment before cutover')
        receipt = {
            'status': 'planned',
            'legacy_agent_id': args.legacy_agent_id,
            'legacy_version': args.legacy_version,
            'successor_agent_id': args.successor_agent_id,
            'successor_version': args.successor_version,
            'conversation_flow_id': args.flow_id,
            'phone_number_masked': '*' * (len(args.phone_number) - 4) + args.phone_number[-4:],
            'old_number_assignment': {
                'inbound_agents': normalized_assignments(current, 'inbound_agents'),
                'outbound_agents': normalized_assignments(current),
            },
            'old_env': {
                key: env_values.get(key, '')
                for key in (
                    'RETELL_AGENT_ID',
                    'RETELL_PERMANENT_AGENT_ID',
                    'RETELL_LEGACY_AGENT_ID',
                    'RETELL_AGENT_VERSION',
                )
            },
            'test_result': json.loads(Path(args.test_result).read_text()),
        }
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))

    if receipt.get('successor_agent_id') != args.successor_agent_id:
        raise RuntimeError('Receipt belongs to a different successor agent')
    validate_agent(
        retell.get_agent(args.successor_agent_id),
        args.successor_agent_id,
        args.flow_id,
        args.successor_version,
    )
    current = retell.get_number(args.phone_number)
    if not exact_assignment(current, args.successor_agent_id, args.successor_version):
        if not exact_assignment(current, args.legacy_agent_id, args.legacy_version):
            raise RuntimeError('Refusing cutover from an unexpected number assignment')
        retell.assign(args.phone_number, args.successor_agent_id, args.successor_version)
    verified = retell.get_number(args.phone_number)
    if normalized_assignments(verified, 'inbound_agents') or not exact_assignment(
        verified, args.successor_agent_id, args.successor_version
    ):
        retell.assign(args.phone_number, args.legacy_agent_id, args.legacy_version)
        raise RuntimeError('Successor number assignment verification failed; legacy assignment restored')

    try:
        update_env(Path(args.env_file), {
            'RETELL_LEGACY_AGENT_ID': args.legacy_agent_id,
            'RETELL_AGENT_ID': args.successor_agent_id,
            'RETELL_PERMANENT_AGENT_ID': args.successor_agent_id,
            'RETELL_AGENT_VERSION': str(args.successor_version),
        })
        receipt['status'] = 'cutover_complete'
        receipt['cutover_at'] = datetime.utcnow().isoformat()
        receipt['new_number_assignment'] = {
            'inbound_agents': normalized_assignments(verified, 'inbound_agents'),
            'outbound_agents': normalized_assignments(verified),
        }
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
        persist_migration(receipt, 'not_required')
    except Exception:
        retell.assign(args.phone_number, args.legacy_agent_id, args.legacy_version)
        update_env(Path(args.env_file), receipt['old_env'])
        raise
    return receipt


def validate_rollback(args: argparse.Namespace, retell: Retell) -> dict:
    receipt = json.loads(Path(args.receipt).read_text())
    if receipt.get('status') != 'cutover_complete':
        raise RuntimeError('Cutover receipt is not complete')
    old = receipt.get('old_number_assignment') or {}
    old_outbound = old.get('outbound_agents') or []
    if len(old_outbound) != 1 or old_outbound[0].get('agent_id') != args.legacy_agent_id:
        raise RuntimeError('Rollback receipt does not contain exactly one legacy assignment')
    current = retell.get_number(args.phone_number)
    if not exact_assignment(current, args.successor_agent_id, args.successor_version):
        raise RuntimeError('Rollback validation requires the successor to be the sole current assignment')
    return {
        'rollback_ready': True,
        'would_assign_only': args.legacy_agent_id,
        'would_restore_env': receipt['old_env'],
        'no_simultaneous_assignment': True,
    }


def rollback(args: argparse.Namespace, retell: Retell) -> dict:
    receipt = json.loads(Path(args.receipt).read_text())
    current = retell.get_number(args.phone_number)
    if not exact_assignment(current, args.successor_agent_id, args.successor_version):
        raise RuntimeError('Refusing rollback from an unexpected number assignment')
    retell.assign(args.phone_number, args.legacy_agent_id, args.legacy_version)
    restored = retell.get_number(args.phone_number)
    if normalized_assignments(restored, 'inbound_agents') or not exact_assignment(
        restored, args.legacy_agent_id, args.legacy_version
    ):
        raise RuntimeError('Legacy assignment verification failed during rollback')
    update_env(Path(args.env_file), receipt['old_env'])
    receipt['status'] = 'rolled_back'
    receipt['rolled_back_at'] = datetime.utcnow().isoformat()
    Path(args.receipt).write_text(json.dumps(receipt, indent=2, sort_keys=True))
    persist_migration(receipt, 'completed')
    return receipt


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument('--mode', choices=('cutover', 'validate-rollback', 'rollback'), required=True)
    value.add_argument('--env-file', default='.env.production')
    value.add_argument('--receipt', required=True)
    value.add_argument('--test-result')
    value.add_argument('--phone-number', required=True)
    value.add_argument('--legacy-agent-id', required=True)
    value.add_argument('--legacy-version', type=int, required=True)
    value.add_argument('--successor-agent-id', required=True)
    value.add_argument('--successor-version', type=int, required=True)
    value.add_argument('--flow-id', required=True)
    return value


def main() -> None:
    args = parser().parse_args()
    _, env_values = parse_env(Path(args.env_file))
    api_key = os.environ.get('RETELL_API_KEY') or env_values.get('RETELL_API_KEY')
    if not api_key:
        raise RuntimeError('RETELL_API_KEY is required')
    retell = Retell(api_key)
    if args.mode == 'cutover':
        if not args.test_result:
            raise RuntimeError('--test-result is required for cutover')
        result = cutover(args, retell, env_values)
    elif args.mode == 'validate-rollback':
        result = validate_rollback(args, retell)
    else:
        result = rollback(args, retell)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
