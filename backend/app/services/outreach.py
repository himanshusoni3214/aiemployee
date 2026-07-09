import hashlib
import json
import os
import re
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    Campaign,
    Company,
    CompanyOutreachSettings,
    AIEmployee,
    Job,
    JobStatus,
    LeadApproval,
    OutreachDraft,
    OutreachEvent,
    ReplyMonitorEvent,
    SuppressionEntry,
)
from app.core.config import settings
from app.services.hermes_control import HermesControlService
from app.services.model_policy import guard_hermes_execution
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



def sender_verification(sender_email: str | None) -> dict[str, Any]:
    sender = normalize_email(sender_email)
    configured = {normalize_email(item) for item in os.getenv("VORYX_APPROVED_SENDERS", "").split(",") if item.strip()}
    allowed = set(APPROVED_SENDER_EMAILS) | configured
    if not sender:
        return {"verified": False, "sender_email": "", "method": "none", "last_verified_at": None, "reason": "Sender email is required."}
    if sender not in allowed:
        return {"verified": False, "sender_email": sender, "method": "allowlist", "last_verified_at": utc_now().isoformat() + "Z", "reason": "Sender is not in the approved Voryx sender allowlist."}
    home = Path(settings.hermes_data_path or "/hermes-data") / "home"
    himalaya_config = home / ".config" / "himalaya"
    method = "approved_sender_allowlist"
    if himalaya_config.exists():
        method = "himalaya_profile_and_allowlist"
    return {"verified": True, "sender_email": sender, "method": method, "last_verified_at": utc_now().isoformat() + "Z", "reason": None}


def human_blocker_text(blocker: str) -> str:
    return {
        "prospect_sending_enabled": "Prospect sending is OFF. Turn on only after internal test and sender verification.",
        "approved_sender_connected": "Sender verification is missing. Connect or verify the approved sender account first.",
        "compliance_acknowledged": "Compliance settings must be acknowledged before prospect sending.",
        "sender_email_not_approved": "Sender email is not approved for this workspace.",
        "reply_to_email_not_approved": "Reply-to email is not approved for this workspace.",
        "daily_send_limit_max_5_initial": "Daily send limit must stay at 5 or lower for the initial controlled rollout.",
    }.get(blocker, blocker.replace("_", " ").capitalize())


