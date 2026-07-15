#!/usr/bin/env python3
import argparse
import csv
import json
import os
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
INTERNET_RESEARCH_PROVIDER_ENV = [
    "VORYX_INTERNET_RESEARCH_PROVIDER",
    "HERMES_WEB_RESEARCH_PROVIDER",
    "BRAVE_SEARCH_API_KEY",
    "SERPAPI_API_KEY",
    "TAVILY_API_KEY",
    "BING_SEARCH_API_KEY",
    "GOOGLE_SEARCH_API_KEY",
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


def clean(value):
    return str(value or "").strip()


def column_key(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def first_value(row, keys):
    lowered = {column_key(key): value for key, value in row.items()}
    for key in keys:
        value = lowered.get(column_key(key))
        if clean(value):
            return clean(value)
    return ""


def source_config(config):
    source = config.get("lead_source") if isinstance(config.get("lead_source"), dict) else {}
    return {
        "type": clean(source.get("type") or config.get("lead_source_type") or config.get("source_type") or ""),
        "file": clean(source.get("file") or source.get("path") or config.get("lead_source_file") or config.get("source_file") or config.get("uploaded_csv_path") or ""),
        "url": clean(source.get("url") or config.get("lead_source_url") or ""),
        "query": clean(source.get("query") or config.get("lead_source_query") or ""),
    }


def internet_research_provider_configured():
    return any(clean(os.getenv(name)) for name in INTERNET_RESEARCH_PROVIDER_ENV)


def safe_source_path(value):
    raw = str(value or "").strip()
    if not raw.startswith("/opt/data"):
        raise SystemExit("ERROR source_config_incomplete: upload CSV must be an absolute /opt/data path")
    data_root = Path(os.getenv("HERMES_DATA_ROOT") or os.getenv("HERMES_CONTAINER_DATA_ROOT") or "/opt/data")
    path = data_root if raw == "/opt/data" else data_root / raw.removeprefix("/opt/data/")
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        resolved = path
    allowed = data_root.resolve()
    if allowed != resolved and allowed not in resolved.parents:
        raise SystemExit("ERROR source_config_incomplete: upload CSV must be under /opt/data")
    if not path.exists():
        raise SystemExit(f"ERROR source_config_incomplete: upload CSV not found: {raw}")
    return path


def read_source_rows(config, limit):
    source = source_config(config)
    source_type = source["type"]
    if source_type == "real_directory":
        source_type = "ai_internet_research"
    if not source_type:
        source_type = "ai_internet_research"
    if source_type == "ai_internet_research":
        if not internet_research_provider_configured():
            raise SystemExit("ERROR internet_research_provider_not_configured: AI Internet Research is selected, but no web/search provider is connected. Connect a search provider or upload a lead CSV.")
        raise SystemExit("ERROR internet_research_provider_not_configured: AI Internet Research provider variable exists, but no Hermes web research adapter is installed for this script yet.")
    if source_type in {"uploaded_seed_csv", "existing_legacy_file", "manual_import"}:
        if not source["file"]:
            raise SystemExit("ERROR source_config_incomplete: upload CSV is required for CSV/manual import sources")
        path = safe_source_path(source["file"])
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            rows = list(csv.DictReader(handle))
        real_rows = [row for row in rows if first_value(row, ["business_name", "Business", "business", "Company", "company", "Name", "name"])]
        return real_rows[:limit], source, str(path)
    if source_type in {"source_urls", "search_queries"}:
        raise SystemExit("ERROR internet_research_provider_not_configured: Manual URLs/search queries still require a connected web/search provider.")
    raise SystemExit(f"ERROR source_config_incomplete: unsupported lead source type: {source_type}")


def normalize_source_row(row, args, columns, source_file, source_row_number, source_run_id):
    business_name = first_value(row, ["business_name", "Business", "business", "Company", "company", "Name", "name"])
    website = first_value(row, ["website", "Website", "url", "URL", "site", "domain"])
    email = first_value(row, ["email", "Email", "Public Email", "verified_public_email", "public_email"])
    phone = first_value(row, ["phone", "Phone", "telephone", "Telephone"])
    city = first_value(row, ["city", "City", "location", "Location"]) or args.location.strip()
    category = first_value(row, ["category", "Category", "industry", "Industry", "niche"]) or args.industry.strip()
    source_url = first_value(row, ["source_url", "Source URL", "url", "URL", "website", "Website"])
    notes = first_value(row, ["notes", "Notes", "note"]) or args.notes.strip()
    lead = {
        "lead_id": first_value(row, ["lead_id", "id", "ID"]) or f"{slug(args.campaign_id)}-{source_row_number:04d}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "company_id": args.company_id,
        "campaign_id": args.campaign_id,
        "employee_id": args.employee_id,
        "hermes_job_id": args.hermes_job_id,
        "source_run_id": source_run_id,
        "business_name": business_name,
        "website": website,
        "email": email,
        "phone": phone,
        "city": city,
        "category": category,
        "lead_status": first_value(row, ["lead_status", "status", "Status"]) or "Generated",
        "verified_at": first_value(row, ["verified_at", "verified", "Verified At"]),
        "source_url": source_url,
        "source_file": source_file,
        "notes": notes,
    }
    for column in columns:
        if column not in lead:
            lead[column] = first_value(row, [column, column.replace("_", " "), column.title().replace("_", " ")])
    return {column: lead.get(column, "") for column in columns}

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
    source_rows, source, source_file = read_source_rows(config, limit)
    if not source_rows:
        raise SystemExit("ERROR no_new_unique_leads: configured source did not contain usable business rows")
    source_run_id = csv_path.name.replace(".csv", "")
    rows = [normalize_source_row(row, args, fields, source_file, index + 1, source_run_id) for index, row in enumerate(source_rows)]
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
        "lead_source": source,
        "source_plan": config.get("source_plan") if isinstance(config.get("source_plan"), dict) else {},
        "source_file": source_file,
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
