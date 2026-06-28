#!/usr/bin/env python3
import argparse
import csv
import json
import re
import subprocess
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TORONTO = "America/Toronto"
SUCCESS_STATUSES = {"sent", "delivered", "accepted", "queued_by_provider"}
MESSAGE_ID_KEYS = ("message_id", "smtp_id", "smtp_response", "provider_message_id", "receipt_id")


def parse_args():
    parser = argparse.ArgumentParser(description="Generate deterministic Brew It by Sash daily outreach report.")
    parser.add_argument("--date", dest="report_date", default=None, help="Toronto report date, YYYY-MM-DD. Defaults to today in Toronto.")
    parser.add_argument("--data-root", default="/opt/data")
    parser.add_argument("--output", default="/opt/data/home/leads/brew_daily_report.txt")
    parser.add_argument("--recipient", default="himanshusoni3214@gmail.com")
    parser.add_argument("--send", action="store_true")
    return parser.parse_args()


def day_window(value, timezone_name=TORONTO):
    tz = ZoneInfo(timezone_name)
    report_date = date.fromisoformat(value) if value else datetime.now(tz).date()
    local_start = datetime.combine(report_date, time.min, tzinfo=tz)
    local_end = datetime.combine(report_date, time.max, tzinfo=tz)
    return {
        "report_date": report_date,
        "timezone": timezone_name,
        "local_start": local_start,
        "local_end": local_end,
        "utc_start": local_start.astimezone(timezone.utc),
        "utc_end": local_end.astimezone(timezone.utc),
    }


def parse_dt(value, default_tz):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            if fmt == "%Y-%m-%d":
                parsed = datetime.combine(datetime.strptime(text, fmt).date(), time.min)
            else:
                parsed = datetime.strptime(text.replace("Z", "+0000"), fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=default_tz)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(timezone.utc)