def outreach_readiness(db: Session, campaign: Campaign, settings: CompanyOutreachSettings | None, drafts: list[OutreachDraft] | None = None) -> dict[str, Any]:
    drafts = drafts or []
    payload = settings_payload(settings, campaign.company_id)
    sender = sender_verification(payload.get("sender_email"))
    blockers_without_switch = [item for item in validate_outreach_settings(settings, prospect=False) if item != "prospect_sending_enabled"]
    approved_drafts = sum(1 for draft in drafts if draft.status == "draft_approved")
    approved_leads = db.scalar(select(func.count()).select_from(LeadApproval).where(LeadApproval.campaign_id == campaign.id, LeadApproval.state == "approved_for_outreach")) or 0
    internal_tests = db.scalar(select(func.count()).select_from(OutreachEvent).where(OutreachEvent.campaign_id == campaign.id, OutreachEvent.status.in_(["internal_test_prepared", "internal_test_sent"]))) or 0
    steps = [
        {"key": "sender_settings", "label": "Sender settings", "complete": all(payload.get(key) for key in ["sender_name", "sender_email", "reply_to_email"]), "detail": payload.get("sender_email") or "Sender email missing"},
        {"key": "sender_verification", "label": "Sender verification", "complete": sender["verified"], "detail": sender.get("method") if sender["verified"] else sender.get("reason")},
        {"key": "compliance_settings", "label": "Compliance settings", "complete": bool(payload.get("physical_mailing_address") and payload.get("unsubscribe_text") and payload.get("compliance_acknowledged")), "detail": "Physical address, unsubscribe text and compliance acknowledgement required."},
        {"key": "lead_approval", "label": "Lead approval", "complete": approved_leads > 0, "detail": f"{approved_leads} approved leads"},
        {"key": "draft_generation", "label": "Draft generation", "complete": len(drafts) > 0, "detail": f"{len(drafts)} drafts"},
        {"key": "draft_approval", "label": "Draft approval", "complete": approved_drafts > 0, "detail": f"{approved_drafts} approved drafts"},
        {"key": "internal_test", "label": "Internal test", "complete": internal_tests > 0, "detail": f"{internal_tests} internal tests prepared/sent"},
        {"key": "enable_prospect_sending", "label": "Enable prospect sending", "complete": bool(payload.get("prospect_sending_enabled")), "detail": "Prospect sending is OFF. Turn on only after internal test and sender verification." if not payload.get("prospect_sending_enabled") else "Controlled prospect sending enabled."},
        {"key": "send_controlled_batch", "label": "Send controlled batch", "complete": False, "detail": "Prospect sending remains blocked during QA; max 5/day when approved."},
        {"key": "reply_monitor_followup", "label": "Reply monitor / follow-up", "complete": False, "detail": FOLLOWUP_DISABLED_REASON},
    ]
    draft_lead_keys = {draft.lead_key for draft in drafts}
    approved_without_drafts = db.scalars(select(LeadApproval).where(LeadApproval.campaign_id == campaign.id, LeadApproval.state == "approved_for_outreach", LeadApproval.lead_key.not_in(draft_lead_keys or {"__none__"}))).all()
    latest_drafts = _latest_drafts_by_lead(drafts)
    ready_to_send = 0
    for approval in db.scalars(select(LeadApproval).where(LeadApproval.campaign_id == campaign.id, LeadApproval.state == "approved_for_outreach")).all():
        draft = latest_drafts.get(approval.lead_key)
        if draft and draft.status == "draft_approved":
            ready_to_send += 1
    return {
        "sender_verification": sender,
        "steps": steps,
        "blockers_without_prospect_switch": blockers_without_switch,
        "human_blockers": [human_blocker_text(item) for item in validate_outreach_settings(settings, prospect=True)],
        "can_enable_prospect_sending": sender["verified"] and not blockers_without_switch and approved_leads > 0 and approved_drafts > 0 and internal_tests > 0,
        "approved_leads": approved_leads,
        "approved_drafts": approved_drafts,
        "drafts_generated": len(drafts),
        "approved_leads_without_drafts": len(approved_without_drafts),
        "ready_to_send": ready_to_send,
        "internal_tests": internal_tests,
    }


def write_outreach_workspace_config(company_id: str, payload: dict[str, Any]) -> str | None:
    if not settings.hermes_data_path:
        return None
    root = Path(settings.hermes_data_path) / "home" / "voryx_workspaces" / company_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / "outreach_settings.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return f"/opt/data/home/voryx_workspaces/{company_id}/outreach_settings.json"


def sync_outreach_settings_to_hermes(company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    service = HermesControlService()
    try:
        raw = service._read_jobs()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "updated": 0}
    jobs = raw if isinstance(raw, list) else raw.get("jobs", []) if isinstance(raw, dict) else []
    updated = 0
    for job in jobs:
        if not isinstance(job, dict) or str(job.get("company_id") or "") != company_id:
            continue
        safety = job.get("safety") if isinstance(job.get("safety"), dict) else {}
        safety["prospect_sending_enabled"] = bool(payload.get("prospect_sending_enabled"))
        safety["sender_verified"] = bool(payload.get("sender_verification", {}).get("verified"))
        safety["outreach_settings_path"] = payload.get("workspace_path")
        job["safety"] = safety
        updated += 1
    if updated:
        service._write_jobs(raw)
        verified_raw = service._read_jobs()
        verified_jobs = verified_raw if isinstance(verified_raw, list) else verified_raw.get("jobs", []) if isinstance(verified_raw, dict) else []
        for job in verified_jobs:
            if isinstance(job, dict) and str(job.get("company_id") or "") == company_id:
                safety = job.get("safety") if isinstance(job.get("safety"), dict) else {}
                if safety.get("prospect_sending_enabled") != bool(payload.get("prospect_sending_enabled")):
                    return {"ok": False, "error": "Hermes outreach settings verification failed", "updated": updated}
    return {"ok": True, "updated": updated}

