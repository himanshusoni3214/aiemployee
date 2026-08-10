from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import (
    CallAttempt,
    CallCampaignSettings,
    CallComplianceItem,
    CallQueueItem,
    CallScriptVersion,
    ConsentSourceProfile,
    ConsentedCallingLead,
    SuppressionEntry,
)
from app.services.calling import normalize_phone, valid_us_ca_e164


CANADIAN_AREA_CODES = {
    '204', '226', '236', '249', '250', '263', '289', '306', '343', '354',
    '365', '367', '368', '382', '403', '416', '418', '428', '431', '437',
    '438', '450', '468', '474', '506', '514', '519', '548', '579', '581',
    '584', '587', '604', '613', '639', '647', '672', '683', '705', '709',
    '742', '753', '778', '780', '782', '807', '819', '825', '867', '873',
    '879', '902', '905',
}

ACTIVE_QUEUE_STATUSES = {'queued', 'calling', 'answered'}
SUCCESS_QUEUE_STATUSES = {'completed', 'appointment'}
RUNNING_CAMPAIGN_STATUSES = {'running', 'waiting_for_window'}


@dataclass(frozen=True)
class EligibilityBlocker:
    code: str
    message: str
    category: str

    def payload(self) -> dict:
        return {'code': self.code, 'message': self.message, 'category': self.category}


@dataclass(frozen=True)
class EligibilityResult:
    ready: bool
    classification: str
    blockers: tuple[EligibilityBlocker, ...]
    calling_now: bool
    next_window_at: datetime | None
    local_time_label: str

    def payload(self) -> dict:
        return {
            'ready': self.ready,
            'classification': self.classification,
            'blockers': [item.payload() for item in self.blockers],
            'calling_now': self.calling_now,
            'next_window_at': self.next_window_at,
            'local_time_label': self.local_time_label,
        }


def is_canadian_e164(phone: str) -> bool:
    return valid_us_ca_e164(phone) and phone[2:5] in CANADIAN_AREA_CODES


