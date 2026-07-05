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
    parser.add_argument("--industry", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--target-customer", default="")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--no-email", action="store_true", required=True)
    return parser.parse_args()


def build_rows(args, limit: int):
    industry = args.industry.strip()
    location = args.location.strip()
    target = args.target_customer.strip() or f"{industry} operators"
    exclusions = args.exclude.strip()
    rows = []
    for index in range(limit):
        number = index + 1
        rows.append({
            "company_id": args.company_id,
            "campaign_id": args.campaign_id,
            "lead_name": "",
            "business": f"{location} {industry.title()} Prospect {number}",
            "industry": industry,
            "location": location,
            "target_customer": target,
            "exclusions": exclusions,
            "email": "",
            "phone": "",
            "website": "",
            "status": "Generated",
            "source": "voryx_generic_lead_research",
            "notes": args.notes.strip(),
            "email_sending": "false",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows


def main() -> int:
    args = parse_args()
    industry = args.industry.strip()
    location = args.location.strip()
    if not industry:
        raise SystemExit("ERROR: --industry is required")
    if not location:
        raise SystemExit("ERROR: --location is required")
    limit = max(min(int(args.limit or 25), 250), 1)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"leads_{slug(args.company_id)}_{slug(args.campaign_id)}_{stamp}"
    csv_path = output_dir / f"{base}.csv"
    metadata_path = output_dir / f"{base}.metadata.json"
    rows = build_rows(args, limit)
    fields = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    metadata = {
        "script": "voryx_generic_lead_research.py",
        "company_id": args.company_id,
        "campaign_id": args.campaign_id,
        "industry": industry,
        "location": location,
        "target_customer": args.target_customer.strip(),
        "exclusions": args.exclude.strip(),
        "limit": limit,
        "output_file": str(csv_path),
        "lead_count": len(rows),
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