def settings_payload(settings: CompanyOutreachSettings | None, company_id: str) -> dict[str, Any]:
    data = default_outreach_settings(company_id)
    if settings:
        for key in list(data.keys()):
            if hasattr(settings, key):
                data[key] = getattr(settings, key)
        data["id"] = settings.id
    sender = sender_verification(data.get("sender_email"))
    missing = validate_outreach_settings(data, prospect=True)
    data["sender_verification"] = sender
    data["ready_for_prospect_sending"] = not missing
    data["blocking_reasons"] = missing
    data["human_blocking_reasons"] = [human_blocker_text(item) for item in missing]
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
    if not sender_verification(sender).get("verified"):
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




def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _window_status(settings_payload_data: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    timezone_name = str(settings_payload_data.get("timezone") or "America/Toronto")
    tz = ZoneInfo(timezone_name)
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    allowed_days = settings_payload_data.get("allowed_sending_days") or []
    allowed_hours = settings_payload_data.get("allowed_sending_hours") or {}
    day_allowed = not allowed_days or local_now.strftime("%A") in allowed_days
    start_text = str(allowed_hours.get("start") or "00:00")
    end_text = str(allowed_hours.get("end") or "23:59")
    try:
        start = time.fromisoformat(start_text)
        end = time.fromisoformat(end_text)
    except ValueError:
        return {"allowed": False, "reason": "Allowed sending hours are invalid.", "timezone": timezone_name, "local_now": local_now.isoformat(), "window": allowed_hours}
    now_time = local_now.time().replace(second=0, microsecond=0)
    hour_allowed = start <= now_time <= end if start <= end else now_time >= start or now_time <= end
    return {"allowed": bool(day_allowed and hour_allowed), "reason": None if day_allowed and hour_allowed else "Outside the approved sending day/hour window.", "timezone": timezone_name, "local_now": local_now.isoformat(), "window": {"days": allowed_days, "hours": {"start": start_text, "end": end_text}}}


def _latest_drafts_by_lead(drafts: list[OutreachDraft]) -> dict[str, OutreachDraft]:
    ordered = sorted(drafts, key=lambda draft: (draft.created_at or datetime.min, draft.version or 0), reverse=True)
    result: dict[str, OutreachDraft] = {}
    for draft in ordered:
        result.setdefault(draft.lead_key, draft)
    return result


def _sent_recipients(db: Session, campaign_id: str) -> set[str]:
    rows = db.scalars(select(OutreachEvent.recipient).where(OutreachEvent.campaign_id == campaign_id, OutreachEvent.status.in_(["sent", "delivered", "accepted"]), OutreachEvent.message_id.is_not(None), OutreachEvent.dry_run == False)).all()
    return {normalize_email(row) for row in rows if row}


def _send_counts(db: Session, campaign: Campaign, settings_data: dict[str, Any]) -> dict[str, int]:
    timezone_name = str(settings_data.get("timezone") or "America/Toronto")
    tz = ZoneInfo(timezone_name)
    local_now = datetime.now(tz)
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    hour_start = local_now.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
    base = select(func.count()).select_from(OutreachEvent).where(OutreachEvent.campaign_id == campaign.id, OutreachEvent.status.in_(["sent", "delivered", "accepted"]), OutreachEvent.message_id.is_not(None), OutreachEvent.dry_run == False)
    daily_sent = db.scalar(base.where(OutreachEvent.sent_at >= day_start)) or 0
    hourly_sent = db.scalar(base.where(OutreachEvent.sent_at >= hour_start)) or 0
    daily_limit = max(0, _safe_int(settings_data.get("daily_send_limit"), 5))
    hourly_limit = max(0, _safe_int(settings_data.get("hourly_send_limit"), 1))
    return {"daily_sent": daily_sent, "hourly_sent": hourly_sent, "daily_limit": daily_limit, "hourly_limit": hourly_limit, "daily_remaining": max(0, daily_limit - daily_sent), "hourly_remaining": max(0, hourly_limit - hourly_sent)}


def _candidate_employee(db: Session, campaign: Campaign) -> AIEmployee | None:
    return db.scalar(select(AIEmployee).where(AIEmployee.campaign_id == campaign.id, AIEmployee.status != "Archived", AIEmployee.hermes_job_id.is_not(None)).order_by(AIEmployee.name)) or db.scalar(select(AIEmployee).where(AIEmployee.company_id == campaign.company_id, AIEmployee.hermes_job_id.is_not(None)).order_by(AIEmployee.name))


def _workspace_evidence_path(company_id: str, campaign_id: str, batch_id: str) -> str | None:
    if not settings.hermes_data_path:
        return None
    root = Path(settings.hermes_data_path) / "home" / "voryx_workspaces" / company_id / "outreach_batches"
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{campaign_id}-{batch_id}.json")


def _batch_snapshot(db: Session, campaign: Campaign, *, limit: int | None = None) -> dict[str, Any]:
    settings = db.scalar(select(CompanyOutreachSettings).where(CompanyOutreachSettings.company_id == campaign.company_id))
    settings_data = settings_payload(settings, campaign.company_id)
    drafts = db.scalars(select(OutreachDraft).where(OutreachDraft.campaign_id == campaign.id)).all()
    approvals = db.scalars(select(LeadApproval).where(LeadApproval.campaign_id == campaign.id)).all()
    suppression_emails, suppression_domains = suppression_sets(db, campaign.company_id)
    latest_drafts = _latest_drafts_by_lead(drafts)
    sent_recipients = _sent_recipients(db, campaign.id)
    counts = _send_counts(db, campaign, settings_data)
    window = _window_status(settings_data)
    sender = sender_verification(settings_data.get("sender_email"))
    employee = _candidate_employee(db, campaign)
    model_guard = guard_hermes_execution(db, task_type="Controlled Outreach Batch", payload={"campaign_id": campaign.id, "company_id": campaign.company_id, "hermes_job_id": getattr(employee, "hermes_job_id", None), "dry_run": True})
    blockers = []
    for item in validate_outreach_settings(settings, prospect=True):
        blockers.append(human_blocker_text(item))
    if not settings_data.get("prospect_sending_enabled"):
        blockers.append("Prospect sending is OFF. Enable it only after internal test and sender verification.")
    if not sender.get("verified"):
        blockers.append("Sender verification is missing or invalid.")
    if not window.get("allowed"):
        blockers.append(window.get("reason") or "Outside approved sending window.")
    if counts["daily_remaining"] <= 0:
        blockers.append("Daily sending limit has been reached.")
    if counts["hourly_remaining"] <= 0:
        blockers.append("Hourly sending limit has been reached.")
    if not model_guard.get("allowed"):
        blockers.append(str(model_guard.get("decision", {}).get("reason") or "Model policy blocked this batch."))

    recipients = []
    blocked = []
    approved_leads = 0
    for approval in approvals:
        email = normalize_email(approval.email)
        domain = domain_from_email(email)
        draft = latest_drafts.get(approval.lead_key)
        reasons = []
        if approval.state == "approved_for_outreach":
            approved_leads += 1
        else:
            reasons.append(f"lead_state_{approval.state}")
        if not email:
            reasons.append("missing_email")
        if email in suppression_emails or domain in suppression_domains:
            reasons.append("suppressed_or_dnc")
        if email in sent_recipients:
            reasons.append("already_sent")
        if not draft:
            reasons.append("missing_draft")
        elif draft.status != "draft_approved":
            reasons.append("draft_not_approved")
        if reasons:
            blocked.append({"lead_key": approval.lead_key, "business": approval.business, "email": email, "reasons": reasons})
            continue
        recipients.append({"lead_key": approval.lead_key, "draft_id": draft.id, "business": draft.business or approval.business, "email": email, "subject": draft.subject, "body_preview": draft.body[:500], "sender_email": settings_data.get("sender_email"), "reply_to_email": settings_data.get("reply_to_email"), "unsubscribe_text": settings_data.get("unsubscribe_text")})
    approved_drafts = sum(1 for draft in drafts if draft.status == "draft_approved")
    draft_lead_keys = {draft.lead_key for draft in drafts}
    approved_without_drafts = [approval for approval in approvals if approval.state == "approved_for_outreach" and approval.lead_key not in draft_lead_keys]
    max_batch = min(counts["daily_remaining"], counts["hourly_remaining"], _safe_int(limit, counts["daily_remaining"]) if limit is not None else counts["daily_remaining"])
    selected = recipients[:max(0, max_batch)]
    return {"campaign_id": campaign.id, "company_id": campaign.company_id, "mode": "dry_run_prepared", "prospect_emails_sent": 0, "sender": sender, "settings": {"sender_email": settings_data.get("sender_email"), "reply_to_email": settings_data.get("reply_to_email"), "unsubscribe_text": settings_data.get("unsubscribe_text"), "prospect_sending_enabled": settings_data.get("prospect_sending_enabled")}, "window": window, "limits": counts, "model_guard": model_guard, "hermes_guard": {"mode": "jobs_json", "employee_id": getattr(employee, "id", None), "hermes_job_id": getattr(employee, "hermes_job_id", None), "allowed": model_guard.get("allowed")}, "coverage": {"total_leads": len(approvals), "approved_leads": approved_leads, "drafts_generated": len(drafts), "approved_drafts": approved_drafts, "approved_leads_without_drafts": len(approved_without_drafts), "ready_to_send": len(recipients), "selected_for_batch": len(selected), "blocked_recipients": len(blocked)}, "recipients": selected, "eligible_recipients": recipients, "blocked_recipients": blocked, "blockers": list(dict.fromkeys(blockers)), "can_send_controlled_batch": bool(settings_data.get("prospect_sending_enabled") and sender.get("verified") and window.get("allowed") and counts["daily_remaining"] > 0 and counts["hourly_remaining"] > 0 and model_guard.get("allowed") and selected)}


def controlled_batch_preview(db: Session, campaign: Campaign, *, limit: int | None = None) -> dict[str, Any]:
    return _batch_snapshot(db, campaign, limit=limit)


def prepare_controlled_batch(db: Session, campaign: Campaign, user_id: str, *, limit: int | None = None, dry_run: bool = True) -> dict[str, Any]:
    snapshot = _batch_snapshot(db, campaign, limit=limit)
    if not dry_run:
        raise ValueError("Real prospect sending is not enabled from Voryx QA mode; use dry_run=true until explicitly approved.")
    if not snapshot["can_send_controlled_batch"]:
        raise ValueError("Controlled batch blocked: " + "; ".join(snapshot["blockers"] or ["no eligible recipients"]))
    now = utc_now()
    batch_id = f"batch-{campaign.id}-{int(now.timestamp())}"
    evidence_path = _workspace_evidence_path(campaign.company_id, campaign.id, batch_id)
    prepared = []
    for item in snapshot["recipients"]:
        event = OutreachEvent(event_id=f"{batch_id}-{item['lead_key']}", campaign_id=campaign.id, company_id=campaign.company_id, recipient=item["email"], business=item.get("business"), subject=item.get("subject"), attempted_at=now, sent_at=None, status="prepared_dry_run", provider="voryx_controlled_batch_guard", dry_run=True, source_file=evidence_path, raw={"batch_id": batch_id, "draft_id": item.get("draft_id"), "requested_by": user_id, "prospect_emails_sent": 0, "body_preview": item.get("body_preview")})
        db.add(event)
        prepared.append(event.event_id)
    job = Job(campaign_id=campaign.id, connector="hermes", task_type="Controlled Outreach Batch", status=JobStatus.completed, payload={"batch_id": batch_id, "dry_run": True, "limit": limit, "source": "dashboard_controlled_batch"}, result={"batch_id": batch_id, "prepared_events": prepared, "prospect_emails_sent": 0, "preview": snapshot}, logs=["Controlled batch prepared in dry-run mode; no prospect email sent.", f"Eligible recipients: {len(snapshot['eligible_recipients'])}", f"Prepared recipients: {len(snapshot['recipients'])}"], evidence_type="controlled_batch_preview", source_output_path=evidence_path, verification_reason="dry_run_prepared_only_no_provider_receipt", attempts=1, max_attempts=1, started_at=now, ended_at=now, created_at=now)
    db.add(job)
    db.flush()
    evidence = {"batch_id": batch_id, "job_id": job.id, "created_at": now.isoformat() + "Z", "dry_run": True, "prospect_emails_sent": 0, "snapshot": snapshot, "prepared_event_ids": prepared}
    if evidence_path:
        Path(evidence_path).write_text(json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    snapshot["batch_id"] = batch_id
    snapshot["job_id"] = job.id
    snapshot["evidence_path"] = evidence_path
    snapshot["prepared_event_ids"] = prepared
    return snapshot


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