def calling_window(
    timezone_name: str,
    campaign: CallCampaignSettings | None = None,
    now: datetime | None = None,
) -> tuple[bool, datetime | None, str]:
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        return False, None, 'Invalid recipient timezone'
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local = instant.astimezone(zone)
    configured_days = campaign.allowed_calling_days if campaign and campaign.allowed_calling_days else [0, 1, 2, 3, 4, 5]
    configured_hours = campaign.allowed_calling_hours if campaign and campaign.allowed_calling_hours else {
        'weekday': {'start': '10:00', 'end': '19:00'},
        'saturday': {'start': '10:30', 'end': '16:00'},
    }

    def hours_for(day: int) -> tuple[int, int] | None:
        if day not in configured_days:
            return None
        key = 'saturday' if day == 5 else 'weekday'
        values = configured_hours.get(str(day)) or configured_hours.get(key) or {}
        try:
            start_h, start_m = (int(part) for part in str(values.get('start') or '10:00').split(':', 1))
            end_h, end_m = (int(part) for part in str(values.get('end') or '19:00').split(':', 1))
            return start_h * 60 + start_m, end_h * 60 + end_m
        except Exception:
            return None

    today_hours = hours_for(local.weekday())
    minute = local.hour * 60 + local.minute
    if today_hours and today_hours[0] <= minute < today_hours[1]:
        return True, local, f'{local.strftime("%A %Y-%m-%d %H:%M")} {local.tzname()}'
    for offset in range(0, 8):
        candidate_day = (local + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        values = hours_for(candidate_day.weekday())
        if not values:
            continue
        candidate = candidate_day + timedelta(minutes=values[0])
        if candidate > local:
            return False, candidate.astimezone(timezone.utc).replace(tzinfo=None), f'{local.strftime("%A %Y-%m-%d %H:%M")} {local.tzname()}'
    return False, None, f'{local.strftime("%A %Y-%m-%d %H:%M")} {local.tzname()}'


def _blocker(code: str, message: str, category: str = 'blocked') -> EligibilityBlocker:
    return EligibilityBlocker(code, message, category)


def evaluate_calling_lead(
    db: Session,
    lead: ConsentedCallingLead,
    *,
    now: datetime | None = None,
    require_window: bool = False,
    require_campaign_running: bool = False,
    exclude_queue_item_id: str | None = None,
    legacy_compatible: bool = False,
) -> EligibilityResult:
    now = now or datetime.utcnow()
    campaign = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == lead.campaign_id))
    phone = normalize_phone(lead.phone_number)
    blockers: list[EligibilityBlocker] = []
    if not is_canadian_e164(phone):
        blockers.append(_blocker('PHONE_NOT_CANADIAN', 'Phone number is not a valid Canadian number'))
    if phone != normalize_phone(lead.consented_number):
        blockers.append(_blocker('CONSENT_NUMBER_MISMATCH', 'Called number does not exactly match consented number'))
    if lead.consent_status != 'verified':
        blockers.append(_blocker('CONSENT_NOT_VERIFIED', 'Consent requires review', 'review'))
    if not lead.consent_reference and not (legacy_compatible and lead.consent_proof):
        blockers.append(_blocker('CONSENT_REFERENCE_MISSING', 'Consent reference is missing', 'review'))
    if not lead.automated_or_synthesized_call_consent:
        blockers.append(_blocker('AUTOMATED_CALL_PERMISSION_MISSING', 'Automated or synthesized call consent is missing'))
    if not lead.organization_authorized:
        blockers.append(_blocker('ORGANIZATION_NOT_AUTHORIZED', 'Consent does not authorize the represented organization'))
    if not lead.consent_source or not lead.consent_timestamp:
        blockers.append(_blocker('CONSENT_EVIDENCE_MISSING', 'Consent source or timestamp is missing', 'review'))
    if not lead.consent_text or not lead.consent_proof:
        blockers.append(_blocker('CONSENT_PROOF_MISSING', 'Consent wording or proof is missing', 'review'))
    if lead.consent_withdrawn:
        blockers.append(_blocker('CONSENT_WITHDRAWN', 'Consent was withdrawn'))
    if lead.consent_expiry and lead.consent_expiry <= now:
        blockers.append(_blocker('CONSENT_EXPIRED', 'Consent is expired'))

    profile = db.get(ConsentSourceProfile, lead.source_profile_id) if lead.source_profile_id else None
    if (not profile or profile.campaign_id != lead.campaign_id) and not legacy_compatible:
        blockers.append(_blocker('CONSENT_PROFILE_MISSING', 'Approved Consent Source Profile is missing', 'review'))
    elif profile and (not profile.organization_authorized or not profile.automated_call_permission):
        blockers.append(_blocker('CONSENT_PROFILE_NOT_APPROVED', 'Consent Source Profile does not authorize automated calling'))
    elif profile and profile.expires_at and profile.expires_at <= now:
        blockers.append(_blocker('CONSENT_PROFILE_EXPIRED', 'Consent Source Profile is expired'))

    if lead.dncl_status != 'clear':
        blockers.append(_blocker('DNCL_NOT_CLEAR', 'DNCL review is not clear', 'review'))
    if not lead.internal_dnc_clear:
        blockers.append(_blocker('DNC_NOT_CLEAR', 'Internal DNC review is not clear', 'review'))
    if not lead.suppression_clear:
        blockers.append(_blocker('SUPPRESSION_NOT_CLEAR', 'Suppression review is not clear', 'review'))
    if db.scalar(select(SuppressionEntry.id).where(
        SuppressionEntry.kind == 'phone', SuppressionEntry.value == phone,
    )):
        blockers.append(_blocker('SUPPRESSED', 'Phone number is suppressed'))

    published = db.scalar(select(CallScriptVersion).where(
        CallScriptVersion.campaign_id == lead.campaign_id,
        CallScriptVersion.status == 'published',
    ))
    if not published:
        blockers.append(_blocker('SCRIPT_NOT_PUBLISHED', 'Approved live script is missing'))
    elif not legacy_compatible or campaign:
        if published.version_number != 8 or (published.retell_agent_version or 0) != 8 or (published.retell_flow_version or 0) != 8:
            blockers.append(_blocker('BASELINE_DRIFT', 'Published voice baseline is not v8'))
        if published.retell_agent_id != settings.retell_agent_id:
            blockers.append(_blocker('AGENT_DRIFT', 'Published script is assigned to the wrong Retell agent'))
    if not campaign and not legacy_compatible:
        blockers.append(_blocker('CAMPAIGN_SETTINGS_MISSING', 'Calling campaign settings are missing'))
    elif campaign:
        if normalize_phone(campaign.from_number or '') != normalize_phone(settings.retell_from_number):
            blockers.append(_blocker('CALLER_ID_DRIFT', 'Configured caller ID does not match the approved number'))
        if campaign.baseline_version != 'v8':
            blockers.append(_blocker('BASELINE_DRIFT', 'Campaign voice baseline is not v8'))
        if require_campaign_running and campaign.campaign_status not in RUNNING_CAMPAIGN_STATUSES:
            blockers.append(_blocker('CAMPAIGN_NOT_RUNNING', f'Campaign is {campaign.campaign_status}'))

    compliance = db.scalars(select(CallComplianceItem).where(
        CallComplianceItem.campaign_id == lead.campaign_id,
        CallComplianceItem.mandatory == True,  # noqa: E712
    )).all()
    if not compliance:
        blockers.append(_blocker('COMPLIANCE_MISSING', 'Campaign compliance evidence is missing'))
    for item in compliance:
        if item.status != 'approved' or not item.approver or not item.evidence or not item.effective_at:
            blockers.append(_blocker('COMPLIANCE_INCOMPLETE', f'{item.label} is not ready'))
        elif item.expires_at and item.expires_at <= now:
            blockers.append(_blocker('COMPLIANCE_EXPIRED', f'{item.label} is expired'))

    queue_query = select(CallQueueItem).where(
        CallQueueItem.campaign_id == lead.campaign_id,
        CallQueueItem.canonical_lead_id == lead.id,
    )
    queue_items = db.scalars(queue_query).all()
    for item in queue_items:
        if item.id == exclude_queue_item_id:
            continue
        if item.status in ACTIVE_QUEUE_STATUSES:
            blockers.append(_blocker('ACTIVE_CALL_EXISTS', 'Lead already has an active or queued call'))
        if item.status in SUCCESS_QUEUE_STATUSES:
            blockers.append(_blocker('ALREADY_COMPLETED', 'Lead was already completed successfully'))
    active_attempt = db.scalar(select(CallAttempt.id).where(
        CallAttempt.campaign_id == lead.campaign_id,
        CallAttempt.consented_calling_lead_id == lead.id,
        CallAttempt.status.in_(['requested', 'initiated', 'ongoing', 'registered']),
    ))
    if active_attempt:
        blockers.append(_blocker('ACTIVE_CALL_EXISTS', 'Lead already has an active call'))

    in_window, next_window, local_label = calling_window(lead.timezone, campaign, now)
    if require_window and not in_window:
        blockers.append(_blocker('OUTSIDE_CALLING_WINDOW', 'Outside the recipient-local calling window', 'waiting'))

    unique = {item.code: item for item in blockers}
    blockers = list(unique.values())
    if not blockers:
        classification = 'ready'
    elif any(item.category == 'blocked' for item in blockers):
        classification = 'blocked'
    elif any(item.category == 'review' for item in blockers):
        classification = 'review'
    else:
        classification = 'waiting'
    return EligibilityResult(not blockers, classification, tuple(blockers), in_window, next_window, local_label)
