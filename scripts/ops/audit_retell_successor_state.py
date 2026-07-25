#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

import httpx


def request(client: httpx.Client, method: str, path: str, payload: dict | None = None, params: dict | None = None):
    response = client.request(method, path, json=payload, params=params)
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--phone-number", required=True)
    parser.add_argument("--legacy-agent-id", required=True)
    parser.add_argument("--successor-agent-id", required=True)
    args = parser.parse_args()
    api_key = os.environ.get("RETELL_API_KEY")
    if not api_key:
        raise RuntimeError("RETELL_API_KEY is required")
    with httpx.Client(
        base_url="https://api.retellai.com",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120,
    ) as client:
        agents_payload = request(client, "GET", "/list-agents", params={"is_latest": "true", "limit": 1000})
        agents = agents_payload if isinstance(agents_payload, list) else agents_payload.get("agents", [])
        flows_payload = request(client, "GET", "/v2/list-conversation-flows")
        flows = flows_payload if isinstance(flows_payload, list) else flows_payload.get("items", [])
        calls_payload = request(client, "POST", "/v2/list-calls", {"limit": 100})
        calls = calls_payload if isinstance(calls_payload, list) else calls_payload.get("calls", [])
        number = request(client, "GET", f"/get-phone-number/{args.phone_number}")
        legacy = request(client, "GET", f"/get-agent/{args.legacy_agent_id}")
        successor = request(client, "GET", f"/get-agent/{args.successor_agent_id}")
    result = {
        "unique_agent_count": len({item.get("agent_id") for item in agents}),
        "conversation_flow_count": len({item.get("conversation_flow_id") for item in flows}),
        "call_count": len(calls),
        "number": {
            "phone_number_masked": "*" * (len(args.phone_number) - 4) + args.phone_number[-4:],
            "inbound_agents": number.get("inbound_agents") or [],
            "outbound_agents": number.get("outbound_agents") or [],
        },
        "legacy": {
            "agent_id": legacy.get("agent_id"),
            "agent_name": legacy.get("agent_name"),
            "version": legacy.get("version"),
            "is_published": legacy.get("is_published"),
            "response_engine_type": (legacy.get("response_engine") or {}).get("type"),
        },
        "successor": {
            "agent_id": successor.get("agent_id"),
            "agent_name": successor.get("agent_name"),
            "version": successor.get("version"),
            "is_published": successor.get("is_published"),
            "voice_id": successor.get("voice_id"),
            "post_call_analysis_model": successor.get("post_call_analysis_model"),
            "response_engine": successor.get("response_engine") or {},
        },
    }
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
