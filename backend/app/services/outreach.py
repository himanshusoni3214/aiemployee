import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    Campaign,
    Company,
    CompanyOutreachSettings,
    LeadApproval,
    OutreachDraft,
    OutreachEvent,
    ReplyMonitorEvent,
    SuppressionEntry,
)
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT, normalize_email

LEAD_STATES = {
    "new",
    "approved_for_outreach",
    "rejected",
    "missing_email",
    "duplicate",
    "sent",
    "replied",
    "bounced",
    "unsubscribed",
    "do_not_contact",
}
DRAFT_STATES = {"draft_created", "draft_needs_review", "draft_approved", "draft_rejected"}
FOLLOWUP_DISABLED_REASON = "Follow-up Manager is disabled until Reply Monitor/Gmail threading is connected for this company."
APPROVED_INTERNAL_RECIPIENT = INTERNAL_REPORT_RECIPIENT
APPROVED_SENDER_EMAILS = {APPROVED_INTERNAL_RECIPIENT, "voryxio@gmail.com"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def domain_from_email(email: str | None) -> str:
    value = normalize_email(email)
    return value.split("@", 1)[1] if "@" in value else ""


def first_text(row: dict[str, Any], keys: list[str]) -> str:
    normalized = {re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_"): v for k, v in row.items()}
    for key in keys:
        value = normalized.get(re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_"))
        if str(value or "").strip():
            return str(value).strip()
    return ""


def lead_key_for(campaign_id: str, row: dict[str, Any], source_run_id: str, index: int) -> str:
    explicit = first_text(row, ["lead_id", "id"])
    email = normalize_email(first_text(row, ["email", "public_email", "verified_public_email"]))
    business = first_text(row, ["business_name", "business", "company", "name"])
    seed = f"{campaign_id}:{source_run_id}:{explicit}:{email}:{business}:{index}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:24]


def default_outreach_settings(company_id: str) -> dict[str, Any]:
    return {
        "company_id": company_id,
        "sender_name": "",
        "sender_email": "",
        "reply_to_email": "",
        "physical_mailing_address": "",
        "unsubscribe_text": "Reply STOP to opt out.",
        "daily_send_limit": 5,
        "hourly_send_limit": 1,
        "allowed_sending_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        "allowed_sending_hours": {"start": "09:00", "end": "17:00"},
        "timezone": "America/Toronto",
        "approved_sender_connected": False,
        "compliance_acknowledged": False,
        "prospect_sending_enabled": False,
        "internal_test_recipient": APPROVED_INTERNAL_RECIPIENT,
    }


def settings_payload(settings: CompanyOutreachSettings | None, company_id: str) -> dict[str, Any]:
    data = default_outreach_settings(company_id)
    if settings:
        for key in list(data.keys()):
            if hasattr(settings, key):
                data[key] = getattr(settings, key)
        data["id"] = settings.id
    missing = validate_outreach_settings(data, prospect=True)
    data["ready_for_prospect_sending"] = not missing
    data["blocking_reasons"] = missing
    return data


def validate_outreach_settings(settings: CompanyOutreachSettings | dict[str, Any] | None, *, prospect: bool) -> list[str]:
    if settings is None:
        data = {}
    elif isinstance(settings, dict):
        data = settings
    else:
        data = {key: getattr(settings, key) for key in default_outreach_settings(settings.company_id).keys() if hasattr(settings, key)}
    required = [
        "sender_name",
        "sender_email",
        "reply_to_email",
        "physical_mailing_address",
        "unsubscribe_text",
        "daily_send_limit",
        "hourly_send_limit",
        "allowed_sending_days",
        "allowed_sending_hours",
        "timezone",
    ]
    missing = [key for key in required if not data.get(key)]
    sender = normalize_email(data.get("sender_email"))
    reply_to = normalize_email(data.get("reply_to_email"))
    if sender and sender not in APPROVED_SENDER_EMAILS:
        missing.append("sender_email_not_approved")
    if reply_to and reply_to not in APPROVED_SENDER_EMAILS:
        missing.append("reply_to_email_not_approved")
    if not data.get("approved_sender_connected"):
        missing.append("approved_sender_connected")
    if not data.get("compliance_acknowledged"):
        missing.append("compliance_acknowledged")
    if prospect and not data.get("prospect_sending_enabled"):
        missing.append("prospect_sending_enabled")
    if int(data.get("daily_send_limit") or 0) > 5:
        missing.append("daily_send_limit_max_5_initial")
    return list(dict.fromkeys(missing))


def suppression_sets(db: Session, company_id: str) -> tuple[set[str], set[str]]:
    entries = db.scalars(select(SuppressionEntry).where(SuppressionEntry.company_id == company_id)).all()
    emails = {normalize_email(e.value) for e in entries if e.kind == "email"}
    domains = {normalize_email(e.value) for e in entries if e.kind == "domain"}
    return emails, domains


def review_items_from_rows(db: Session, campaign: Campaign, rows: list[dict[str, Any]], source_run_id: str = "latest") -> list[dict[str, Any]]:
    approvals = {
        item.lead_key: item
        for item in db.scalars(select(LeadApproval).where(LeadApproval.campaign_id == campaign.id)).all()
    }
    suppressed_emails, suppressed_domains = suppression_sets(db, campaign.company_id)
    email_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    normalized = []
    for index, row in enumerate(rows, start=1):
        email = normalize_email(first_text(row, ["email", "public_email", "verified_public_email", "Public Email"]))
        domain = domain_from_email(email)
        email_counts[email] = email_counts.get(email, 0) + (1 if email else 0)
        domain_counts[domain] = domain_counts.get(domain, 0) + (1 if domain else 0)
        normalized.append((index, row, email, domain))
    items = []
    for index, row, email, domain in normalized:
        business = first_text(row, ["business_name", "Business Name", "business", "company", "name"])
        key = lead_key_for(campaign.id, row, source_run_id, index)
        computed = "new"
        reason = ""
        if not email:
            computed = "missing_email"; reason = "Missing email"
        elif email in suppressed_emails or domain in suppressed_domains:
            computed = "do_not_contact"; reason = "Suppression list match"
        elif email_counts.get(email, 0) > 1 or domain_counts.get(domain, 0) > 1:
            computed = "duplicate"; reason = "Duplicate email or domain in campaign source"
        approval = approvals.get(key)
        state = approval.state if approval else computed
        if computed in {"missing_email", "duplicate", "do_not_contact"} and state in {"new", "approved_for_outreach"}:
            state = computed
        items.append({
            "lead_key": key,
            "source_run_id": source_run_id,
            "business": business,
            "email": email,
            "domain": domain,
            "state": state,
            "computed_state": computed,
            "reason": approval.reason if approval else reason,
            "raw": row,
            "can_send": state == "approved_for_outreach" and computed == "new",
            "history": approval.history if approval else [],
        })
    return items


def upsert_approval(db: Session, campaign: Campaign, item: dict[str, Any], state: str, user_id: str, reason: str = "") -> LeadApproval:
    if state not in LEAD_STATES:
        raise ValueError(f"Unsupported lead state: {state}")
    if item["computed_state"] in {"missing_email", "duplicate", "do_not_contact"} and state == "approved_for_outreach":
        raise ValueError(f"Lead cannot be approved while computed state is {item['computed_state']}")
    approval = db.scalar(select(LeadApproval).where(LeadApproval.campaign_id == campaign.id, LeadApproval.lead_key == item["lead_key"]))
    now = utc_now()
    history = list(approval.history or []) if approval else []
    history.append({"at": now.isoformat() + "Z", "user_id": user_id, "state": state, "reason": reason})
    if not approval:
        approval = LeadApproval(company_id=campaign.company_id, campaign_id=campaign.id, lead_key=item["lead_key"])
        db.add(approval)
    approval.email = item.get("email") or None
    approval.domain = item.get("domain") or None
    approval.business = item.get("business") or None
    approval.source_run_id = item.get("source_run_id") or None
    approval.state = state
    approval.reason = reason
    approval.raw = item.get("raw") or {}
    approval.history = history
    approval.updated_at = now
    return approval


def generate_draft_for_item(db: Session, campaign: Campaign, company: Company, item: dict[str, Any]) -> OutreachDraft:
    if not item.get("can_send"):
        raise ValueError(f"Lead is not eligible for draft generation: {item.get('state')}")
    existing = db.scalar(select(OutreachDraft).where(OutreachDraft.campaign_id == campaign.id, OutreachDraft.lead_key == item["lead_key"], OutreachDraft.version == 1))
    offer = (campaign.description or campaign.name or "our offer").strip()
    business = item.get("business") or "there"
    subject = f"Quick idea for {business}"
    unsubscribe = "Reply STOP and I will not contact you again."
    body = (
        f"Hi {business},\n\n"
        f"I am reaching out from {company.name}. {offer}\n\n"
        f"If this is relevant, I can send a short overview.\n\n"
        f"{unsubscribe}"
    )
    if existing:
        return existing
    draft = OutreachDraft(
        company_id=campaign.company_id,
        campaign_id=campaign.id,
        lead_key=item["lead_key"],
        lead_email=item.get("email"),
        business=item.get("business"),
        source_run_id=item.get("source_run_id"),
        subject=subject[:250],
        body=body,
        status="draft_created",
        raw={"lead": item.get("raw") or {}, "offer": offer, "safety": "draft_only_no_send"},
    )
    db.add(draft)
    return draft


def draft_to_payload(draft: OutreachDraft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "company_id": draft.company_id,
        "campaign_id": draft.campaign_id,
        "lead_key": draft.lead_key,
        "lead_email": draft.lead_email,
        "business": draft.business,
        "subject": draft.subject,
        "body": draft.body,
        "status": draft.status,
        "version": draft.version,
        "approved_at": draft.approved_at,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


def company_has_reply_monitor(db: Session, company_id: str) -> bool:
    # Placeholder for future Gmail connector state. No connected sender/thread monitor exists yet.
    return False


def followup_status(db: Session, campaign: Campaign) -> dict[str, Any]:
    connected = company_has_reply_monitor(db, campaign.company_id)
    return {
        "enabled": False,
        "reply_monitor_connected": connected,
        "state": "disabled" if not connected else "awaiting_campaign_approval",
        "reason": FOLLOWUP_DISABLED_REASON if not connected else "Follow-up campaign approval required before sending.",
        "states": ["no_followup", "followup_due", "followup_sent", "replied_stop", "bounced_stop", "unsubscribed_stop", "manual_stop"],
        "rules": {"first_delay_business_days": 3, "max_followups": 2, "requires_same_thread": True, "requires_campaign_approval": True},
    }


def reply_monitor_status(db: Session, campaign: Campaign) -> dict[str, Any]:
    connected = company_has_reply_monitor(db, campaign.company_id)
    events = db.scalars(select(ReplyMonitorEvent).where(ReplyMonitorEvent.campaign_id == campaign.id).order_by(ReplyMonitorEvent.created_at.desc()).limit(20)).all()
    return {
        "enabled": connected,
        "state": "connected" if connected else "disabled",
        "reason": None if connected else "Gmail thread monitoring is not connected for this company.",
        "classifications": ["positive", "neutral", "negative", "unsubscribe", "bounce", "out_of_office"],
        "events": [
            {"id": e.id, "lead_key": e.lead_key, "recipient": e.recipient, "thread_id": e.thread_id, "classification": e.classification, "status": e.status, "created_at": e.created_at}
            for e in events
        ],
    }


def send_blockers(db: Session, campaign: Campaign, draft: OutreachDraft | None = None, *, internal_test: bool = False) -> list[str]:
    settings = db.scalar(select(CompanyOutreachSettings).where(CompanyOutreachSettings.company_id == campaign.company_id))
    blockers = validate_outreach_settings(settings, prospect=not internal_test)
    if internal_test:
        recipient = normalize_email(getattr(settings, "internal_test_recipient", None) or APPROVED_INTERNAL_RECIPIENT)
        if recipient != APPROVED_INTERNAL_RECIPIENT:
            blockers.append("internal_test_recipient_not_approved")
    if draft:
        if draft.company_id != campaign.company_id or draft.campaign_id != campaign.id:
            blockers.append("draft_company_campaign_mismatch")
        if draft.status != "draft_approved":
            blockers.append("draft_not_approved")
        approval = db.scalar(select(LeadApproval).where(LeadApproval.campaign_id == campaign.id, LeadApproval.lead_key == draft.lead_key))
        if not approval or approval.state != "approved_for_outreach":
            blockers.append("lead_not_approved")
        if approval and (approval.email and normalize_email(approval.email) != normalize_email(draft.lead_email)):
            blockers.append("lead_draft_email_mismatch")
        sent_count = db.scalar(select(func.count()).select_from(OutreachEvent).where(OutreachEvent.campaign_id == campaign.id, OutreachEvent.recipient == draft.lead_email, OutreachEvent.status.in_(["sent", "delivered", "accepted"]))) or 0
        if sent_count:
            blockers.append("already_sent_to_lead_in_campaign")
    return list(dict.fromkeys(blockers))


def create_internal_test_event(db: Session, campaign: Campaign, draft: OutreachDraft, user_id: str) -> OutreachEvent:
    blockers = send_blockers(db, campaign, draft, internal_test=True)
    if blockers:
        raise ValueError("Internal test blocked: " + ", ".join(blockers))
    now = utc_now()
    event = OutreachEvent(
        event_id=f"internal-test-{campaign.id}-{draft.id}-{int(now.timestamp())}",
        campaign_id=campaign.id,
        company_id=campaign.company_id,
        employee_id=draft.employee_id,
        recipient=APPROVED_INTERNAL_RECIPIENT,
        business=draft.business,
        subject=f"[INTERNAL TEST] {draft.subject}",
        attempted_at=now,
        sent_at=None,
        status="internal_test_prepared",
        provider="voryx_internal_only",
        dry_run=True,
        source_file=None,
        raw={"draft_id": draft.id, "requested_by": user_id, "prospect_emails_sent": 0, "message": "Internal test prepared only; no prospect email sent."},
    )
    db.add(event)
    return event
