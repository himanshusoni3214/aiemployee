import csv
import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.entities import LeadApproval, OutreachDraft, OutreachEvent
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT, provider_message_id_from_output, validate_report_recipient

TORONTO = "America/Toronto"
SUCCESS_STATUSES = {"sent", "delivered", "accepted", "queued_by_provider"}
MESSAGE_ID_KEYS = {"message_id", "smtp_id", "smtp_response", "provider_message_id", "receipt_id"}
BIBS_COMPANY_ID = "company-brew-it-by-sash"


@dataclass(frozen=True)
class DayWindow:
    report_date: date
    timezone_name: str
    local_start: datetime
    local_end: datetime
    utc_start: datetime
    utc_end: datetime


def _data_path(data_path: str | None = None) -> Path:
    raw = data_path or settings.hermes_data_path
    if not raw:
        raise ValueError("HERMES_DATA_PATH is not configured")
    return Path(raw)


def day_window(value: str | date | None = None, timezone_name: str = TORONTO) -> DayWindow:
    tz = ZoneInfo(timezone_name)
    report_date = value if isinstance(value, date) else date.fromisoformat(value) if value else datetime.now(tz).date()
    local_start = datetime.combine(report_date, time.min, tzinfo=tz)
    local_end = datetime.combine(report_date, time.max, tzinfo=tz)
    return DayWindow(
        report_date=report_date,
        timezone_name=timezone_name,
        local_start=local_start,
        local_end=local_end,
        utc_start=local_start.astimezone(timezone.utc),
        utc_end=local_end.astimezone(timezone.utc),
    )


def _parse_dt(value: Any, default_tz: ZoneInfo) -> datetime | None:
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
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(timezone.utc)


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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


def _file_evidence(path: Path, rows_examined: int, rows_included: int, filter_used: str, missing_columns: list[str], window: DayWindow) -> dict[str, Any]:
    stat = path.stat() if path.exists() else None
    return {
        "source_file": str(path),
        "file_modified_at_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else None,
        "rows_examined": rows_examined,
        "rows_included": rows_included,
        "filter_used": filter_used,
        "date_boundaries": {
            "timezone": window.timezone_name,
            "local_start": window.local_start.isoformat(),
            "local_end": window.local_end.isoformat(),
            "utc_start": window.utc_start.isoformat(),
            "utc_end": window.utc_end.isoformat(),
        },
        "missing_columns": missing_columns,
    }


def _metric(value: Any, verified: bool, source: str, note: str = "") -> dict[str, Any]:
    return {"value": value, "verified": verified, "source": source, "note": note}


def _db_outreach_metrics(window: DayWindow) -> dict[str, Any]:
    db = SessionLocal()
    try:
        start = window.utc_start.replace(tzinfo=None)
        end = window.utc_end.replace(tzinfo=None)
        events = db.query(OutreachEvent).filter(OutreachEvent.company_id == BIBS_COMPANY_ID).all()
        real_sent = [e for e in events if e.status in {"sent", "delivered", "accepted"} and e.message_id and e.sent_at and start <= e.sent_at <= end and not e.dry_run]
        dry_runs = [e for e in events if e.status == "prepared_dry_run" and e.created_at and start <= e.created_at <= end]
        internal = [e for e in events if e.status in {"internal_test_prepared", "internal_test_sent"} and e.created_at and start <= e.created_at <= end]
        approved_leads = db.query(LeadApproval).filter(LeadApproval.company_id == BIBS_COMPANY_ID, LeadApproval.state == "approved_for_outreach").count()
        approved_drafts = db.query(OutreachDraft).filter(OutreachDraft.company_id == BIBS_COMPANY_ID, OutreachDraft.status == "draft_approved").count()
        generated_drafts = db.query(OutreachDraft).filter(OutreachDraft.company_id == BIBS_COMPANY_ID).count()
        return {
            "real_emails_sent_today": len(real_sent),
            "real_message_ids": [e.message_id for e in real_sent],
            "dry_runs_prepared_today": len(dry_runs),
            "internal_tests_today": len(internal),
            "approved_leads": approved_leads,
            "drafts_generated": generated_drafts,
            "drafts_approved": approved_drafts,
            "ready_to_send": min(approved_leads, approved_drafts),
        }
    except Exception as exc:
        return {"error": str(exc)}
    finally:
        db.close()


