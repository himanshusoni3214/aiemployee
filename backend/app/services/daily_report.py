import csv
import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT, provider_message_id_from_output, validate_report_recipient

TORONTO = "America/Toronto"
SUCCESS_STATUSES = {"sent", "delivered", "accepted", "queued_by_provider"}
MESSAGE_ID_KEYS = {"message_id", "smtp_id", "smtp_response", "provider_message_id", "receipt_id"}


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
        missing = [column for column in ("created_at",) if column not in headers]
        included_created = 0
        included_verified = 0
        if "created_at" not in headers:
            legacy_lead_rows_without_timestamps += len(rows)
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

    metrics = {
        "leads_generated_today": _metric(leads_created_today, True, "new lead CSV created_at", "legacy files without row timestamps excluded" if legacy_lead_rows_without_timestamps else ""),
        "leads_created_today": _metric(leads_created_today, True, "new lead CSV created_at", "legacy files without row timestamps excluded" if legacy_lead_rows_without_timestamps else ""),
        "latest_lead_file": _metric(latest_lead_file or "none", bool(latest_lead_file), "Hermes lead workspace"),
        "latest_lead_file_rows": _metric(latest_lead_file_rows, True, "latest lead CSV row count"),
        "leads_verified_today": _metric(leads_verified_today, True, "new lead CSV created_at + email", "legacy files without row timestamps excluded" if legacy_lead_rows_without_timestamps else ""),
        "verified_leads_available": _metric(len(verified_available_emails), True, "current verified lead CSVs"),
        "outreach_attempts_today": _metric(attempts_today, True, "structured outreach events and legacy outreach_log.csv timestamps"),
        "emails_confirmed_sent_today": _metric(sent_today, True, "success status plus durable message_id/receipt"),
        "emails_skipped_today": _metric(skipped_today, True, "outreach status rows"),
        "emails_failed_today": _metric(failed_today, True, "outreach status rows"),
        "replies_received_today": _metric("Unverified", False, "no structured reply source"),
        "positive_replies": _metric("Unverified", False, "no structured reply classification source"),
        "meetings_booked": _metric("Unverified", False, "no structured meeting source"),
    }
    if sent_today == 0:
        next_action = "Do not report sent-email volume until outreach logging includes durable message IDs; use dry-run/internal test first."
    elif blockers:
        next_action = "Review blockers, then continue with verified-only outreach."
    else:
        next_action = "Continue campaign within configured limits."

    return {
        "report_date": window.report_date.isoformat(),
        "timezone": window.timezone_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "errors_and_blockers": blockers,
        "source_summary": {"lead_files": len(lead_files), "lead_rows_examined": all_lead_rows, "timestamped_lead_rows": timestamped_lead_rows, "legacy_lead_rows_without_timestamps": legacy_lead_rows_without_timestamps, "latest_lead_file": latest_lead_file, "latest_lead_file_rows": latest_lead_file_rows},
        "evidence": evidence,
        "next_recommended_action": next_action,
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        f"Brew It by Sash Outreach Report - {report['report_date']}",
        f"Generated UTC: {report['generated_at']}",
        f"Timezone: {report['timezone']}",
        "",
        "Metrics",
    ]
    for key, value in report["metrics"].items():
        verified = "verified" if value.get("verified") else "unverified"
        note = f" - {value.get('note')}" if value.get("note") else ""
        lines.append(f"- {key}: {value.get('value')} ({verified}; source: {value.get('source')}){note}")
    lines.extend(["", "Errors and blockers"])
    blockers = report.get("errors_and_blockers") or []
    lines.extend([f"- {item}" for item in blockers] or ["- none"])
    lines.extend(["", "Evidence"])
    for item in report.get("evidence") or []:
        lines.append(f"- {item['source_file']}: modified={item.get('file_modified_at_utc')} rows_examined={item['rows_examined']} rows_included={item['rows_included']} filter={item['filter_used']} missing_columns={','.join(item.get('missing_columns') or []) or 'none'}")
    lines.extend(["", f"Next recommended action: {report.get('next_recommended_action')}"])
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
