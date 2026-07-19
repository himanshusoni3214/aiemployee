import hashlib
import json
import os
import re
from datetime import date, datetime, time, timezone, timedelta
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
from app.services.campaign_inventory import get_campaign_email_inventory
from app.services.internal_mail_queue import (
    enqueue_controlled_outreach_delivery,
    ingest_internal_mail_receipts,
    process_one_mail_request,
)

LEAD_STATES = {
    "new",
    "approved_for_outreach",
    "rejected",
    "missing_email",
    "duplicate",
    "assumed_email",
    "phone_ready",
    "enrichment_needed",
    "unreachable",
    "invalid",
    "sent",
    "replied",
    "bounced",
    "unsubscribed",
    "do_not_contact",
}
DRAFT_STATES = {"draft_created", "draft_needs_review", "draft_approved", "draft_rejected"}
FOLLOWUP_DISABLED_REASON = "Follow-up Manager is disabled until Reply Monitor/Gmail threading is connected for this company."
BIBS_LEAD_CAMPAIGN_ID = "campaign-brew-it-by-sash-lead-research"
BIBS_OUTREACH_CAMPAIGN_ID = "campaign-brew-it-by-sash-outreach"
APPROVED_INTERNAL_RECIPIENT = INTERNAL_REPORT_RECIPIENT
APPROVED_SENDER_EMAILS = {APPROVED_INTERNAL_RECIPIENT, "voryxio@gmail.com"}
SEND_MODE_DRY_RUN = "dry_run_prepare"
SEND_MODE_INTERNAL_TEST = "internal_test"
SEND_MODE_REAL_PROSPECT = "real_prospect_send"
CONFIRM_SEND_ONE = "SEND 1 REAL EMAIL"
CONFIRM_SEND_BATCH = "SEND CONTROLLED BATCH"


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




def _domain_from_url(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text)
    text = text.split("/", 1)[0].split("?", 1)[0].strip()
    if text.startswith("www."):
        text = text[4:]
    return text