def read_csv(path):
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def email_from(row):
    for key in ("recipient", "email", "Public Email", "verified_public_email", "Email"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.lower()
    return ""


def message_id_from(row):
    for key in MESSAGE_ID_KEYS:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def message_id_from_output(output):
    patterns = (
        r"(?:provider_message_id|message_id|smtp_id|receipt_id)\s*[:=]\s*([<]?[^\s,;]+[>]?)",
        r"Message-ID:\s*([<]?[^\s,;]+[>]?)",
        r"EMAIL_SENT[^\n]*\s([<][^\s,;]+[>])",
    )
    for pattern in patterns:
        match = re.search(pattern, output or "", flags=re.I)
        if match:
            return match.group(1).strip()
    return ""


def timestamp_from(row, default_tz):
    for key in ("sent_at", "attempted_at", "timestamp", "created_at", "date"):
        parsed = parse_dt(row.get(key), default_tz)
        if parsed:
            return parsed
    return None


def evidence(path, rows_examined, rows_included, filter_used, missing_columns, window):
    stat = path.stat() if path.exists() else None
    return {
        "source_file": str(path),
        "file_modified_at_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else None,
        "rows_examined": rows_examined,
        "rows_included": rows_included,
        "filter_used": filter_used,
        "date_boundaries": {
            "timezone": window["timezone"],
            "local_start": window["local_start"].isoformat(),
            "local_end": window["local_end"].isoformat(),
            "utc_start": window["utc_start"].isoformat(),
            "utc_end": window["utc_end"].isoformat(),
        },
        "missing_columns": missing_columns,
    }


def metric(value, verified, source, note=""):
    return {"value": value, "verified": verified, "source": source, "note": note}


def generate(data_root, report_date=None):
    root = Path(data_root)
    leads_dir = root / "home" / "leads"
    window = day_window(report_date)
    tz = ZoneInfo(TORONTO)
    report_evidence = []
    blockers = []

    lead_files = sorted(leads_dir.glob("leads*.csv"))
    leads_created_today = 0
    leads_verified_today = 0
    timestamped_lead_rows = 0
    missing_lead_timestamps = False
    verified_available = set()
    all_lead_rows = 0
    for path in lead_files:
        rows, headers = read_csv(path)
        all_lead_rows += len(rows)
        missing = []
        if "created_at" not in headers:
            missing.append("created_at")
            missing_lead_timestamps = True
        included = 0
        verified_included = 0
        for row in rows:
            email = email_from(row)
            if email and "@" in email and "inferred" not in email:
                verified_available.add(email)
            created = parse_dt(row.get("created_at"), tz)
            if not created:
                continue
            timestamped_lead_rows += 1
            if window["utc_start"] <= created <= window["utc_end"]:
                included += 1
                if email and "@" in email:
                    verified_included += 1
        leads_created_today += included
        leads_verified_today += verified_included
        report_evidence.append(evidence(path, len(rows), included, "created_at inside selected Toronto day", missing, window))

    verified_path = leads_dir / "leads_verified.csv"
    verified_rows, verified_headers = read_csv(verified_path)
    verified_path_emails = set()
    for row in verified_rows:
        email = email_from(row)
        if email and "@" in email and "inferred" not in email:
            verified_available.add(email)
            verified_path_emails.add(email)
    report_evidence.append(evidence(verified_path, len(verified_rows), len(verified_path_emails), "unique valid public email rows", [c for c in ("Public Email", "created_at") if c not in verified_headers], window))

    event_rows = []
    for path in (root / "outreach_events.jsonl", leads_dir / "outreach_events.jsonl"):
        rows = read_jsonl(path)
        event_rows.extend(rows)
        report_evidence.append(evidence(path, len(rows), 0, "structured outreach events loaded; filtered below", [], window))

    legacy_log = root / "outreach_log.csv"
    legacy_rows, legacy_headers = read_csv(legacy_log)
    legacy_missing = [column for column in ("message_id", "campaign_id", "sent_at") if column not in legacy_headers]
    for row in legacy_rows:
        copied = dict(row)
        copied["source_file"] = str(legacy_log)
        event_rows.append(copied)

    attempts_today = 0
    sent_today = 0
    skipped_today = 0
    failed_today = 0
    included_legacy = 0
    unverified_sent_rows = 0
    seen_success_ids = set()
    for row in event_rows:
        ts = timestamp_from(row, tz)
        if not ts or not (window["utc_start"] <= ts <= window["utc_end"]):
            continue
        source_file = str(row.get("source_file") or "")
        is_brew = row.get("campaign_id") in (None, "", "brew-it-by-sash", "campaign-brew-it-by-sash-outreach") or "outreach_log.csv" in source_file
        if not is_brew:
            continue
        status = str(row.get("status") or "").strip().lower()
        attempts_today += 1
        if "outreach_log.csv" in source_file:
            included_legacy += 1
        if status in ("skipped", "skip"):
            skipped_today += 1
        if status in ("failed", "error", "bounced"):
            failed_today += 1
        if status in SUCCESS_STATUSES:
            message_id = message_id_from(row)
            if message_id:
                event_id = str(row.get("event_id") or message_id or f"{email_from(row)}:{ts.isoformat()}")
                if event_id not in seen_success_ids:
                    seen_success_ids.add(event_id)
                    sent_today += 1
            else:
                unverified_sent_rows += 1
    report_evidence.append(evidence(legacy_log, len(legacy_rows), included_legacy, "timestamp inside selected Toronto day; sent requires message_id/receipt", legacy_missing, window))

    if missing_lead_timestamps:
        blockers.append("Leads found today unavailable: lead CSV rows do not contain reliable created_at timestamps.")
    if unverified_sent_rows:
        blockers.append(f"{unverified_sent_rows} legacy sent rows were excluded because they lack durable message_id/receipt evidence.")

    metrics = {
        "leads_created_today": metric("Unavailable - source does not contain a reliable creation timestamp" if missing_lead_timestamps else leads_created_today, not missing_lead_timestamps, "lead CSV created_at"),
        "leads_verified_today": metric("Unavailable - source does not contain a reliable creation timestamp" if missing_lead_timestamps else leads_verified_today, not missing_lead_timestamps, "lead CSV created_at + email"),
        "verified_leads_available": metric(len(verified_available), True, "current verified lead CSVs"),
        "outreach_attempts_today": metric(attempts_today, True, "outreach event timestamps"),
        "emails_confirmed_sent_today": metric(sent_today, True, "success status plus durable message_id/receipt"),
        "emails_skipped_today": metric(skipped_today, True, "outreach status rows"),
        "emails_failed_today": metric(failed_today, True, "outreach status rows"),
        "replies_received_today": metric("Unverified", False, "no structured reply source"),
        "positive_replies": metric("Unverified", False, "no structured reply classification source"),
        "meetings_booked": metric("Unverified", False, "no structured meeting source"),
    }
    next_action = "Do not report sent-email volume until outreach logging includes durable message IDs; use dry-run/internal test first." if sent_today == 0 else "Continue campaign within configured limits."
    return {
        "report_date": window["report_date"].isoformat(),
        "timezone": TORONTO,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "errors_and_blockers": blockers,
        "source_summary": {"lead_files": len(lead_files), "lead_rows_examined": all_lead_rows, "timestamped_lead_rows": timestamped_lead_rows},
        "evidence": report_evidence,
        "next_recommended_action": next_action,
    }


def render(report):
    lines = [
        f"Brew It by Sash Outreach Report - {report['report_date']}",
        f"Generated UTC: {report['generated_at']}",
        f"Timezone: {report['timezone']}",
        "",
        "Metrics",
    ]
    for key, value in report["metrics"].items():
        verified = "verified" if value.get("verified") else "unverified"
        lines.append(f"- {key}: {value.get('value')} ({verified}; source: {value.get('source')})")
    lines.append("")
    lines.append("Errors and blockers")
    lines.extend([f"- {item}" for item in report.get("errors_and_blockers") or []] or ["- none"])
    lines.append("")
    lines.append("Evidence")
    for item in report.get("evidence") or []:
        lines.append(f"- {item['source_file']}: modified={item.get('file_modified_at_utc')} rows_examined={item['rows_examined']} rows_included={item['rows_included']} filter={item['filter_used']} missing_columns={','.join(item.get('missing_columns') or []) or 'none'}")
    lines.append("")
    lines.append(f"Next recommended action: {report.get('next_recommended_action')}")
    return "\n".join(lines) + "\n"


def main():
    args = parse_args()
    report = generate(args.data_root, args.report_date)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(report), encoding="utf-8")
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"REPORT_WRITTEN path={output}")
    print(f"EMAILS_CONFIRMED_SENT={report['metrics']['emails_confirmed_sent_today']['value']}")
    print(f"LEADS_CREATED_TODAY={report['metrics']['leads_created_today']['value']}")
    if args.send:
        subject = f"Brew It by Sash Outreach Report - {report['report_date']}"
        sender = Path(args.data_root) / "home" / "leads" / "send_daily_report.sh"
        completed = subprocess.run([str(sender), args.recipient, subject, str(output)], text=True, capture_output=True, check=False)
        combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        print(combined)
        message_id = message_id_from_output(combined)
        if completed.returncode != 0 or "EMAIL_SENT" not in combined or not message_id:
            raise SystemExit(f"REPORT_EMAIL_FAILED exit_code={completed.returncode} missing_provider_message_id={not bool(message_id)}")
        print(f"REPORT_EMAIL_DELIVERED recipient={args.recipient} subject={subject} provider_message_id={message_id}")


if __name__ == "__main__":
    main()
