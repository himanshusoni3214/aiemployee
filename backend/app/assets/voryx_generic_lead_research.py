#!/usr/bin/env python3
import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text[:80] or "lead-research"


def parse_args():
    parser = argparse.ArgumentParser(description="Voryx generic safe lead research generator")
    parser.add_argument("--company-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--employee-id", default="")
    parser.add_argument("--hermes-job-id", default="")
    parser.add_argument("--industry", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--target-customer", default="")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--config", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--no-email", action="store_true", required=True)
    return parser.parse_args()


LOCKED_FIELDS = [
    "lead_id",
    "created_at",
    "company_id",
    "campaign_id",
    "employee_id",
    "hermes_job_id",
    "source_run_id",
    "business_name",
    "website",
    "email",
    "phone",
    "city",
    "category",
    "lead_status",
    "verified_at",
    "source_url",
    "source_file",
    "notes",
]


def load_config(args):
    if not args.config:
        return {}
    path = Path(args.config)
    if not path.exists():
        raise SystemExit(f"ERROR: config file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def schema_columns(config):
    schema = config.get("lead_schema") if isinstance(config.get("lead_schema"), dict) else {}
    columns = schema.get("columns") if isinstance(schema, dict) else None
    if not isinstance(columns, list) or not columns:
        columns = LOCKED_FIELDS
    result = []
    for field in columns:
        name = slug(str(field)).replace("-", "_")
        if name and name not in result:
            result.append(name)
    for field in LOCKED_FIELDS:
        if field not in result:
            result.insert(LOCKED_FIELDS.index(field), field)
    return result


def build_rows(args, limit: int, columns, source_file: str):
    industry = args.industry.strip()
    location = args.location.strip()
    target = args.target_customer.strip() or f"{industry} operators"
    exclusions = args.exclude.strip()
    row_notes = args.notes.strip() or f"Target: {target}; Exclude: {exclusions}"
    rows = []
    for index in range(limit):
        number = index + 1
        lead = {
            "lead_id": f"{slug(args.campaign_id)}-{number:04d}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "company_id": args.company_id,
            "campaign_id": args.campaign_id,
            "employee_id": args.employee_id,
            "hermes_job_id": args.hermes_job_id,
            "source_run_id": source_file.rsplit("/", 1)[-1].replace(".csv", ""),
            "business_name": f"{location} {industry.title()} Prospect {number}",
            "website": "",
            "email": "",
            "phone": "",
            "city": location,
            "category": industry,
            "lead_status": "Generated",
            "verified_at": "",
            "source_url": "",
            "source_file": source_file,
            "notes": row_notes,
            "owner_name": "",
            "instagram": "",
            "google_rating": "",
            "number_of_locations": "",
            "decision_maker_title": target,
            "priority": "",
            "call_notes": f"Target: {target}; Exclude: {exclusions}",
            "sms_status": "",
            "target_customer": target,
            "exclusions": exclusions,
            "email_sending": "false",
            "source": "voryx_generic_lead_research",
        }
        rows.append({column: lead.get(column, "") for column in columns})
    return rows


def main() -> int:
    args = parse_args()
    industry = args.industry.strip()
    location = args.location.strip()
    if not industry:
        raise SystemExit("ERROR: --industry is required")
    if not location:
        raise SystemExit("ERROR: --location is required")
    config = load_config(args)
    limit = max(min(int(args.limit or 25), 250), 1)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"leads_{slug(args.company_id)}_{slug(args.campaign_id)}_{stamp}"
    csv_path = output_dir / f"{base}.csv"
    metadata_path = output_dir / f"{base}.metadata.json"
    fields = schema_columns(config)
    rows = build_rows(args, limit, fields, str(csv_path))
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    metadata = {
        "script": "voryx_generic_lead_research.py",
        "company_id": args.company_id,
        "campaign_id": args.campaign_id,
        "employee_id": args.employee_id,
        "hermes_job_id": args.hermes_job_id,
        "industry": industry,
        "location": location,
        "target_customer": args.target_customer.strip(),
        "exclusions": args.exclude.strip(),
        "limit": limit,
        "output_file": str(csv_path),
        "lead_count": len(rows),
        "columns": fields,
        "config_path": args.config,
        "email_sending": False,
        "prospect_outreach": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(f"GENERIC_LEAD_RESEARCH_OUTPUT path={csv_path}")
    print(f"GENERIC_LEAD_RESEARCH_METADATA path={metadata_path}")
    print(f"LEADS_GENERATED={len(rows)}")
    print("EMAIL_SENDING=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