def _normalized_identity_text(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"\b(unit|suite|ste|floor|fl|#)\b\.?", " ", text)
    text = re.sub(r"[^a-z0-9@.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _first_domain_from_row(row: dict[str, Any]) -> str:
    for key_group in (
        ["website", "Website", "domain", "url"],
        ["source_url", "Source URL", "source", "evidence_url", "contact_page"],
    ):
        domain = _domain_from_url(first_text(row, key_group))
        if domain:
            return domain
    return ""


def _phone_digits(value: str | None) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _lead_category_from_row(row: dict[str, Any], email: str) -> tuple[str, str]:
    explicit = first_text(row, ["lead_category", "Lead Category"]).lower()
    explicit_map = {
        "email_ready", "phone_ready", "enrichment_needed", "unreachable", "invalid",
        "duplicate", "previously_rejected", "do_not_contact", "previously_sent",
    }
    if explicit in explicit_map:
        return explicit, first_text(row, ["lead_quality_reason", "Lead Quality Reason"]) or explicit
    business = first_text(row, ["business_name", "Business Name", "business", "company", "name"])
    website = first_text(row, ["website", "Website", "domain", "url"])
    source_url = first_text(row, ["source_url", "Source URL", "source", "evidence_url", "contact_page", "Evidence URL"])
    email_evidence = first_text(row, ["email_evidence", "Email Evidence"]) or source_url
    phone = _phone_digits(first_text(row, ["phone", "Phone", "telephone", "phone_number"]))
    if not business:
        return "invalid", "missing_business_name"
    if email and email_evidence:
        return "email_ready", "public_email_with_source_evidence"
    if email and not email_evidence:
        return "assumed_email", "email_missing_public_evidence"
    if phone:
        return "phone_ready", "public_phone_no_usable_email"
    if website or source_url:
        return "enrichment_needed", "identity_has_source_but_missing_email_and_phone"
    return "unreachable", "identity_without_contact_or_usable_source"


def lead_quality_for(row: dict[str, Any], email: str, domain: str) -> dict[str, Any]:
    website = first_text(row, ["website", "Website", "domain", "url"])
    source_url = first_text(row, ["source_url", "Source URL", "source", "evidence_url", "contact_page", "Evidence URL"])
    email_evidence = first_text(row, ["email_evidence", "Email Evidence"]) or source_url
    verified = first_text(row, ["verified_at", "email_verified_at", "verification_status", "email_verification", "verified_public_email"])
    status = first_text(row, ["lead_status", "status", "email_status"]).lower()
    website_domain = _domain_from_url(website)
    evidence_domain = _domain_from_url(email_evidence or source_url)
    reasons: list[str] = []
    if not email:
        return {"email_confidence": "missing", "lead_quality": "missing_email", "quality_reasons": ["missing_email"], "evidence_url": source_url, "website": website}
    if not email_evidence:
        return {"email_confidence": "assumed", "lead_quality": "assumed_email_no_public_source", "quality_reasons": ["missing_email_evidence_url"], "evidence_url": source_url, "website": website}
    if verified or "verified" in status:
        confidence = "verified"
        quality = "verified_public_email"
        reasons.append("verification_metadata_present")
    elif domain and (domain == website_domain or domain == evidence_domain or evidence_domain.endswith(domain) or domain.endswith(evidence_domain)):
        confidence = "public_unverified"
        quality = "public_source_domain_match"
        reasons.append("email_evidence_domain_matches_email_domain")
    else:
        confidence = "public_unverified"
        quality = "public_source_needs_review"
        reasons.append("email_evidence_present_domain_not_matched")
    if not website:
        reasons.append("missing_website")
    return {"email_confidence": confidence, "lead_quality": quality, "quality_reasons": reasons, "evidence_url": email_evidence or source_url, "website": website}

def lead_key_for(campaign_id: str, row: dict[str, Any], source_run_id: str, index: int) -> str:
    explicit = first_text(row, ["canonical_lead_id", "stable_id", "source_platform_id", "osm_id", "place_id"])
    email = normalize_email(first_text(row, ["email", "public_email", "verified_public_email"]))
    business = _normalized_identity_text(first_text(row, ["business_name", "business", "company", "name"]))
    website = _first_domain_from_row(row)
    address = _normalized_identity_text(first_text(row, ["address", "Address", "street_address", "location", "formatted_address"]))
    phone = _normalized_identity_text(first_text(row, ["phone", "Phone", "telephone", "phone_number"]))
    source_url = _normalized_identity_text(first_text(row, ["source_url", "Source URL", "source", "evidence_url", "contact_page"]))
    if explicit:
        seed = f"{campaign_id}:explicit:{explicit}"
    elif email:
        seed = f"{campaign_id}:email:{email}"
    elif website:
        seed = f"{campaign_id}:domain:{website}"
    elif business and address:
        seed = f"{campaign_id}:business-address:{business}:{address}"
    elif business and phone:
        seed = f"{campaign_id}:business-phone:{business}:{phone}"
    elif source_url:
        seed = f"{campaign_id}:source:{source_url}"
    else:
        seed = f"{campaign_id}:business:{business}"
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
        "allowed_sending_start_date": None,
        "allowed_sending_end_date": None,
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


def lead_inventory_campaign_for(db: Session, campaign: Campaign) -> Campaign:
    result = campaign.provisioning_result if isinstance(campaign.provisioning_result, dict) else {}
    configured_source_id = str(
        result.get("lead_source_campaign_id")
        or result.get("source_campaign_id")
        or result.get("source_campaign")
        or ""
    ).strip()
    if not configured_source_id and campaign.id == BIBS_OUTREACH_CAMPAIGN_ID:
        configured_source_id = BIBS_LEAD_CAMPAIGN_ID
    if configured_source_id and configured_source_id != campaign.id:
        source = db.get(Campaign, configured_source_id)
        if source and source.company_id == campaign.company_id:
            return source
    return campaign


def outreach_readiness(db: Session, campaign: Campaign, settings: CompanyOutreachSettings | None, drafts: list[OutreachDraft] | None = None) -> dict[str, Any]:
    drafts = drafts or []
    payload = settings_payload(settings, campaign.company_id)
    sender = sender_verification(payload.get("sender_email"))
    blockers_without_switch = [item for item in validate_outreach_settings(settings, prospect=False) if item != "prospect_sending_enabled"]
    approved_drafts = sum(1 for draft in drafts if draft.status == "draft_approved")
    lead_campaign = lead_inventory_campaign_for(db, campaign)
    inventory = get_campaign_email_inventory(db, campaign.company_id, lead_campaign.id, draft_campaign_id=campaign.id)
    approved_leads = int(inventory.get("approved_unsent", 0))
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
    ready_to_send = int(inventory.get("ready_to_send", 0))
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
        "email_inventory": inventory,
        "lead_source_campaign_id": lead_campaign.id,
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


def required_unsubscribe_text(db: Session, company_id: str) -> str:
    settings = db.scalar(select(CompanyOutreachSettings).where(CompanyOutreachSettings.company_id == company_id))
    return str(settings_payload(settings, company_id).get("unsubscribe_text") or "Reply STOP to opt out.").strip()


def body_has_unsubscribe(body: str | None, unsubscribe_text: str | None) -> bool:
    required = str(unsubscribe_text or "").strip()
    if not required:
        return True
    return required in str(body or "")


def body_with_unsubscribe(body: str | None, unsubscribe_text: str | None) -> str:
    value = str(body or "").rstrip()
    required = str(unsubscribe_text or "").strip()
    if not required or required in value:
        return value
    return f"{value}\n\n{required}" if value else required


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
    approval_rows = db.scalars(select(LeadApproval).where(LeadApproval.campaign_id == campaign.id).order_by(LeadApproval.updated_at)).all()
    approvals = {item.lead_key: item for item in approval_rows}
    approvals_by_email = {normalize_email(item.email): item for item in approval_rows if item.email}
    approvals_by_domain = {normalize_email(item.domain): item for item in approval_rows if item.domain}
    suppressed_emails, suppressed_domains = suppression_sets(db, campaign.company_id)
    email_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    phone_counts: dict[str, int] = {}
    business_address_counts: dict[str, int] = {}
    normalized = []
    for index, row in enumerate(rows, start=1):
        email = normalize_email(first_text(row, ["email", "public_email", "verified_public_email", "Public Email"]))
        domain = domain_from_email(email) or _first_domain_from_row(row)
        phone = _phone_digits(first_text(row, ["phone", "Phone", "telephone", "phone_number"]))
        business_address = f"{_normalized_identity_text(first_text(row, ['business_name', 'Business Name', 'business', 'company', 'name']))}|{_normalized_identity_text(first_text(row, ['address', 'Address', 'street_address', 'location', 'formatted_address']))}"
        email_counts[email] = email_counts.get(email, 0) + (1 if email else 0)
        domain_counts[domain] = domain_counts.get(domain, 0) + (1 if domain else 0)
        phone_counts[phone] = phone_counts.get(phone, 0) + (1 if phone else 0)
        business_address_counts[business_address] = business_address_counts.get(business_address, 0) + (1 if business_address.strip('|') else 0)
        normalized.append((index, row, email, domain, phone, business_address))
    items = []
    for index, row, email, domain, phone, business_address in normalized:
        business = first_text(row, ["business_name", "Business Name", "business", "company", "name"])
        key = lead_key_for(campaign.id, row, source_run_id, index)
        category, category_reason = _lead_category_from_row(row, email)
        computed = "new" if category == "email_ready" else category
        reason = ""
        if email in suppressed_emails or domain in suppressed_domains:
            category = "do_not_contact"; computed = "do_not_contact"; reason = "Suppression list match"
        elif (email and email_counts.get(email, 0) > 1) or (domain and domain_counts.get(domain, 0) > 1) or (phone and phone_counts.get(phone, 0) > 1) or (business_address.strip('|') and business_address_counts.get(business_address, 0) > 1):
            category = "duplicate"; computed = "duplicate"; reason = "Duplicate identity in campaign source"
        elif computed != "new":
            reason = category_reason.replace("_", " ")
        approval = approvals.get(key) or (approvals_by_email.get(email) if email else None) or (approvals_by_domain.get(domain) if domain else None)
        quality = lead_quality_for(row, email, domain_from_email(email))
        if computed == "assumed_email" or (computed == "new" and quality["email_confidence"] == "assumed"):
            category = "enrichment_needed"
            computed = "assumed_email"
            reason = "Email has no public source evidence"
        state = approval.state if approval else computed
        if approval and approval.state == "rejected":
            category = "previously_rejected"
        elif approval and approval.state == "do_not_contact":
            category = "do_not_contact"
        elif approval and approval.state in {"sent", "contacted"}:
            category = "previously_sent"
        if computed in {"missing_email", "duplicate", "do_not_contact", "assumed_email", "phone_ready", "enrichment_needed", "unreachable", "invalid"} and state in {"new", "approved_for_outreach"}:
            state = computed
        can_send = state == "approved_for_outreach" and computed == "new" and category == "email_ready" and quality["email_confidence"] in {"verified", "public_unverified"}
        items.append({
            "lead_key": key,
            "source_run_id": source_run_id,
            "business": business,
            "email": email,
            "domain": domain,
            "state": state,
            "computed_state": computed,
            "lead_category": category,
            "identity_needs_review": str(first_text(row, ["identity_needs_review", "Identity Needs Review"]) or "").lower() == "true",
            "reason": approval.reason if approval else reason,
            "raw": row,
            "can_send": can_send,
            "approval_eligible": computed == "new" and category == "email_ready" and quality["email_confidence"] in {"verified", "public_unverified"},
            "email_confidence": quality["email_confidence"],
            "lead_quality": quality["lead_quality"],
            "quality_reasons": quality["quality_reasons"],
            "evidence_url": quality["evidence_url"],
            "website": quality["website"],
            "history": approval.history if approval else [],
        })
    return items

def upsert_approval(db: Session, campaign: Campaign, item: dict[str, Any], state: str, user_id: str, reason: str = "") -> LeadApproval:
    if state not in LEAD_STATES:
        raise ValueError(f"Unsupported lead state: {state}")
    if item["computed_state"] in {"missing_email", "duplicate", "do_not_contact", "assumed_email", "phone_ready", "enrichment_needed", "unreachable", "invalid"} and state == "approved_for_outreach":
        raise ValueError(f"Lead cannot be approved while computed state is {item['computed_state']}")
    if state == "approved_for_outreach" and item.get("email_confidence") not in {"verified", "public_unverified"}:
        raise ValueError("Lead cannot be approved until it has public or verified email evidence")
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
    unsubscribe = required_unsubscribe_text(db, campaign.company_id)
    company_name = (company.name or "our team").strip()
    industry = (campaign.industry or "your market").strip()
    location = first_text(item.get("raw") or {}, ["city", "City", "location", "Location", "address", "Address"])
    context = f" in {location}" if location else ""
    body = (
        f"Hi {business},\n\n"
        f"I am reaching out from {company_name}. We are looking for a few {industry} partners{context} who may want to review a simple wholesale/sample option.\n\n"
        f"{offer}\n\n"
        f"If this is relevant, reply here and I can send the short details. If it is not a fit, no problem.\n\n"
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


def bulk_update_drafts(
    db: Session,
    campaign: Campaign,
    user_id: str,
    *,
    action: str,
    draft_ids: list[str] | None = None,
) -> dict[str, Any]:
    action = action.strip().lower()
    drafts = db.scalars(select(OutreachDraft).where(OutreachDraft.campaign_id == campaign.id)).all()
    selected = [draft for draft in drafts if not draft_ids or draft.id in set(draft_ids)]
    if action == "approve_all_generated":
        approvals = {
            item.lead_key
            for item in db.scalars(select(LeadApproval).where(LeadApproval.campaign_id == campaign.id, LeadApproval.state == "approved_for_outreach")).all()
        }
        selected = [draft for draft in drafts if draft.lead_key in approvals and draft.status != "draft_rejected"]
        action = "approve_selected"
    now = utc_now()
    unsubscribe = required_unsubscribe_text(db, campaign.company_id)
    updated: list[str] = []
    created: list[str] = []
    for draft in selected:
        if action == "approve_selected":
            repaired_body = body_with_unsubscribe(draft.body, unsubscribe)
            changed = repaired_body != draft.body or draft.status != "draft_approved" or not draft.approved_by or not draft.approved_at
            draft.body = repaired_body
            draft.status = "draft_approved"
            draft.approved_by = user_id
            draft.approved_at = draft.approved_at or now
            if changed:
                draft.updated_at = now
                updated.append(draft.id)
        elif action == "reject_selected":
            draft.status = "draft_rejected"
            draft.updated_at = now
            updated.append(draft.id)
        elif action == "regenerate_selected":
            latest_version = max([item.version or 1 for item in drafts if item.lead_key == draft.lead_key] or [1])
            regenerated = OutreachDraft(
                company_id=draft.company_id,
                campaign_id=draft.campaign_id,
                employee_id=draft.employee_id,
                hermes_job_id=draft.hermes_job_id,
                source_run_id=draft.source_run_id,
                lead_key=draft.lead_key,
                lead_email=draft.lead_email,
                business=draft.business,
                subject=draft.subject,
                body=draft.body,
                status="draft_needs_review",
                version=latest_version + 1,
                raw={**(draft.raw or {}), "regenerated_from": draft.id, "regenerated_by": user_id},
            )
            db.add(regenerated)
            db.flush()
            created.append(regenerated.id)
        else:
            raise ValueError(f"Unsupported draft bulk action: {action}")
    return {"ok": True, "action": action, "updated": len(updated), "created": len(created), "draft_ids": updated, "created_draft_ids": created, "prospect_emails_sent": 0}




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
    start_date_text = str(settings_payload_data.get("allowed_sending_start_date") or "").strip()
    end_date_text = str(settings_payload_data.get("allowed_sending_end_date") or "").strip()
    day_allowed = not allowed_days or local_now.strftime("%A") in allowed_days
    start_text = str(allowed_hours.get("start") or "00:00")
    end_text = str(allowed_hours.get("end") or "23:59")
    try:
        start = time.fromisoformat(start_text)
        end = time.fromisoformat(end_text)
    except ValueError:
        return {"allowed": False, "reason": "Allowed sending hours are invalid.", "timezone": timezone_name, "local_now": local_now.isoformat(), "window": allowed_hours}
    try:
        start_date = date.fromisoformat(start_date_text) if start_date_text else None
        end_date = date.fromisoformat(end_date_text) if end_date_text else None
    except ValueError:
        return {
            "allowed": False,
            "reason": "Allowed sending dates are invalid.",
            "timezone": timezone_name,
            "local_now": local_now.isoformat(),
            "next_allowed_send_at": None,
            "window": {"days": allowed_days, "hours": {"start": start_text, "end": end_text}, "dates": {"start": start_date_text or None, "end": end_date_text or None}},
        }
    local_date = local_now.date()
    date_allowed = (start_date is None or local_date >= start_date) and (end_date is None or local_date <= end_date)
    now_time = local_now.time().replace(second=0, microsecond=0)
    hour_allowed = start <= now_time <= end if start <= end else now_time >= start or now_time <= end
    allowed = bool(day_allowed and date_allowed and hour_allowed)
    if not date_allowed:
        reason = "Outside the approved sending date range."
    elif not day_allowed or not hour_allowed:
        reason = "Outside the approved sending day/hour window."
    else:
        reason = None
    return {
        "allowed": allowed,
        "reason": reason,
        "timezone": timezone_name,
        "local_now": local_now.isoformat(),
        "next_allowed_send_at": None if allowed else _next_allowed_send_at(local_now, allowed_days, start, start_date, end_date),
        "window": {"days": allowed_days, "hours": {"start": start_text, "end": end_text}, "dates": {"start": start_date_text or None, "end": end_date_text or None}},
    }


def _next_allowed_send_at(local_now: datetime, allowed_days: list[str], start: time, start_date: date | None = None, end_date: date | None = None) -> str | None:
    for offset in range(0, 15):
        candidate_date = local_now.date() + timedelta(days=offset)
        if start_date and candidate_date < start_date:
            candidate_date = start_date
        if end_date and candidate_date > end_date:
            return None
        candidate = datetime.combine(candidate_date, start, tzinfo=local_now.tzinfo)
        if candidate <= local_now:
            continue
        if allowed_days and candidate.strftime("%A") not in allowed_days:
            continue
        return candidate.isoformat()
    return ""


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
    lead_campaign = lead_inventory_campaign_for(db, campaign)
    approvals = db.scalars(select(LeadApproval).where(LeadApproval.campaign_id == campaign.id)).all()
    inventory = get_campaign_email_inventory(db, campaign.company_id, lead_campaign.id, draft_campaign_id=campaign.id)
    suppression_emails, suppression_domains = suppression_sets(db, campaign.company_id)
    latest_drafts = _latest_drafts_by_lead(drafts)
    sent_recipients = _sent_recipients(db, campaign.id)
    counts = _send_counts(db, campaign, settings_data)
    window = _window_status(settings_data)
    unsubscribe_text = str(settings_data.get("unsubscribe_text") or "").strip()
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
        elif not body_has_unsubscribe(draft.body, unsubscribe_text):
            reasons.append("draft_missing_unsubscribe_text")
        if reasons:
            blocked.append({"lead_key": approval.lead_key, "business": approval.business, "email": email, "reasons": reasons})
            continue
        recipients.append({"lead_key": approval.lead_key, "draft_id": draft.id, "business": draft.business or approval.business, "email": email, "subject": draft.subject, "body": draft.body, "body_preview": draft.body[:500], "sender_email": settings_data.get("sender_email"), "reply_to_email": settings_data.get("reply_to_email"), "unsubscribe_text": settings_data.get("unsubscribe_text")})
    approved_drafts = sum(1 for draft in drafts if draft.status == "draft_approved")
    draft_lead_keys = {draft.lead_key for draft in drafts}
    approved_without_drafts = [approval for approval in approvals if approval.state == "approved_for_outreach" and approval.lead_key not in draft_lead_keys]
    max_batch = min(counts["daily_remaining"], counts["hourly_remaining"], _safe_int(limit, counts["daily_remaining"]) if limit is not None else counts["daily_remaining"])
    selected = recipients[:max(0, max_batch)]
    can_send = bool(settings_data.get("prospect_sending_enabled") and sender.get("verified") and window.get("allowed") and counts["daily_remaining"] > 0 and counts["hourly_remaining"] > 0 and model_guard.get("allowed") and selected)
    canonical_approved = int(inventory.get("approved_unsent", approved_leads))
    canonical_pending_drafts = int(inventory.get("drafts_pending_review", 0))
    canonical_approved_drafts = int(inventory.get("drafts_approved_unsent", approved_drafts))
    return {"campaign_id": campaign.id, "company_id": campaign.company_id, "lead_source_campaign_id": lead_campaign.id, "mode": SEND_MODE_DRY_RUN, "available_modes": [SEND_MODE_DRY_RUN, SEND_MODE_INTERNAL_TEST, SEND_MODE_REAL_PROSPECT], "confirmation_required": {"send_one": CONFIRM_SEND_ONE, "batch": CONFIRM_SEND_BATCH}, "prospect_emails_sent": 0, "sender": sender, "settings": {"sender_email": settings_data.get("sender_email"), "reply_to_email": settings_data.get("reply_to_email"), "unsubscribe_text": settings_data.get("unsubscribe_text"), "prospect_sending_enabled": settings_data.get("prospect_sending_enabled")}, "window": window, "limits": counts, "model_guard": model_guard, "hermes_guard": {"mode": "jobs_json", "employee_id": getattr(employee, "id", None), "hermes_job_id": getattr(employee, "hermes_job_id", None), "allowed": model_guard.get("allowed")}, "coverage": {"total_leads": int(inventory.get("active_unsent_email_ready", len(approvals))), "approved_leads": canonical_approved, "drafts_generated": len(drafts), "approved_drafts": canonical_approved_drafts, "approved_leads_without_drafts": max(0, canonical_approved - canonical_pending_drafts - canonical_approved_drafts), "pending_draft_approval": canonical_pending_drafts, "ready_to_send": int(inventory.get("ready_to_send", len(recipients))), "selected_for_batch": len(selected), "blocked_recipients": len(blocked)}, "email_inventory": inventory, "recipients": selected, "eligible_recipients": recipients, "blocked_recipients": blocked, "blockers": list(dict.fromkeys(blockers)), "can_send_one_real_email": bool(can_send and selected), "can_send_controlled_batch": can_send}


def controlled_batch_preview(db: Session, campaign: Campaign, *, limit: int | None = None) -> dict[str, Any]:
    return _batch_snapshot(db, campaign, limit=limit)


def prepare_controlled_batch(db: Session, campaign: Campaign, user_id: str, *, limit: int | None = None, dry_run: bool = True) -> dict[str, Any]:
    snapshot = _batch_snapshot(db, campaign, limit=limit)
    if not dry_run:
        raise ValueError("Real prospect sending is not enabled from Voryx QA mode; use dry_run=true until explicitly approved.")
    dry_run_recipients = list(snapshot.get("recipients") or [])
    if not dry_run_recipients and snapshot.get("eligible_recipients"):
        dry_run_recipients = list(snapshot["eligible_recipients"])[: max(1, min(_safe_int(limit, 5) if limit is not None else 5, 5))]
        snapshot["recipients"] = dry_run_recipients
        snapshot["coverage"] = {**snapshot.get("coverage", {}), "selected_for_batch": len(dry_run_recipients)}
    if not dry_run_recipients:
        raise ValueError("Controlled dry-run blocked: no approved leads with approved drafts are ready to preview")
    now = utc_now()
    batch_id = f"batch-{campaign.id}-{int(now.timestamp())}"
    evidence_path = _workspace_evidence_path(campaign.company_id, campaign.id, batch_id)
    prepared = []
    for item in dry_run_recipients:
        event = OutreachEvent(event_id=f"{batch_id}-{item['lead_key']}", campaign_id=campaign.id, company_id=campaign.company_id, recipient=item["email"], business=item.get("business"), subject=item.get("subject"), attempted_at=now, sent_at=None, status="prepared_dry_run", provider="voryx_controlled_batch_guard", dry_run=True, source_file=evidence_path, raw={"batch_id": batch_id, "draft_id": item.get("draft_id"), "requested_by": user_id, "prospect_emails_sent": 0, "body_preview": item.get("body_preview")})
        db.add(event)
        prepared.append(event.event_id)
    job = Job(campaign_id=campaign.id, connector="hermes", task_type="Controlled Outreach Batch", status=JobStatus.completed, payload={"batch_id": batch_id, "dry_run": True, "limit": limit, "source": "dashboard_controlled_batch"}, result={"batch_id": batch_id, "prepared_events": prepared, "prospect_emails_sent": 0, "preview": snapshot}, logs=["Controlled batch prepared in dry-run mode; no prospect email sent.", f"Eligible recipients: {len(snapshot['eligible_recipients'])}", f"Prepared recipients: {len(dry_run_recipients)}"], evidence_type="controlled_batch_preview", source_output_path=evidence_path, verification_reason="dry_run_prepared_only_no_provider_receipt", attempts=1, max_attempts=1, started_at=now, ended_at=now, created_at=now)
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


def schedule_controlled_batch_next_window(db: Session, campaign: Campaign, user_id: str, *, limit: int | None = 5) -> dict[str, Any]:
    snapshot = _batch_snapshot(db, campaign, limit=limit)
    recipients = snapshot.get("recipients") or snapshot.get("eligible_recipients") or []
    if not recipients:
        raise ValueError("Schedule blocked: no approved leads with approved drafts are ready to send")
    next_allowed = snapshot.get("window", {}).get("next_allowed_send_at") or snapshot.get("window", {}).get("local_now")
    now = utc_now()
    batch_id = f"scheduled-batch-{campaign.id}-{int(now.timestamp())}"
    evidence_path = _workspace_evidence_path(campaign.company_id, campaign.id, batch_id)
    selected = recipients[: max(1, min(int(limit or 5), 5))]
    job = Job(
        campaign_id=campaign.id,
        employee_id=snapshot.get("hermes_guard", {}).get("employee_id"),
        connector="hermes",
        task_type="Controlled Outreach Batch",
        status=JobStatus.queued,
        payload={
            "batch_id": batch_id,
            "mode": "scheduled_next_window",
            "scheduled_for": next_allowed,
            "limit": limit,
            "source": "ai_sales_employee_control_center",
            "dry_run": False,
            "requires_confirmation": True,
        },
        result={"batch_id": batch_id, "scheduled_for": next_allowed, "selected_recipients": len(selected), "prospect_emails_sent": 0, "snapshot": snapshot},
        logs=[
            "Controlled batch scheduled from AI Sales Employee Control Center.",
            "No prospect email was sent during scheduling.",
            f"Scheduled for next allowed window: {next_allowed or 'unknown'}",
        ],
        evidence_type="controlled_batch_schedule",
        source_output_path=evidence_path,
        verification_reason="scheduled_only_no_provider_receipt",
        attempts=0,
        max_attempts=1,
        created_at=now,
    )
    db.add(job)
    db.flush()
    evidence = {
        "batch_id": batch_id,
        "job_id": job.id,
        "created_at": now.isoformat() + "Z",
        "scheduled_for": next_allowed,
        "selected_recipients": selected,
        "prospect_emails_sent": 0,
        "send_requires_confirmation": True,
        "snapshot": snapshot,
    }
    if evidence_path:
        Path(evidence_path).write_text(json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return {"ok": True, "batch_id": batch_id, "job_id": job.id, "scheduled_for": next_allowed, "evidence_path": evidence_path, "selected_recipients": len(selected), "prospect_emails_sent": 0, "snapshot": snapshot}


def _write_send_evidence(company_id: str, campaign_id: str, batch_id: str, evidence: dict[str, Any]) -> str | None:
    path = _workspace_evidence_path(company_id, campaign_id, batch_id)
    if path:
        Path(path).write_text(json.dumps(evidence, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path


def send_real_controlled_batch(
    db: Session,
    campaign: Campaign,
    user_id: str,
    *,
    limit: int | None = None,
    confirmation: str,
    send_one: bool = False,
    process_now: bool = True,
) -> dict[str, Any]:
    expected = CONFIRM_SEND_ONE if send_one else CONFIRM_SEND_BATCH
    if confirmation != expected:
        raise ValueError(f"real_send_confirmed required: type {expected}")
    snapshot = _batch_snapshot(db, campaign, limit=1 if send_one else limit)
    if not snapshot["can_send_controlled_batch"]:
        raise ValueError("Controlled real send blocked: " + "; ".join(snapshot["blockers"] or ["no eligible recipients"]))
    recipients = snapshot["recipients"][:1] if send_one else snapshot["recipients"]
    if send_one and len(recipients) != 1:
        raise ValueError("Send 1 real email requires exactly one eligible recipient")
    now = utc_now()
    batch_id = f"real-batch-{campaign.id}-{int(now.timestamp())}"
    prepared_events: list[str] = []
    queued_jobs: list[str] = []
    queued: list[dict[str, Any]] = []
    for item in recipients:
        event_id = f"{batch_id}-{item['lead_key']}"
        event = OutreachEvent(
            event_id=event_id,
            campaign_id=campaign.id,
            company_id=campaign.company_id,
            employee_id=snapshot.get("hermes_guard", {}).get("employee_id"),
            lead_id=None,
            recipient=item["email"],
            business=item.get("business"),
            subject=item.get("subject"),
            attempted_at=now,
            sent_at=None,
            status="queued_by_provider",
            message_id=None,
            provider="himalaya",
            dry_run=False,
            raw={"batch_id": batch_id, "draft_id": item.get("draft_id"), "requested_by": user_id, "send_mode": SEND_MODE_REAL_PROSPECT, "confirmation": expected},
        )
        db.add(event)
        db.flush()
        job, queued_info = enqueue_controlled_outreach_delivery(
            db,
            campaign_id=campaign.id,
            company_id=campaign.company_id,
            employee_id=snapshot.get("hermes_guard", {}).get("employee_id"),
            lead_key=item["lead_key"],
            draft_id=item["draft_id"],
            recipient=item["email"],
            business=item.get("business"),
            subject=item.get("subject") or "",
            body=item.get("body") or item.get("body_preview") or "",
            sender_email=item.get("sender_email") or "",
            reply_to_email=item.get("reply_to_email") or "",
            unsubscribe_text=item.get("unsubscribe_text") or "",
            requested_by=user_id,
            batch_id=batch_id,
            event_id=event_id,
        )
        prepared_events.append(event_id)
        queued_jobs.append(job.id)
        queued.append({"event_id": event_id, "job_id": job.id, "recipient": item["email"], "request_path": queued_info["request_path"]})
    db.flush()
    process_result = None
    if process_now:
        process_result = process_one_mail_request()
        ingest_internal_mail_receipts(db)
    sent_events = db.scalars(select(OutreachEvent).where(OutreachEvent.event_id.in_(prepared_events))).all()
    sent = [event for event in sent_events if event.status == "sent" and event.message_id]
    failed = [event for event in sent_events if event.status != "sent" or not event.message_id]
    for event in failed:
        if event.status == "queued_by_provider":
            event.status = "provider_pending"
    evidence = {
        "batch_id": batch_id,
        "send_mode": SEND_MODE_REAL_PROSPECT,
        "send_one": send_one,
        "created_at": now.isoformat() + "Z",
        "queued": queued,
        "sent_count": len(sent),
        "failed_count": len(failed),
        "skipped_count": len(snapshot["blocked_recipients"]),
        "message_ids": [event.message_id for event in sent],
        "provider_errors": [event.error_message for event in failed if event.error_message],
        "process_result": {"returncode": process_result.returncode, "stdout": process_result.stdout[-1000:], "stderr": process_result.stderr[-1000:]} if process_result else None,
        "snapshot": snapshot,
    }
    evidence_path = _write_send_evidence(campaign.company_id, campaign.id, batch_id, evidence)
    for event in sent_events:
        event.source_file = evidence_path
    if failed and process_now:
        # Receipt is mandatory. Leave queued/provider_pending evidence but do not claim success.
        raise ValueError("receipt_missing: provider receipt/message_id was not recorded for every queued email")
    evidence["evidence_path"] = evidence_path
    return evidence


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


def create_internal_test_event(db: Session, campaign: Campaign, draft: OutreachDraft, user_id: str, *, process_now: bool = False) -> OutreachEvent:
    blockers = send_blockers(db, campaign, draft, internal_test=True)
    if blockers:
        raise ValueError("Internal test blocked: " + ", ".join(blockers))
    settings_row = db.scalar(select(CompanyOutreachSettings).where(CompanyOutreachSettings.company_id == campaign.company_id))
    settings_data = settings_payload(settings_row, campaign.company_id)
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
    db.flush()
    if not process_now:
        return event
    batch_id = f"internal-test-{campaign.id}-{int(now.timestamp())}"
    body = body_with_unsubscribe(
        f"Internal test copy for {draft.business or draft.lead_email or draft.lead_key}.\n\n{draft.body}",
        settings_data.get("unsubscribe_text"),
    )
    job, queued = enqueue_controlled_outreach_delivery(
        db,
        campaign_id=campaign.id,
        company_id=campaign.company_id,
        employee_id=draft.employee_id,
        lead_key=draft.lead_key,
        draft_id=draft.id,
        recipient=APPROVED_INTERNAL_RECIPIENT,
        business=f"Internal test: {draft.business or draft.lead_email or draft.lead_key}",
        subject=event.subject or f"[INTERNAL TEST] {draft.subject}",
        body=body,
        sender_email=normalize_email(settings_data.get("sender_email")) or "voryxio@gmail.com",
        reply_to_email=normalize_email(settings_data.get("reply_to_email")) or "voryxio@gmail.com",
        unsubscribe_text=str(settings_data.get("unsubscribe_text") or "").strip(),
        requested_by=user_id,
        batch_id=batch_id,
        event_id=event.event_id,
        internal_test=True,
    )
    event.status = "internal_test_queued"
    event.raw = {**(event.raw or {}), "mail_queue_job_id": job.id, "request_path": queued.get("request_path")}
    process_result = process_one_mail_request()
    ingest_internal_mail_receipts(db)
    db.flush()
    db.refresh(event)
    if event.status != "internal_test_sent" or not event.message_id:
        event.status = "internal_test_failed"
        event.raw = {
            **(event.raw or {}),
            "process_result": {
                "returncode": process_result.returncode,
                "stdout": process_result.stdout[-1000:],
                "stderr": process_result.stderr[-1000:],
            },
        }
        raise ValueError("Internal test failed: provider receipt/message_id was not recorded")
    return event
