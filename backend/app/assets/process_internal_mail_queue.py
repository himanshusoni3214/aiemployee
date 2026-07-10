#!/usr/bin/env python3
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any

DATA_ROOT = Path(os.environ.get("HERMES_DATA_ROOT", "/opt/data"))
CONTAINER_DATA_ROOT = Path(os.environ.get("HERMES_CONTAINER_DATA_ROOT", "/opt/data"))
QUEUE_ROOT = DATA_ROOT / "home" / "voryx_mail_queue"
ALLOWED_RECIPIENT = "himanshusoni3214@gmail.com"
APPROVED_OUTREACH_SENDERS = {"voryxio@gmail.com"}
DEFAULT_MESSAGE_DOMAIN = "voryx.ca"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def ensure_dirs() -> None:
    for name in ("pending", "processing", "receipts", "failed", "archive"):
        (QUEUE_ROOT / name).mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("request JSON must be an object")
    return data


def normalize_recipient(value: Any) -> str:
    if isinstance(value, list):
        raise ValueError("recipient must be a single email address")
    text = str(value or "").strip().lower()
    if "," in text or ";" in text:
        raise ValueError("recipient must be a single email address")
    return text


def safe_message_id(request_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "-", request_id).strip("-") or uuid.uuid4().hex
    return f"<{clean}@{DEFAULT_MESSAGE_DOMAIN}>"


def request_artifact_path(request: dict[str, Any]) -> Path:
    artifact = Path(str(request.get("artifact_path") or ""))
    if not artifact.is_absolute():
        raise ValueError("artifact_path must be absolute")
    try:
        relative = artifact.relative_to(CONTAINER_DATA_ROOT)
    except ValueError as exc:
        raise ValueError(f"artifact_path must be under {CONTAINER_DATA_ROOT}") from exc
    return DATA_ROOT / relative


def validate_request(request: dict[str, Any]) -> None:
    kind = request.get("kind")
    if kind == "controlled_outreach":
        validate_controlled_outreach_request(request)
        return
    if kind != "daily_report":
        raise ValueError("only daily_report and controlled_outreach requests are allowed")
    for disallowed in ("cc", "bcc", "recipients"):
        if request.get(disallowed):
            raise ValueError(f"{disallowed} is not allowed")
    recipient = normalize_recipient(request.get("recipient"))
    if recipient != ALLOWED_RECIPIENT:
        raise ValueError(f"recipient is not allowed: {recipient or 'none'}")
    artifact = request_artifact_path(request)
    if not artifact.exists() or not artifact.is_file():
        raise ValueError(f"artifact_path does not exist: {artifact}")


def validate_controlled_outreach_request(request: dict[str, Any]) -> None:
    for disallowed in ("cc", "bcc", "recipients"):
        if request.get(disallowed):
            raise ValueError(f"{disallowed} is not allowed")
    recipient = normalize_recipient(request.get("recipient"))
    if not recipient or "@" not in recipient:
        raise ValueError("controlled outreach requires exactly one recipient")
    sender = str(request.get("sender_email") or "").strip().lower()
    if sender not in APPROVED_OUTREACH_SENDERS:
        raise ValueError(f"sender_profile_missing: {sender or 'none'} is not approved")
    reply_to = str(request.get("reply_to_email") or sender).strip().lower()
    if reply_to not in APPROVED_OUTREACH_SENDERS and reply_to != ALLOWED_RECIPIENT:
        raise ValueError(f"reply_to_email_not_approved: {reply_to or 'none'}")
    if not str(request.get("subject") or "").strip():
        raise ValueError("controlled outreach subject is required")
    body = str(request.get("body") or "")
    unsubscribe = str(request.get("unsubscribe_text") or "").strip()
    if not body.strip():
        raise ValueError("controlled outreach body is required")
    if not unsubscribe or unsubscribe not in body:
        raise ValueError("controlled outreach body must include unsubscribe text")
    if not request.get("event_id"):
        raise ValueError("controlled outreach requires event_id")


def compose_message(request: dict[str, Any], message_id: str, message_path: Path) -> None:
    kind = request.get("kind")
    if kind == "controlled_outreach":
        body = str(request.get("body") or "")
    else:
        artifact = request_artifact_path(request)
        body = artifact.read_text(encoding="utf-8", errors="replace")
    message = EmailMessage()
    sender = str(request.get("sender_email") or os.environ.get("VORYX_INTERNAL_REPORT_FROM", "voryxio@gmail.com")).strip()
    if (
        not sender
        or "@" not in sender
        or "," in sender
        or ";" in sender
    ):
        raise ValueError(
            "VORYX_INTERNAL_REPORT_FROM must contain exactly one email address"
        )
    message["From"] = sender
    message["To"] = normalize_recipient(request.get("recipient"))
    reply_to = str(request.get("reply_to_email") or sender).strip()
    if reply_to:
        message["Reply-To"] = reply_to
    message["Subject"] = str(request.get("subject") or f"Voryx Daily Report - {request.get('report_date') or ''}").strip()
    message["Date"] = format_datetime(utc_now())
    message["Message-ID"] = message_id
    message.set_content(body)
    message_path.write_text(message.as_string(), encoding="utf-8")


