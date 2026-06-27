#!/usr/bin/env python3
import csv
import datetime as dt
import hashlib
import json
import os
import smtplib
import subprocess
import uuid
from email.mime.text import MIMEText
from email.utils import make_msgid
from pathlib import Path

DATA = Path("/opt/data")
LEADS_FILE = Path(os.getenv("LEADS_FILE", "/opt/data/home/leads/leads_verified.csv"))
LEGACY_LOG = Path("/opt/data/outreach_log.csv")
EVENT_LOG = Path(os.getenv("OUTREACH_EVENT_LOG", "/opt/data/outreach_events.jsonl"))
SENDER = os.getenv("OUTREACH_SENDER", "voryxio@gmail.com")
PASSWORD_SCRIPT = os.getenv("GMAIL_PASSWORD_SCRIPT", "/opt/data/home/.secrets/gmail-pass.sh")
MAX_SEND = int(os.getenv("MAX_SEND", "5"))
DRY_RUN = os.getenv("DRY_RUN", "0").lower() in {"1", "true", "yes"}
CAMPAIGN_ID = os.getenv("CAMPAIGN_ID", "campaign-brew-it-by-sash-outreach")
COMPANY_ID = os.getenv("COMPANY_ID", "company-brew-it-by-sash")
EMPLOYEE_ID = os.getenv("EMPLOYEE_ID", "hermes-outreach-sender")
JOB_RUN_ID = os.getenv("JOB_RUN_ID", f"manual-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
PROVIDER = "gmail-smtp"
BAD_PARTS = ("inferred", "family-owned", "example.com", "test.com", "no-reply", "noreply", "(", ")", " ")


def now_utc():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def get_password():
    return subprocess.check_output([PASSWORD_SCRIPT], text=True).strip().replace('"', "").strip()


def value(row, *names):
    for name in names:
        item = (row.get(name) or "").strip()
        if item:
            return item
    return ""


def bad_email(email):
    email = email.lower().strip()
    return not email or "@" not in email or any(part in email for part in BAD_PARTS)


def already_attempted(email):
    if not LEGACY_LOG.exists():
        return False
    needle = email.lower().strip()
    with LEGACY_LOG.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.lower().startswith(needle + ","):
                return True
    return False


def stable_event_id(email, subject, job_run_id):
    raw = f"{CAMPAIGN_ID}|{email.lower()}|{subject}|{job_run_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_event(event):
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def append_legacy(email, status, business, timestamp):
    LEGACY_LOG.parent.mkdir(parents=True, exist_ok=True)
    exists = LEGACY_LOG.exists()
    with LEGACY_LOG.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if not exists:
            writer.writerow(["recipient", "status", "timestamp", "business"])
        writer.writerow([email, status, timestamp, business])


def event(row, email, business, subject, status, attempted_at, sent_at=None, message_id=None, error=None):
    return {
        "event_id": stable_event_id(email, subject, JOB_RUN_ID),
        "campaign_id": CAMPAIGN_ID,
        "company_id": COMPANY_ID,
        "employee_id": EMPLOYEE_ID,
        "lead_id": value(row, "lead_id", "id") or None,
        "recipient": email,
        "business": business,
        "subject": subject,
        "attempted_at": attempted_at,
        "sent_at": sent_at,
        "status": status,
        "message_id": message_id,
        "thread_id": None,
        "provider": PROVIDER,
        "error_code": type(error).__name__ if error else None,
        "error_message": str(error)[:1000] if error else None,
        "dry_run": DRY_RUN,
        "job_run_id": JOB_RUN_ID,
    }


def build_message(email, business, fit):
    subject = f"Local cold brew option for {business}"
    body = f"""Hi {business} team,

I am reaching out from Voryx. We are helping Brew It by Sash introduce cold brew coffee concentrate to selected cafes in Toronto.

I noticed your cafe may be a good fit because: {fit or 'your business appears aligned with premium local beverage options'}.

Would you be open to trying a sample or having a quick 10-minute conversation this week?

Best,
Himanshu
Voryx"""
    message_id = make_msgid(domain="voryx.local")
    msg = MIMEText(body)
    msg["From"] = SENDER
    msg["To"] = email
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    return subject, body, message_id, msg


def main():
    if not LEADS_FILE.exists():
        print(f"ERROR no leads file found: {LEADS_FILE}")
        return 1
    sent = 0
    attempted = 0
    with LEADS_FILE.open(newline="", encoding="utf-8", errors="replace") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        email = value(row, "Public Email", "verified_public_email", "Contact", "Email", "email").replace(" ", "")
        business = value(row, "Business Name", "business_name", "Business", "name") or "there"
        fit = value(row, "Why this is a fit for Brew It by Sash", "fit_reason", "Notes")
        if bad_email(email):
            continue
        if already_attempted(email):
            continue
        subject, _, message_id, msg = build_message(email, business, fit)
        attempted_at = now_utc()
        attempted += 1
        if DRY_RUN:
            append_event(event(row, email, business, subject, "dry_run", attempted_at, message_id=message_id))
            print(f"DRY_RUN recipient={email} message_id={message_id}")
            continue
        try:
            password = get_password()
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(SENDER, password)
                refused = server.sendmail(SENDER, [email], msg.as_string())
            if refused:
                raise RuntimeError(f"SMTP refused recipients: {refused}")
            sent_at = now_utc()
            append_event(event(row, email, business, subject, "sent", attempted_at, sent_at=sent_at, message_id=message_id))
            append_legacy(email, "sent", business, sent_at)
            print(f"EMAIL_SENT recipient={email} message_id={message_id}")
            sent += 1
        except Exception as exc:
            append_event(event(row, email, business, subject, "failed", attempted_at, error=exc))
            append_legacy(email, "failed", business, attempted_at)
            print(f"EMAIL_FAILED recipient={email} error={exc}")
        if sent >= MAX_SEND:
            break
    print(f"Outreach attempts considered: {attempted}")
    print(f"Outreach emails confirmed sent: {sent}")
    print(f"Dry run: {DRY_RUN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
