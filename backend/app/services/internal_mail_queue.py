import json
import os
import shutil
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Job, JobStatus
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT, parse_datetime, validate_report_recipient

HERMES_DATA_ROOT = Path("/opt/data")
QUEUE_RELATIVE_ROOT = Path("home") / "voryx_mail_queue"
PROCESSOR_SCRIPT_NAME = "process_internal_mail_queue.py"
PROCESSOR_JOB_ID = "voryx-internal-report-mail-processor"
PROCESSOR_JOB_NAME = "Voryx Internal Report Mail Processor"
STALE_REQUEST_MINUTES = 15


class InternalMailQueueError(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _data_root(data_path: str | None = None) -> Path:
    raw = data_path or settings.hermes_data_path
    if not raw:
        raise InternalMailQueueError("HERMES_DATA_PATH is not configured")
    return Path(raw)


def queue_root(data_path: str | None = None) -> Path:
    return _data_root(data_path) / QUEUE_RELATIVE_ROOT


def _ensure_dirs(root: Path) -> None:
    for name in ("pending", "processing", "receipts", "failed", "archive"):
        (root / name).mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _backend_path_from_hermes(hermes_path: str, data_path: str | None = None) -> Path:
    path = Path(hermes_path)
    if not path.is_absolute():
        raise InternalMailQueueError("Hermes artifact path must be absolute")
    try:
        relative = path.relative_to(HERMES_DATA_ROOT)
    except ValueError as exc:
        raise InternalMailQueueError(f"Artifact path must be under {HERMES_DATA_ROOT}") from exc
    return _data_root(data_path) / relative


def hermes_path_for_artifact(artifact_path: Path, data_path: str | None = None) -> str:
    root = _data_root(data_path).resolve()
    artifact = artifact_path.resolve()
    try:
        relative = artifact.relative_to(root)
    except ValueError as exc:
        raise InternalMailQueueError("Report artifact must be inside the Hermes data mount") from exc
    if not artifact.exists() or not artifact.is_file():
        raise InternalMailQueueError(f"Report artifact does not exist: {artifact}")
    return str(HERMES_DATA_ROOT / relative)


def validate_internal_mail_request_payload(payload: dict[str, Any], data_path: str | None = None) -> None:
    if payload.get("kind") != "daily_report":
        raise InternalMailQueueError("Only daily_report internal mail requests are allowed")
    for disallowed in ("cc", "bcc", "recipients"):
        if payload.get(disallowed):
            raise InternalMailQueueError(f"{disallowed} is not allowed for internal report delivery")
    recipient = payload.get("recipient")
    if isinstance(recipient, list) or "," in str(recipient or "") or ";" in str(recipient or ""):
        raise InternalMailQueueError("Internal report delivery allows exactly one recipient")
    validate_report_recipient(str(recipient or ""), report_only_acceptance=True)
    artifact = str(payload.get("artifact_path") or "")
    if not artifact:
        raise InternalMailQueueError("artifact_path is required")
    backend_artifact = _backend_path_from_hermes(artifact, data_path)
    if not backend_artifact.exists() or not backend_artifact.is_file():
        raise InternalMailQueueError("artifact_path does not exist in the Hermes data mount")


def install_processor_script(data_path: str | None = None) -> Path:
    root = queue_root(data_path)
    _ensure_dirs(root)
    source = Path(__file__).resolve().parents[1] / "assets" / PROCESSOR_SCRIPT_NAME
    target = root / PROCESSOR_SCRIPT_NAME
    if not source.exists():
        raise InternalMailQueueError(f"Bundled processor script is missing: {source}")
    next_content = source.read_text(encoding="utf-8")
    if not target.exists() or target.read_text(encoding="utf-8", errors="replace") != next_content:
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(next_content, encoding="utf-8")
        tmp.replace(target)
    mode = target.stat().st_mode
    target.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def enqueue_daily_report_delivery(
    db: Session,
    *,
    recipient: str,
    subject: str,
    artifact_path: Path,
    report_date: str,
    company_id: str | None = None,
    campaign_id: str | None = None,
    data_path: str | None = None,
) -> tuple[Job, dict[str, Any]]:
    recipient = validate_report_recipient(recipient, report_only_acceptance=True)
    request_id = f"voryx-report-{uuid.uuid4().hex}"
    now = _utc_now()
    hermes_artifact = hermes_path_for_artifact(artifact_path, data_path)
    processor = install_processor_script(data_path)
    request = {
        "request_id": request_id,
        "job_id": "",
        "kind": "daily_report",
        "recipient": recipient,
        "subject": subject,
        "artifact_path": hermes_artifact,
        "report_date": report_date,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "report_only_acceptance": True,
        "processor_path": str(HERMES_DATA_ROOT / QUEUE_RELATIVE_ROOT / PROCESSOR_SCRIPT_NAME),
    }
    validate_internal_mail_request_payload(request, data_path)

    job = Job(
        employee_id=None,
        campaign_id=campaign_id,
        connector="hermes",
        task_type="Daily Report",
        status=JobStatus.queued,
        payload={
            "source": "internal_mail_queue",
            "kind": "daily_report",
            "request_id": request_id,
            "report_only_acceptance": True,
            "report_date": report_date,
            "processor_backend_path": str(processor),
        },
        result={"request": {**request, "job_id": None}},
        logs=["Daily report queued for Hermes internal mail processor"],
        recipient_email=recipient,
        delivery_status="queued",
        evidence_type="mail_queue_request",
        source_output_path=str(artifact_path),
        verification_reason="queued for Hermes internal mail processor; awaiting receipt",
        external_execution_key=f"daily-report:{report_date}:{recipient}:{request_id}",
        attempts=0,
        max_attempts=1,
        created_at=now.replace(tzinfo=None),
    )
    db.add(job)
    db.flush()

    request["job_id"] = job.id
    request_path = queue_root(data_path) / "pending" / f"{request_id}.json"
    _atomic_write_json(request_path, request)
    job.payload = {**(job.payload or {}), "request_path": str(request_path)}
    job.result = {"request": request, "request_path": str(request_path)}
    return job, {"request": request, "request_path": str(request_path), "processor_path": str(processor)}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _receipt_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in ("receipts", "failed"):
        folder = root / name
        if folder.exists():
            files.extend(path for path in sorted(folder.glob("*.json")) if path.is_file())
    return files


def _path_label(path: Path, data_path: str | None = None) -> str:
    try:
        return str(path.relative_to(_data_root(data_path)))
    except ValueError:
        return str(path)


def _receipt_ok(receipt: dict[str, Any], job: Job) -> tuple[bool, str]:
    if receipt.get("job_id") != job.id:
        return False, "receipt job_id does not match job"
    request_id = (job.payload or {}).get("request_id")
    if request_id and receipt.get("request_id") != request_id:
        return False, "receipt request_id does not match job"
    recipient = str(receipt.get("recipient") or "").strip().lower()
    if recipient != INTERNAL_REPORT_RECIPIENT:
        return False, f"receipt recipient mismatch: {recipient or 'none'}"
    if str(receipt.get("delivery_status") or receipt.get("status") or "").lower() != "sent":
        return False, str(receipt.get("error") or "receipt is not sent")
    if not str(receipt.get("provider_message_id") or "").strip():
        return False, "receipt lacks provider_message_id"
    if not parse_datetime(receipt.get("sent_at")):
        return False, "receipt lacks sent_at"
    return True, "Hermes internal mail receipt contains recipient, durable message ID, and sent timestamp"


def ingest_internal_mail_receipts(
    db: Session,
    *,
    data_path: str | None = None,
    stale_after_minutes: int = STALE_REQUEST_MINUTES,
) -> dict[str, Any]:
    root = queue_root(data_path)
    _ensure_dirs(root)
    processed = 0
    completed = 0
    failed = 0
    errors: list[str] = []

    for path in _receipt_files(root):
        receipt = _read_json(path)
        if not receipt:
            errors.append(f"invalid receipt JSON: {_path_label(path, data_path)}")
            continue
        job_id = str(receipt.get("job_id") or "")
        if not job_id:
            errors.append(f"receipt missing job_id: {_path_label(path, data_path)}")
            continue
        job = db.get(Job, job_id)
        if not job:
            errors.append(f"receipt job not found: {job_id}")
            continue
        ok, reason = _receipt_ok(receipt, job)
        now = _utc_now().replace(tzinfo=None)
        if ok:
            job.status = JobStatus.completed
            job.delivery_status = "sent"
            job.provider_message_id = str(receipt.get("provider_message_id")).strip()
            job.recipient_email = INTERNAL_REPORT_RECIPIENT
            job.sent_at = parse_datetime(receipt.get("sent_at"))
            job.evidence_type = str(receipt.get("evidence_type") or "rfc_message_id")
            job.verification_reason = reason
            job.error_message = None
            completed += 1
        else:
            job.status = JobStatus.failed
            job.delivery_status = str(receipt.get("delivery_status") or "failed")
            job.provider_message_id = str(receipt.get("provider_message_id") or "").strip() or None
            job.recipient_email = str(receipt.get("recipient") or "").strip().lower() or None
            job.sent_at = parse_datetime(receipt.get("sent_at"))
            job.evidence_type = str(receipt.get("evidence_type") or "mail_queue_failure")
            job.verification_reason = reason
            job.error_message = reason
            failed += 1
        job.result = {**(job.result or {}), "receipt": receipt, "receipt_path": str(path)}
        job.logs = [*(job.logs or []), f"Internal mail receipt ingested: {_path_label(path, data_path)}"]
        job.source_output_path = _path_label(path, data_path)
        job.ended_at = now
        processed += 1

    cutoff = _utc_now().replace(tzinfo=None) - timedelta(minutes=stale_after_minutes)
    stale_jobs = db.scalars(
        select(Job)
        .where(Job.status.in_([JobStatus.queued, JobStatus.running]))
        .where(Job.task_type == "Daily Report")
    ).all()
    for job in stale_jobs:
        payload = job.payload if isinstance(job.payload, dict) else {}
        if payload.get("source") != "internal_mail_queue":
            continue
        if job.created_at and job.created_at > cutoff:
            continue
        job.status = JobStatus.failed
        job.delivery_status = "failed"
        job.evidence_type = "mail_queue_timeout"
        job.verification_reason = f"internal mail request stale after {stale_after_minutes} minutes without a receipt"
        job.error_message = job.verification_reason
        job.ended_at = _utc_now().replace(tzinfo=None)
        job.logs = [*(job.logs or []), job.verification_reason]
        failed += 1
        processed += 1

    return {"status": "ok" if not errors else "degraded", "processed": processed, "completed": completed, "failed": failed, "errors": errors}


def copy_processor_to_hermes_data(data_path: str | None = None) -> str:
    return str(install_processor_script(data_path))

