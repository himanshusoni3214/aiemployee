import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    Campaign,
    LeadApproval,
    OutreachDraft,
    OutreachEvent,
    SuppressionEntry,
)
from app.core.config import settings


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SENT_EVENT_STATES = {"sent", "delivered", "accepted"}
BLOCKED_STATES = {"rejected", "do_not_contact", "unsubscribed", "sent", "contacted"}
BLOCKED_CATEGORIES = {"duplicate", "previously_rejected", "do_not_contact", "previously_sent", "invalid"}
BIBS_LEAD_CAMPAIGN_ID = "campaign-brew-it-by-sash-lead-research"


def normalize_email(value: str | None) -> str:
    text = str(value or "").strip().lower()
    return text if EMAIL_RE.match(text) else ""


def domain_from_email(value: str | None) -> str:
    email = normalize_email(value)
    return email.split("@", 1)[1] if "@" in email else ""


def domain_from_url(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text)
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()
    return text.removeprefix("www.")


def normalized_text(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"[^a-z0-9@.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def first_text(row: dict[str, Any], keys: list[str]) -> str:
    normalized = {re.sub(r"[^a-z0-9]+", "_", str(k).lower()).strip("_"): v for k, v in row.items()}
    for key in keys:
        value = normalized.get(re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_"))
        if str(value or "").strip():
            return str(value).strip()
    return ""


def public_email_evidence(row: dict[str, Any], email: str) -> str:
    evidence = first_text(row, ["email_evidence", "Email Evidence"])
    if evidence:
        return evidence
    source = first_text(row, ["source_url", "Source URL", "evidence_url", "Evidence URL", "contact_page"])
    source_domain = domain_from_url(source)
    email_domain = domain_from_email(email)
    return source if source and email_domain and (source_domain == email_domain or source_domain.endswith(email_domain) or email_domain.endswith(source_domain)) else ""


def canonical_identity(*, lead_key: str | None = None, email: str | None = None, domain: str | None = None, business: str | None = None, website: str | None = None, phone: str | None = None) -> str:
    email = normalize_email(email)
    if email:
        return f"email:{email}"
    domain = str(domain or "").strip().lower() or domain_from_url(website)
    if domain:
        return f"domain:{domain}"
    business_key = normalized_text(business)
    phone_key = re.sub(r"\D+", "", str(phone or ""))
    if business_key and phone_key:
        return f"business-phone:{business_key}:{phone_key}"
    if business_key:
        return f"business:{business_key}"
    return f"lead:{lead_key or ''}"


@dataclass
class InventoryEntry:
    identity: str
    lead_key: str = ""
    email: str = ""
    domain: str = ""
    business: str = ""
    state: str = "new"
    category: str = "new"
    has_public_email_evidence: bool = False
    assumed_email: bool = False
    suppressed: bool = False
    sent: bool = False
    draft_status: str = ""
    source: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def email_ready(self) -> bool:
        return bool(self.email and self.has_public_email_evidence and not self.assumed_email and self.category == "email_ready")

    @property
    def blocked(self) -> bool:
        return self.suppressed or self.sent or self.state in BLOCKED_STATES or self.category in BLOCKED_CATEGORIES

    @property
    def active_email_ready(self) -> bool:
        return self.email_ready and not self.blocked


def _entry_from_item(item: dict[str, Any]) -> InventoryEntry:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    email = normalize_email(item.get("email") or first_text(raw, ["Public Email", "email", "public_email", "verified_public_email"]))
    domain = str(item.get("domain") or domain_from_email(email) or domain_from_url(item.get("website") or first_text(raw, ["Website", "website", "Source URL", "source_url"]))).strip().lower()
    category = str(item.get("lead_category") or item.get("computed_state") or item.get("state") or "").strip() or "new"
    evidence = public_email_evidence(raw, email) or str(item.get("evidence_url") or "").strip()
    if category == "duplicate" and email and evidence:
        category = "email_ready"
    assumed = str(item.get("email_confidence") or "").lower() == "assumed" or category == "assumed_email"
    return InventoryEntry(
        identity=canonical_identity(lead_key=item.get("lead_key"), email=email, domain=domain, business=item.get("business"), website=item.get("website"), phone=first_text(raw, ["Phone", "phone"])),
        lead_key=str(item.get("lead_key") or ""),
        email=email,
        domain=domain,
        business=str(item.get("business") or first_text(raw, ["Business Name", "business"]) or ""),
        state=str(item.get("state") or "new"),
        category=category,
        has_public_email_evidence=bool(email and evidence),
        assumed_email=assumed,
        source="review_item",
        raw=raw,
    )


def _hermes_physical_path(container_path: str) -> Path | None:
    if not settings.hermes_data_path:
        return None
    text = str(container_path or "").strip()
    root = Path(settings.hermes_data_path).resolve()
    candidate = root if text == "/opt/data" else root / text.removeprefix("/opt/data/").lstrip("/")
    try:
        resolved = candidate.resolve()
    except FileNotFoundError:
        resolved = candidate.absolute()
    if root != resolved and root not in resolved.parents:
        return None
    return resolved


def _campaign_output_dirs(campaign: Campaign) -> list[Path]:
    paths: list[Path] = []
    workspace = _hermes_physical_path(f"/opt/data/home/voryx_workspaces/{campaign.company_id}/{campaign.id}/leads")
    if workspace:
        paths.append(workspace)
    if campaign.id == BIBS_LEAD_CAMPAIGN_ID:
        legacy = _hermes_physical_path("/opt/data/home/leads")
        if legacy:
            paths.append(legacy)
    return paths


def _csv_review_items_from_outputs(campaign: Campaign, limit: int = 50) -> list[dict[str, Any]]:
    candidates: list[Path] = []
    for directory in _campaign_output_dirs(campaign):
        if not directory.exists():
            continue
        if campaign.id == BIBS_LEAD_CAMPAIGN_ID and directory.name == "leads":
            patterns = ("leads_brew_it_combined_*.csv", "leads_brew_it_*.csv", "leads_verified.csv")
        else:
            patterns = ("*.csv",)
        for pattern in patterns:
            candidates.extend(path for path in directory.glob(pattern) if path.is_file())
    unique = {path.resolve(): path for path in candidates}
    rows: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    for path in sorted(unique.values(), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for index, row in enumerate(csv.DictReader(handle), start=1):
                raw = dict(row)
                raw.setdefault("Source File", f"/opt/data/{path.resolve().relative_to(Path(settings.hermes_data_path).resolve()).as_posix()}" if settings.hermes_data_path else str(path))
                email = normalize_email(first_text(raw, ["Public Email", "email", "public_email", "verified_public_email"]))
                business = first_text(raw, ["Business Name", "business_name", "business", "company", "name"])
                website = first_text(raw, ["Website", "website", "domain", "url"])
                phone = first_text(raw, ["Phone", "phone", "telephone", "phone_number"])
                identity = canonical_identity(email=email, domain=domain_from_email(email) or domain_from_url(website), business=business, website=website, phone=phone)
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
                category = first_text(raw, ["Lead Category", "lead_category"]) or ("email_ready" if email and public_email_evidence(raw, email) else "new")
                rows.append({
                    "lead_key": first_text(raw, ["canonical_lead_id", "stable_id"]) or re.sub(r"[^a-f0-9]", "", identity.lower())[:24] or f"row-{index}",
                    "business": business,
                    "email": email,
                    "website": website,
                    "lead_category": category,
                    "state": "new",
                    "raw": raw,
                })
    return rows


def _entry_from_approval(approval: LeadApproval) -> InventoryEntry:
    raw = approval.raw if isinstance(approval.raw, dict) else {}
    email = normalize_email(approval.email or first_text(raw, ["Public Email", "email", "public_email", "verified_public_email"]))
    domain = str(approval.domain or domain_from_email(email) or domain_from_url(first_text(raw, ["Website", "website", "Source URL", "source_url"]))).strip().lower()
    category = first_text(raw, ["Lead Category", "lead_category"]) or ("email_ready" if email and public_email_evidence(raw, email) else "enrichment_needed")
    if category == "duplicate" and email and public_email_evidence(raw, email):
        category = "email_ready"
    return InventoryEntry(
        identity=canonical_identity(lead_key=approval.lead_key, email=email, domain=domain, business=approval.business, website=first_text(raw, ["Website", "website"]), phone=first_text(raw, ["Phone", "phone"])),
        lead_key=approval.lead_key,
        email=email,
        domain=domain,
        business=approval.business or first_text(raw, ["Business Name", "business"]),
        state=approval.state,
        category=category,
        has_public_email_evidence=bool(email and public_email_evidence(raw, email)),
        assumed_email=category == "assumed_email",
        source="approval",
        raw=raw,
    )


def _prefer_entry(existing: InventoryEntry, incoming: InventoryEntry) -> InventoryEntry:
    if incoming.state in BLOCKED_STATES or incoming.category in {"previously_rejected", "do_not_contact", "previously_sent"}:
        incoming.draft_status = existing.draft_status or incoming.draft_status
        incoming.sent = existing.sent or incoming.sent
        incoming.suppressed = existing.suppressed or incoming.suppressed
        return incoming
    if existing.state in BLOCKED_STATES or existing.category in {"previously_rejected", "do_not_contact", "previously_sent"}:
        return existing
    if existing.source == "review_item" and incoming.source != "review_item":
        if incoming.state and incoming.state != "new":
            existing.state = incoming.state
        return existing
    if incoming.source == "review_item" and existing.source != "review_item":
        incoming.draft_status = existing.draft_status or incoming.draft_status
        incoming.sent = existing.sent or incoming.sent
        incoming.suppressed = existing.suppressed or incoming.suppressed
        if existing.state in BLOCKED_STATES and incoming.state not in BLOCKED_STATES:
            incoming.state = existing.state
        return incoming
    if existing.active_email_ready and not incoming.active_email_ready:
        return existing
    return incoming if incoming.email_ready and not existing.email_ready else existing


def get_campaign_email_inventory(
    db: Session,
    company_id: str,
    campaign_id: str,
    *,
    review_items: list[dict[str, Any]] | None = None,
    draft_campaign_id: str | None = None,
) -> dict[str, Any]:
    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        return _empty_inventory(company_id, campaign_id)

    entries: dict[str, InventoryEntry] = {}
    raw_historical_email_ready_rows = 0
    unique_historical_email_ready_identities: set[str] = set()

    if review_items is None:
        review_items = _csv_review_items_from_outputs(campaign)

    for item in review_items or []:
        entry = _entry_from_item(item)
        if entry.email and entry.has_public_email_evidence:
            raw_historical_email_ready_rows += 1
            unique_historical_email_ready_identities.add(entry.identity)
        entries[entry.identity] = _prefer_entry(entries[entry.identity], entry) if entry.identity in entries else entry

    for approval in db.scalars(select(LeadApproval).where(LeadApproval.company_id == company_id, LeadApproval.campaign_id == campaign_id)).all():
        entry = _entry_from_approval(approval)
        entries[entry.identity] = _prefer_entry(entries[entry.identity], entry) if entry.identity in entries else entry

    suppressed_emails = set()
    suppressed_domains = set()
    for suppression in db.scalars(select(SuppressionEntry).where(SuppressionEntry.company_id == company_id)).all():
        if suppression.kind == "email":
            suppressed_emails.add(normalize_email(suppression.value))
        elif suppression.kind == "domain":
            suppressed_domains.add(str(suppression.value or "").strip().lower())

    sent_emails = set()
    for event in db.scalars(select(OutreachEvent).where(OutreachEvent.company_id == company_id, OutreachEvent.dry_run == False, OutreachEvent.status.in_(list(SENT_EVENT_STATES)))).all():
        email = normalize_email(event.recipient)
        if email:
            sent_emails.add(email)

    latest_drafts: dict[str, OutreachDraft] = {}
    latest_drafts_by_identity: dict[str, OutreachDraft] = {}
    draft_campaign = draft_campaign_id or campaign_id
    for draft in db.scalars(select(OutreachDraft).where(OutreachDraft.company_id == company_id, OutreachDraft.campaign_id == draft_campaign).order_by(OutreachDraft.updated_at, OutreachDraft.created_at)).all():
        latest_drafts[draft.lead_key] = draft
        latest_drafts_by_identity[canonical_identity(lead_key=draft.lead_key, email=draft.lead_email, business=draft.business)] = draft

    for entry in entries.values():
        if entry.email in suppressed_emails or entry.domain in suppressed_domains:
            entry.suppressed = True
        if entry.email in sent_emails:
            entry.sent = True
        draft = latest_drafts.get(entry.lead_key) or latest_drafts_by_identity.get(entry.identity)
        if draft:
            entry.draft_status = draft.status

    active_email_ready_entries = [entry for entry in entries.values() if entry.active_email_ready]
    approved_unsent_entries = [entry for entry in active_email_ready_entries if entry.state == "approved_for_outreach"]
    drafts_pending_review_entries = [entry for entry in active_email_ready_entries if entry.draft_status in {"draft_created", "draft_needs_review"}]
    drafts_approved_unsent_entries = [entry for entry in active_email_ready_entries if entry.draft_status == "draft_approved"]
    ready_to_send_entries = [entry for entry in approved_unsent_entries if entry.draft_status == "draft_approved"]
    target = int(campaign.daily_lead_goal or 25)
    inventory = {
        "company_id": company_id,
        "campaign_id": campaign_id,
        "target": target,
        "unique_email_ready_active": len(active_email_ready_entries),
        "active_unsent_email_ready": len(active_email_ready_entries),
        "approved_unsent": len(approved_unsent_entries),
        "drafts_pending_review": len(drafts_pending_review_entries),
        "draft_pending_review": len(drafts_pending_review_entries),
        "drafts_approved_unsent": len(drafts_approved_unsent_entries),
        "draft_approved_unsent": len(drafts_approved_unsent_entries),
        "ready_to_send": min(len(ready_to_send_entries), len(approved_unsent_entries), len(drafts_approved_unsent_entries)),
        "sent": sum(1 for entry in entries.values() if entry.sent or entry.state in {"sent", "contacted"} or entry.category == "previously_sent"),
        "rejected": sum(1 for entry in entries.values() if entry.state == "rejected" or entry.category == "previously_rejected"),
        "DNC": sum(1 for entry in entries.values() if entry.state in {"do_not_contact", "unsubscribed"} or entry.category == "do_not_contact"),
        "do_not_contact": sum(1 for entry in entries.values() if entry.state in {"do_not_contact", "unsubscribed"} or entry.category == "do_not_contact"),
        "suppressed": sum(1 for entry in entries.values() if entry.suppressed),
        "duplicates": max(0, len(review_items or []) - len({entry.identity for entry in map(_entry_from_item, review_items or [])})),
        "enrichment_needed": sum(1 for entry in entries.values() if not entry.blocked and entry.category in {"enrichment_needed", "assumed_email", "unreachable"}),
        "phone_ready": sum(1 for entry in entries.values() if not entry.blocked and entry.category == "phone_ready"),
        "raw_historical_email_ready_rows": raw_historical_email_ready_rows,
        "unique_historical_email_ready_identities": len(unique_historical_email_ready_identities),
    }
    inventory["remaining_to_target"] = max(0, target - inventory["unique_email_ready_active"])
    inventory["remaining_needed"] = inventory["remaining_to_target"]
    return inventory


def _empty_inventory(company_id: str, campaign_id: str) -> dict[str, Any]:
    keys = [
        "unique_email_ready_active", "active_unsent_email_ready", "approved_unsent", "drafts_pending_review",
        "draft_pending_review", "drafts_approved_unsent", "draft_approved_unsent", "ready_to_send", "sent",
        "rejected", "DNC", "do_not_contact", "suppressed", "duplicates", "enrichment_needed", "phone_ready",
        "raw_historical_email_ready_rows", "unique_historical_email_ready_identities", "remaining_to_target",
        "remaining_needed",
    ]
    return {"company_id": company_id, "campaign_id": campaign_id, "target": 25, **{key: 0 for key in keys}}
