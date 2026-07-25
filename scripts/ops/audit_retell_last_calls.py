#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

import httpx


SAFE_FIELDS = (
    "call_id",
    "call_status",
    "start_timestamp",
    "end_timestamp",
    "duration_ms",
    "agent_id",
    "agent_version",
    "direction",
    "transcript",
    "transcript_object",
    "disconnection_reason",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    api_key = os.environ.get("RETELL_API_KEY")
    if not api_key:
        raise RuntimeError("RETELL_API_KEY is required")
    with httpx.Client(
        base_url="https://api.retellai.com",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120,
    ) as client:
        response = client.post("/v2/list-calls", json={"limit": 2, "sort_order": "descending"})
        response.raise_for_status()
    payload = response.json()
    calls = payload if isinstance(payload, list) else payload.get("calls", [])
    safe_calls = [{field: call.get(field) for field in SAFE_FIELDS} for call in calls[:2]]
    Path(args.output).write_text(json.dumps(safe_calls, indent=2, sort_keys=True))
    print(json.dumps({"captured_calls": len(safe_calls), "real_calls_placed": 0}))


if __name__ == "__main__":
    main()