def send_command(message_path: Path) -> list[str]:
    override = os.environ.get("VORYX_HIMALAYA_SEND_COMMAND", "").strip()
    if override:
        return [part.format(message_file=str(message_path)) for part in shlex.split(override)]
    himalaya = os.environ.get("HIMALAYA_BIN", "/usr/local/bin/himalaya")
    return [himalaya, "message", "send"]


def run_send(message_path: Path) -> subprocess.CompletedProcess[str]:
    command = send_command(message_path)
    timeout = int(
        os.environ.get("VORYX_HIMALAYA_TIMEOUT_SECONDS", "90")
    )

    override = os.environ.get(
        "VORYX_HIMALAYA_SEND_COMMAND",
        "",
    ).strip()

    if override:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    return subprocess.run(
        command,
        input=message_path.read_text(
            encoding="utf-8",
            errors="replace",
        ),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def verify_sent_folder(message_id: str, subject: str) -> bool:
    himalaya = os.environ.get("HIMALAYA_BIN", "/usr/local/bin/himalaya")
    folders = ["Sent", "[Gmail]/Sent Mail", "Sent Mail"]
    commands: list[list[str]] = []
    for folder in folders:
        commands.append([himalaya, "envelope", "list", "--folder", folder])
        commands.append([himalaya, "message", "list", "--folder", folder])
    for command in commands:
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=20, check=False)
        except Exception:
            continue
        haystack = f"{completed.stdout}\n{completed.stderr}"
        if message_id in haystack or (subject and subject in haystack):
            return True
    return False


def claim_pending() -> Path | None:
    for pending in sorted((QUEUE_ROOT / "pending").glob("*.json")):
        claimed = QUEUE_ROOT / "processing" / pending.name
        try:
            pending.replace(claimed)
            return claimed
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return None


def write_failure(request: dict[str, Any], error: str, exit_code: int = 1) -> None:
    failed_at = utc_now().isoformat().replace("+00:00", "Z")
    request_id = str(request.get("request_id") or uuid.uuid4().hex)
    receipt = {
        "request_id": request_id,
        "job_id": request.get("job_id"),
        "status": "failed",
        "delivery_status": "failed",
        "recipient": normalize_recipient(request.get("recipient")) if request.get("recipient") else None,
        "error": error[-1000:],
        "exit_code": exit_code,
        "failed_at": failed_at,
        "sender": "himalaya",
        "evidence_type": "mail_queue_failure",
    }
    atomic_write_json(QUEUE_ROOT / "failed" / f"{request_id}.json", receipt)


def write_success(request: dict[str, Any], message_id: str, sent_at: str, sent_folder_verified: bool) -> None:
    request_id = str(request["request_id"])
    receipt = {
        "request_id": request_id,
        "job_id": request.get("job_id"),
        "status": "sent",
        "delivery_status": "sent",
        "recipient": normalize_recipient(request.get("recipient")),
        "subject": request.get("subject"),
        "provider_message_id": message_id,
        "sent_at": sent_at,
        "sender": "himalaya",
        "sender_email": str(request.get("sender_email") or "voryxio@gmail.com").strip().lower(),
        "reply_to_email": str(request.get("reply_to_email") or "").strip().lower(),
        "kind": request.get("kind"),
        "campaign_id": request.get("campaign_id"),
        "company_id": request.get("company_id"),
        "event_id": request.get("event_id"),
        "lead_key": request.get("lead_key"),
        "draft_id": request.get("draft_id"),
        "batch_id": request.get("batch_id"),
        "exit_code": 0,
        "sent_folder_verified": sent_folder_verified,
        "evidence_type": "rfc_message_id",
    }
    atomic_write_json(QUEUE_ROOT / "receipts" / f"{request_id}.json", receipt)


def archive_request(path: Path, suffix: str = "") -> None:
    target = QUEUE_ROOT / "archive" / f"{path.stem}{suffix}{path.suffix}"
    try:
        path.replace(target)
    except FileNotFoundError:
        pass


def process_one() -> bool:
    ensure_dirs()
    claimed = claim_pending()
    if not claimed:
        return False
    request: dict[str, Any] = {}
    message_path = QUEUE_ROOT / "processing" / f"{claimed.stem}.eml"
    try:
        request = load_json(claimed)
        validate_request(request)
        request_id = str(request.get("request_id") or claimed.stem)
        message_id = str(request.get("provider_message_id") or "").strip() or safe_message_id(request_id)
        compose_message(request, message_id, message_path)
        completed = run_send(message_path)
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        if completed.returncode != 0:
            write_failure(request, output or "himalaya send failed", completed.returncode)
            archive_request(claimed, ".failed")
            return True
        sent_at = utc_now().isoformat().replace("+00:00", "Z")
        sent_folder_verified = verify_sent_folder(message_id, str(request.get("subject") or ""))
        write_success(request, message_id, sent_at, sent_folder_verified)
        print(f"EMAIL_SENT provider_message_id={message_id}")
        archive_request(claimed)
        return True
    except Exception as exc:
        fallback = request or {"request_id": claimed.stem, "job_id": None, "recipient": None}
        write_failure(fallback, str(exc), 1)
        archive_request(claimed, ".failed")
        return True
    finally:
        try:
            message_path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    processed = 0
    while process_one():
        processed += 1
        if os.environ.get("VORYX_PROCESS_ONE_MAIL") == "1":
            break
    print(f"processed={processed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
