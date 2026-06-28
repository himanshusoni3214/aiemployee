import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models.entities import Job, JobStatus

INTERNAL_REPORT_RECIPIENT = "himanshusoni3214@gmail.com"
SUCCESS_STATUSES = {"sent", "delivered", "accepted", "queued_by_provider", "ok", "success", "completed"}
FAILED_STATUSES = {"failed", "error", "bounced", "rejected"}
SKIPPED_STATUSES = {"skipped", "skip", "no_recipients", "zero_recipients", "zero_eligible", "no_eligible"}
DRY_RUN_STATUSES = {"dry_run", "dry-run", "test_run", "test-run"}
MESSAGE_ID_KEYS = ("provider_message_id", "message_id", "smtp_id", "smtp_response", "receipt_id")
RECIPIENT_KEYS = ("recipient_email", "recipient", "email", "to")
SENT_AT_KEYS = ("sent_at", "delivered_at", "accepted_at")
DELIVERY_TASK_KEYWORDS = ("email", "outreach", "send", "report")


@dataclass(frozen=True)
class EvidenceDecision:
    status: JobStatus
    delivery_status: str
    evidence_type: str
    verification_reason: str
    provider_message_id: str | None = None
    recipient_email: str | None = None
    sent_at: datetime | None = None


def is_delivery_task(task_type: str | None) -> bool:
    text = (task_type or "").lower()
    return any(keyword in text for keyword in DELIVERY_TASK_KEYWORDS)


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def validate_report_recipient(recipient: str | None, report_only_acceptance: bool = True) -> str:
    email = normalize_email(recipient or INTERNAL_REPORT_RECIPIENT)
    if report_only_acceptance and email != INTERNAL_REPORT_RECIPIENT:
        raise ValueError(f"report-only acceptance mode only allows {INTERNAL_REPORT_RECIPIENT}")
    return email


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _dig(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None


def _first_text(source: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(source, dict):
        return ""
    for key in keys:
        value = source.get(key)
        if value:
            return str(value).strip()
    for nested_key in ("delivery", "result", "results", "email", "message"):
        nested = source.get(nested_key)
        if isinstance(nested, dict):
            value = _first_text(nested, keys)
            if value:
                return value
    return ""


def message_id_from(source: Any) -> str:
    return _first_text(source, MESSAGE_ID_KEYS)


def recipient_from(source: Any) -> str:
    return normalize_email(_first_text(source, RECIPIENT_KEYS))


def sent_at_from(source: Any) -> datetime | None:
    if not isinstance(source, dict):
        return None
    for key in SENT_AT_KEYS:
        parsed = parse_datetime(source.get(key))
        if parsed:
            return parsed
    for nested_key in ("delivery", "result", "results", "email", "message"):
        parsed = sent_at_from(source.get(nested_key))
        if parsed:
            return parsed
    return None


def provider_message_id_from_output(output: str) -> str:
    text = output or ""
    patterns = (
        r"(?:provider_message_id|message_id|smtp_id|receipt_id)\s*[:=]\s*([<]?[^\s,;]+[>]?)",
        r"Message-ID:\s*([<]?[^\s,;]+[>]?)",
        r"EMAIL_SENT[^\n]*\s([<][^\s,;]+[>])",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).strip()
    return ""


def _result_body(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    body = dict(result)
    nested = result.get("results")
    if isinstance(nested, dict):
        body.update(nested)
    delivery = result.get("delivery")
    if isinstance(delivery, dict):
        body.update(delivery)
    return body


def _delivery_records(body: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in ("sent", "deliveries", "delivery_attempts", "messages", "recipients"):
        value = body.get(key)
        if isinstance(value, list):
            records.extend([item for item in value if isinstance(item, dict)])
    if not records and (message_id_from(body) or recipient_from(body)):
        records.append(body)
    return records


def _status_text(source: dict[str, Any]) -> str:
    return str(source.get("status") or source.get("delivery_status") or source.get("state") or "").strip().lower()


def classify_delivery_result(
    task_type: str,
    payload: dict[str, Any] | None,
    result: dict[str, Any] | None,
    *,
    imported: bool = False,
    expected_recipient: str | None = None,
) -> EvidenceDecision:
    payload = payload or {}
    body = _result_body(result)
    task_is_delivery = is_delivery_task(task_type)
    status_text = _status_text(body)
    dry_run = bool(payload.get("dry_run") or body.get("dry_run")) or status_text in DRY_RUN_STATUSES

    if status_text in FAILED_STATUSES:
        return EvidenceDecision(
            status=JobStatus.failed,
            delivery_status="failed",
            evidence_type="provider_error",
            verification_reason=str(body.get("error") or body.get("error_message") or "provider/API failure"),
            recipient_email=recipient_from(body) or normalize_email(expected_recipient),
        )

    if dry_run:
        return EvidenceDecision(
            status=JobStatus.skipped,
            delivery_status="dry_run",
            evidence_type="dry_run",
            verification_reason="dry run does not prove delivery",
            provider_message_id=message_id_from(body) or None,
            recipient_email=recipient_from(body) or normalize_email(expected_recipient),
            sent_at=sent_at_from(body),
        )

    if not task_is_delivery:
        return EvidenceDecision(
            status=JobStatus.completed,
            delivery_status="not_applicable",
            evidence_type="worker_result",
            verification_reason="non-delivery task completed by worker result",
        )

    if "report" in (task_type or "").lower():
        message_id = message_id_from(body)
        recipient = recipient_from(body) or normalize_email(expected_recipient)
        if expected_recipient and recipient != normalize_email(expected_recipient):
            return EvidenceDecision(
                status=JobStatus.failed if not imported else JobStatus.synced,
                delivery_status="recipient_mismatch",
                evidence_type="recipient_verification",
                verification_reason=f"expected report recipient {normalize_email(expected_recipient)}, got {recipient or 'none'}",
                provider_message_id=message_id or None,
                recipient_email=recipient or None,
                sent_at=sent_at_from(body),
            )
        if message_id and recipient:
            return EvidenceDecision(
                status=JobStatus.completed,
                delivery_status="sent",
                evidence_type="provider_message_id",
                verification_reason="provider message ID present for configured report recipient",
                provider_message_id=message_id,
                recipient_email=recipient,
                sent_at=sent_at_from(body),
            )
        return EvidenceDecision(
            status=JobStatus.synced if imported else JobStatus.failed,
            delivery_status="unverified",
            evidence_type="missing_provider_message_id",
            verification_reason="daily report delivery lacks durable provider message ID",
            recipient_email=recipient or None,
            sent_at=sent_at_from(body),
        )

    records = _delivery_records(body)
    successful = [record for record in records if _status_text(record) in SUCCESS_STATUSES or message_id_from(record)]
    if successful:
        missing = [record for record in successful if not message_id_from(record) or not recipient_from(record)]
        if missing:
            return EvidenceDecision(
                status=JobStatus.synced if imported else JobStatus.failed,
                delivery_status="unverified",
                evidence_type="missing_provider_message_id",
                verification_reason="one or more successful sends lack recipient or provider message ID",
            )
        first = successful[0]
        return EvidenceDecision(
            status=JobStatus.completed,
            delivery_status="sent",
            evidence_type="provider_message_id",
            verification_reason="successful send has durable provider message ID",
            provider_message_id=message_id_from(first),
            recipient_email=recipient_from(first),
            sent_at=sent_at_from(first),
        )

    sent_count = body.get("sent_count")
    eligible_count = body.get("eligible_count", body.get("eligible_recipients"))
    if status_text in SKIPPED_STATUSES or sent_count == 0 or eligible_count == 0:
        reason = "zero eligible verified recipients" if eligible_count == 0 else "zero sends recorded"
        return EvidenceDecision(
            status=JobStatus.skipped,
            delivery_status="skipped",
            evidence_type="zero_send",
            verification_reason=reason,
        )

    return EvidenceDecision(
        status=JobStatus.synced if imported else JobStatus.failed,
        delivery_status="unverified",
        evidence_type="missing_delivery_evidence",
        verification_reason="delivery task returned without durable send evidence",
    )


def apply_decision(job: Job, decision: EvidenceDecision) -> None:
    job.status = decision.status
    job.delivery_status = decision.delivery_status
    job.evidence_type = decision.evidence_type
    job.verification_reason = decision.verification_reason
    job.provider_message_id = decision.provider_message_id
    job.recipient_email = decision.recipient_email
    job.sent_at = decision.sent_at
    if decision.status in {JobStatus.failed, JobStatus.blocked}:
        job.error_message = decision.verification_reason
    elif job.error_message == decision.verification_reason:
        job.error_message = None