def _email(row: dict[str, Any]) -> str:
    for key in ("recipient", "email", "Public Email", "verified_public_email", "Email"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.lower()
    return ""


def _message_id(row: dict[str, Any]) -> str:
    for key in MESSAGE_ID_KEYS:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _timestamp(row: dict[str, Any], default_tz: ZoneInfo) -> datetime | None:
    for key in ("sent_at", "attempted_at", "timestamp", "created_at", "date"):
        parsed = _parse_dt(row.get(key), default_tz)
        if parsed:
            return parsed
    return None


def generate_daily_report(report_date: str | None = None, data_path: str | None = None, timezone_name: str = TORONTO) -> dict[str, Any]:
    root = _data_path(data_path)
    leads_dir = root / "home" / "leads"
    window = day_window(report_date, timezone_name)
    tz = ZoneInfo(timezone_name)
    evidence: list[dict[str, Any]] = []
    blockers: list[str] = []

    lead_files = sorted([*leads_dir.glob("leads*.csv"), *(root / "home" / "voryx_workspaces").glob("**/leads*.csv")])
    leads_created_today = 0
    leads_verified_today = 0
    timestamped_lead_rows = 0
    legacy_lead_rows_without_timestamps = 0
    latest_lead_file = str(lead_files[-1]) if lead_files else ""
    latest_lead_file_rows = 0
    latest_lead_file_modified = ""
    legacy_latest_file = ""
    legacy_latest_file_rows = 0
    legacy_latest_file_modified = ""
    legacy_latest_mtime = 0.0
    verified_available_emails: set[str] = set()
    all_lead_rows = 0
    latest_mtime = 0.0
    for path in lead_files:
        rows, headers = _read_csv(path)
        all_lead_rows += len(rows)
        mtime = path.stat().st_mtime if path.exists() else 0.0
        if mtime >= latest_mtime:
            latest_mtime = mtime
            latest_lead_file = str(path)
            latest_lead_file_rows = len(rows)
            latest_lead_file_modified = datetime.fromtimestamp(mtime, timezone.utc).isoformat() if mtime else ""
        missing = [column for column in ("created_at",) if column not in headers]
        included_created = 0
        included_verified = 0
        if "created_at" not in headers:
            legacy_lead_rows_without_timestamps += len(rows)
            if mtime >= legacy_latest_mtime:
                legacy_latest_mtime = mtime
                legacy_latest_file = str(path)
                legacy_latest_file_rows = len(rows)
                legacy_latest_file_modified = datetime.fromtimestamp(mtime, timezone.utc).isoformat() if mtime else ""
        for row in rows:
            email = _email(row)
            if email and "@" in email and "inferred" not in email:
                verified_available_emails.add(email)
            created = _parse_dt(row.get("created_at"), tz)
            if not created:
                continue
            timestamped_lead_rows += 1
            if window.utc_start <= created <= window.utc_end:
                included_created += 1
                if email and "@" in email:
                    included_verified += 1
        leads_created_today += included_created
        leads_verified_today += included_verified
        evidence.append(_file_evidence(path, len(rows), included_created, "created_at inside selected Toronto day", missing, window))

    verified_path = leads_dir / "leads_verified.csv"
    verified_rows, verified_headers = _read_csv(verified_path)
    verified_path_emails: set[str] = set()
    for row in verified_rows:
        email = _email(row)
        if email and "@" in email and "inferred" not in email:
            verified_available_emails.add(email)
            verified_path_emails.add(email)
    evidence.append(_file_evidence(verified_path, len(verified_rows), len(verified_path_emails), "unique valid public email rows", [c for c in ("Public Email", "created_at") if c not in verified_headers], window))

    structured_event_paths = [root / "outreach_events.jsonl", leads_dir / "outreach_events.jsonl"]
    structured_rows: list[dict[str, Any]] = []
    for path in structured_event_paths:
        rows = _read_jsonl(path)
        structured_rows.extend(rows)
        evidence.append(_file_evidence(path, len(rows), 0, "structured outreach events loaded; filtered below", [], window))

    legacy_log = root / "outreach_log.csv"
    legacy_rows, legacy_headers = _read_csv(legacy_log)
    legacy_missing = [column for column in ("message_id", "campaign_id", "sent_at") if column not in legacy_headers]
    event_rows = [*structured_rows]
    for row in legacy_rows:
        copied: dict[str, Any] = dict(row)
        copied["source_file"] = str(legacy_log)
        copied["legacy_missing_message_id"] = "message_id" not in legacy_headers
        event_rows.append(copied)

    attempts_today = 0
    sent_today = 0
    skipped_today = 0
    failed_today = 0
    included_legacy = 0
    unverified_sent_rows = 0
    seen_success_ids: set[str] = set()
    for row in event_rows:
        ts = _timestamp(row, tz)
        if not ts or not (window.utc_start <= ts <= window.utc_end):
            continue
        status = str(row.get("status") or "").strip().lower()
        source_file = str(row.get("source_file") or "")
        is_brew = row.get("campaign_id") in {None, "", "brew-it-by-sash", "campaign-brew-it-by-sash-outreach"} or "outreach_log.csv" in source_file
        if not is_brew:
            continue
        attempts_today += 1
        if "outreach_log.csv" in source_file:
            included_legacy += 1
        if status in {"skipped", "skip"}:
            skipped_today += 1
        if status in {"failed", "error", "bounced"}:
            failed_today += 1
        if status in SUCCESS_STATUSES:
            message_id = _message_id(row)
            if message_id:
                event_id = str(row.get("event_id") or message_id or f"{_email(row)}:{ts.isoformat()}")
                if event_id not in seen_success_ids:
                    seen_success_ids.add(event_id)
                    sent_today += 1
            else:
                unverified_sent_rows += 1
    evidence.append(_file_evidence(legacy_log, len(legacy_rows), included_legacy, "timestamp inside selected Toronto day; sent requires message_id/receipt", legacy_missing, window))

    if legacy_lead_rows_without_timestamps:
        blockers.append(f"{legacy_lead_rows_without_timestamps} legacy lead rows are present without row timestamps; new generated CSV rows are counted by created_at.")
    if unverified_sent_rows:
        blockers.append(f"{unverified_sent_rows} legacy sent rows were excluded because they lack durable message_id/receipt evidence.")

    db_metrics = _db_outreach_metrics(window)
    metrics = {
        "leads_generated_today": _metric(leads_created_today, True, "new lead CSV created_at", "legacy files without row timestamps excluded" if legacy_lead_rows_without_timestamps else ""),
        "leads_created_today": _metric(leads_created_today, True, "new lead CSV created_at", "legacy files without row timestamps excluded" if legacy_lead_rows_without_timestamps else ""),
        "latest_lead_file": _metric(latest_lead_file or "none", bool(latest_lead_file), "Hermes lead workspace"),
        "latest_lead_file_rows": _metric(latest_lead_file_rows, True, "latest lead CSV row count"),
        "latest_lead_file_modified": _metric(latest_lead_file_modified or "none", bool(latest_lead_file_modified), "latest lead CSV modified time"),
        "legacy_latest_file": _metric(legacy_latest_file or "none", bool(legacy_latest_file), "legacy lead CSV without created_at"),
        "legacy_latest_file_rows": _metric(legacy_latest_file_rows, True, "legacy latest lead CSV row count"),
        "legacy_latest_file_modified": _metric(legacy_latest_file_modified or "none", bool(legacy_latest_file_modified), "legacy latest lead CSV modified time"),
        "leads_verified_today": _metric(leads_verified_today, True, "new lead CSV created_at + email", "legacy files without row timestamps excluded" if legacy_lead_rows_without_timestamps else ""),
        "verified_leads_available": _metric(len(verified_available_emails), True, "current verified lead CSVs"),
        "outreach_attempts_today": _metric(attempts_today, True, "structured outreach events and legacy outreach_log.csv timestamps"),
        "emails_confirmed_sent_today": _metric(sent_today, True, "success status plus durable message_id/receipt"),
        "real_emails_sent_today": _metric(db_metrics.get("real_emails_sent_today", sent_today), True, "PostgreSQL OutreachEvent receipts with message_id"),
        "dry_runs_prepared_today": _metric(db_metrics.get("dry_runs_prepared_today", 0), True, "PostgreSQL prepared_dry_run OutreachEvent rows"),
        "internal_tests_today": _metric(db_metrics.get("internal_tests_today", 0), True, "PostgreSQL internal test OutreachEvent rows"),
        "approved_leads": _metric(db_metrics.get("approved_leads", "see dashboard"), "error" not in db_metrics, "PostgreSQL lead approvals"),
        "drafts_generated": _metric(db_metrics.get("drafts_generated", "see dashboard"), "error" not in db_metrics, "PostgreSQL outreach drafts"),
        "drafts_approved": _metric(db_metrics.get("drafts_approved", "see dashboard"), "error" not in db_metrics, "PostgreSQL approved outreach drafts"),
        "ready_to_send": _metric(db_metrics.get("ready_to_send", "see dashboard"), "error" not in db_metrics, "approved leads with approved drafts"),
        "emails_skipped_today": _metric(skipped_today, True, "outreach status rows"),
        "emails_failed_today": _metric(failed_today, True, "outreach status rows"),
        "replies_received_today": _metric("Unverified", False, "no structured reply source"),
        "positive_replies": _metric("Unverified", False, "no structured reply classification source"),
        "meetings_booked": _metric("Unverified", False, "no structured meeting source"),
    }
    if db_metrics.get("error"):
        blockers.append(f"Dashboard outreach metrics unavailable: {db_metrics['error']}")
    if sent_today == 0:
        next_action = "Do not report sent-email volume until outreach logging includes durable message IDs; use dry-run/internal test first."
    elif blockers:
        next_action = "Review blockers, then continue with verified-only outreach."
    else:
        next_action = "Continue campaign within configured limits."

    lead_highlights: list[dict[str, Any]] = []
    if latest_lead_file and Path(latest_lead_file).exists():
        latest_rows, _latest_headers = _read_csv(Path(latest_lead_file))
        for row in latest_rows[:5]:
            lead_highlights.append({
                "business": row.get("business_name") or row.get("Business Name") or row.get("business") or row.get("name") or "",
                "email": _email(row),
                "website": row.get("website") or row.get("Website") or row.get("url") or "",
                "why_fit": row.get("why_fit") or row.get("Why Fit") or row.get("notes") or "Matches the Brew It By Sash lead source.",
            })
    return {
        "report_date": window.report_date.isoformat(),
        "timezone": window.timezone_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "errors_and_blockers": blockers,
        "source_summary": {"lead_files": len(lead_files), "lead_rows_examined": all_lead_rows, "timestamped_lead_rows": timestamped_lead_rows, "legacy_lead_rows_without_timestamps": legacy_lead_rows_without_timestamps, "latest_lead_file": latest_lead_file, "latest_lead_file_rows": latest_lead_file_rows, "latest_lead_file_modified": latest_lead_file_modified, "legacy_latest_file": legacy_latest_file, "legacy_latest_file_rows": legacy_latest_file_rows, "legacy_latest_file_modified": legacy_latest_file_modified},
        "outreach_status": db_metrics,
        "lead_highlights": lead_highlights,
        "evidence": evidence,
        "next_recommended_action": next_action,
    }


def _metric_value(report: dict[str, Any], key: str, default: Any = 0) -> Any:
    value = (report.get("metrics") or {}).get(key) or {}
    return value.get("value", default)


def render_report(report: dict[str, Any]) -> str:
    source = report.get("source_summary") or {}
    evidence = report.get("evidence") or []
    warnings = report.get("errors_and_blockers") or []
    latest_file = _metric_value(report, "latest_lead_file", source.get("latest_lead_file") or "none")
    latest_rows = _metric_value(report, "latest_lead_file_rows", source.get("latest_lead_file_rows") or 0)
    legacy_rows = _metric_value(report, "legacy_latest_file_rows", source.get("legacy_latest_file_rows") or 0)
    lead_note = "legacy file lacks row timestamps" if source.get("legacy_latest_file_rows") else "created_at available when present"
    outreach_status = report.get("outreach_status") or {}
    lines = [
        f"Brew It By Sash Daily Outreach Report - {report['report_date']}",
        f"Generated UTC: {report['generated_at']}",
        f"Timezone: {report['timezone']}",
        "",
        "Executive summary",
        f"- Leads generated today: {_metric_value(report, 'leads_generated_today', _metric_value(report, 'leads_created_today', 0))}",
        f"- Latest lead file: {Path(str(latest_file)).name if latest_file else 'none'}",
        f"- Lead rows in latest file: {latest_rows}",
        f"- Legacy latest file rows: {legacy_rows} ({lead_note})",
        f"- Verified leads available: {_metric_value(report, 'verified_leads_available', 0)}",
        f"- Leads approved for outreach: {_metric_value(report, 'approved_leads', 'see dashboard')}",
        f"- Drafts generated: {_metric_value(report, 'drafts_generated', 'see dashboard')}",
        f"- Drafts approved: {_metric_value(report, 'drafts_approved', 'see dashboard')}",
        f"- Ready to send: {_metric_value(report, 'ready_to_send', 'see dashboard')}",
        f"- Dry-runs prepared today: {_metric_value(report, 'dry_runs_prepared_today', 0)}",
        f"- Internal tests prepared/sent today: {_metric_value(report, 'internal_tests_today', 0)}",
        f"- Prospect emails sent: {_metric_value(report, 'real_emails_sent_today', _metric_value(report, 'emails_confirmed_sent_today', 0))}",
        f"- Message IDs: {', '.join(outreach_status.get('real_message_ids') or []) or 'none'}",
        "- Replies received: not connected",
        "- Follow-up status: disabled until reply monitor connected",
        "",
        "Today's lead highlights",
    ]
    highlights = report.get("lead_highlights") or []
    if highlights:
        for item in highlights[:5]:
            lines.append(f"- {item.get('business') or 'Unknown business'} | {item.get('email') or 'no email'} | {item.get('website') or 'no website'} | {item.get('why_fit') or 'matches campaign source'}")
    else:
        lines.append("- No lead highlight rows available in the latest file.")
    lines.extend([
        "",
        "Outreach readiness",
        "- Sender verified: see dashboard Outreach Control",
        "- Compliance settings: see dashboard Outreach Control",
        "- Prospect sending enabled: see dashboard Outreach Control",
        "- Batch ready: see dashboard Outreach Control",
        f"- Main blockers: {'; '.join(warnings[:3]) if warnings else 'none'}",
        "",
        "Files",
        f"- Latest lead CSV path: {latest_file}",
        f"- Latest report path: {source.get('latest_report_path') or 'written artifact path shown in delivery evidence'}",
        "- Dashboard: https://ops.themealz.com/campaigns?company_id=company-brew-it-by-sash",
        "",
        "Technical evidence summary",
        f"- Evidence records: {len(evidence)}",
        f"- Rows examined: {source.get('lead_rows_examined', 0)}",
        f"- Timestamped rows: {source.get('timestamped_lead_rows', 0)}",
        f"- Warnings count: {len(warnings)}",
        f"- Evidence file path: {latest_file}.json",
        "",
        f"Next recommended action: {report.get('next_recommended_action')}",
    ])
    return "\n".join(lines) + "\n"


def write_report_artifact(report: dict[str, Any], data_path: str | None = None, filename: str = "brew_daily_report.txt") -> Path:
    root = _data_path(data_path)
    output = root / "home" / "leads" / filename
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(report), encoding="utf-8")
    (output.with_suffix(output.suffix + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return output


def send_report_artifact(
    recipient: str | None,
    subject: str,
    artifact_path: Path,
    data_path: str | None = None,
    timeout_seconds: int = 60,
    report_only_acceptance: bool = True,
) -> dict[str, Any]:
    recipient = validate_report_recipient(recipient or INTERNAL_REPORT_RECIPIENT, report_only_acceptance=report_only_acceptance)
    root = _data_path(data_path)
    sender = root / "home" / "leads" / "send_daily_report.sh"
    if not sender.exists():
        return {"status": "failed", "error": f"{sender} does not exist"}
    completed = subprocess.run(
        [str(sender), recipient, subject, str(artifact_path)],
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    provider_message_id = provider_message_id_from_output(output)
    sent = completed.returncode == 0 and "EMAIL_SENT" in output and bool(provider_message_id)
    return {
        "status": "sent" if sent else "failed",
        "delivery_status": "sent" if sent else "failed",
        "recipient": recipient,
        "subject": subject,
        "artifact_path": str(artifact_path),
        "provider_message_id": provider_message_id or None,
        "sent_at": datetime.now(timezone.utc).isoformat() if sent else None,
        "exit_code": completed.returncode,
        "error": None if sent else "report sender did not return EMAIL_SENT with a durable provider_message_id",
        "output": output[-4000:],
    }
