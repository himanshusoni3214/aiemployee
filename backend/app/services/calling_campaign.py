from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import (
    ActivityLog,
    CallAppointment,
    CallAttempt,
    CallCampaignSettings,
    CallComplianceItem,
    CallContactImportBatch,
    CallContactImportRow,
    CallDisposition,
    CallQueueItem,
    CallScriptVersion,
    CallTranscript,
    ConsentSourceProfile,
    ConsentedCallingLead,
    SuppressionEntry,
    User,
)
from app.services.calling import (
    ALLSTATE_CAMPAIGN_ID,
    ALLSTATE_COMPANY_ID,
    RetellCallingProvider,
    calling_provider,
    masked_phone,
    normalize_phone,
    sync_call_attempt_from_retell,
)
from app.services.calling_eligibility import (
    RUNNING_CAMPAIGN_STATUSES,
    calling_window,
    evaluate_calling_lead,
    is_canadian_e164,
)


START_CONFIRMATION = 'START APPROVED CALLING CAMPAIGN'
PRIMARY_COLUMNS = ['first_name', 'phone_number', 'consent_timestamp', 'consent_reference']
OPTIONAL_COLUMNS = [
    'last_name', 'product_interest', 'renewal_month', 'preferred_call_time',
    'timezone', 'notes',
]
TERMINAL_QUEUE_STATUSES = {
    'completed', 'appointment', 'no_answer', 'voicemail', 'not_interested',
    'dnc', 'blocked', 'provider_failed', 'cancelled',
}


def _now() -> datetime:
    return datetime.utcnow()


def _local_day_bounds(timezone_name: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    instant = (now or _now()).replace(tzinfo=timezone.utc)
    zone = ZoneInfo(timezone_name)
    local = instant.astimezone(zone)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def primary_csv_template() -> str:
    output = io.StringIO()
    csv.DictWriter(output, fieldnames=PRIMARY_COLUMNS + OPTIONAL_COLUMNS).writeheader()
    return output.getvalue()


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _profile_blockers(profile: ConsentSourceProfile, now: datetime) -> list[tuple[str, str, str]]:
    blockers = []
    if not profile.organization_authorized:
        blockers.append(('ORGANIZATION_NOT_AUTHORIZED', 'Source does not authorize the represented organization', 'blocked'))
    if not profile.automated_call_permission:
        blockers.append(('AUTOMATED_CALL_PERMISSION_MISSING', 'Source does not permit automated or synthesized calling', 'blocked'))
    if not profile.approved_consent_language or not profile.source_approval_evidence:
        blockers.append(('CONSENT_PROFILE_INCOMPLETE', 'Consent Source Profile evidence is incomplete', 'review'))
    if profile.approval_date > now:
        blockers.append(('CONSENT_PROFILE_NOT_EFFECTIVE', 'Consent Source Profile is not effective yet', 'review'))
    if profile.expires_at and profile.expires_at <= now:
        blockers.append(('CONSENT_PROFILE_EXPIRED', 'Consent Source Profile is expired', 'blocked'))
    return blockers


def upload_contacts(
    db: Session,
    *,
    profile: ConsentSourceProfile,
    content: str,
    filename: str,
    user: User,
) -> CallContactImportBatch:
    rows = [dict(item) for item in csv.DictReader(io.StringIO(content))]
    if not rows:
        raise ValueError('CSV contains no contact rows')
    missing_headers = [column for column in PRIMARY_COLUMNS if column not in (rows[0] or {})]
    if missing_headers:
        raise ValueError(f'Missing required columns: {", ".join(missing_headers)}')
    now = _now()
    profile_reasons = _profile_blockers(profile, now)
    batch = CallContactImportBatch(
        company_id=ALLSTATE_COMPANY_ID,
        campaign_id=ALLSTATE_CAMPAIGN_ID,
        source_profile_id=profile.id,
        filename=filename[:255] or 'contacts.csv',
        status='reviewed',
        created_by=user.id,
        created_at=now,
    )
    db.add(batch)
    db.flush()
    seen: set[str] = set()
    reason_counts: Counter[str] = Counter()
    counts = Counter()
    for row_number, raw in enumerate(rows, start=2):
        first_name = str(raw.get('first_name') or '').strip()
        raw_phone = str(raw.get('phone_number') or '').strip()
        phone = normalize_phone(raw_phone)
        consent_reference = str(raw.get('consent_reference') or '').strip()
        consent_timestamp = _parse_timestamp(raw.get('consent_timestamp'))
        raw_is_test = str(raw.get('is_test') or '').strip().lower() in {'1', 'true', 'yes'}
        reasons: list[tuple[str, str, str]] = list(profile_reasons)
        if not first_name:
            reasons.append(('FIRST_NAME_MISSING', 'First name is missing', 'review'))
        if not raw_phone or not is_canadian_e164(phone):
            reasons.append(('PHONE_INVALID', 'Phone number is invalid or not Canadian', 'blocked'))
        if not consent_reference:
            reasons.append(('CONSENT_REFERENCE_MISSING', 'Consent reference is missing', 'review'))
        if not consent_timestamp:
            reasons.append(('CONSENT_TIMESTAMP_MISSING', 'Consent timestamp is missing or invalid', 'review'))
        if phone in seen:
            reasons.append(('DUPLICATE_IN_UPLOAD', 'Duplicate phone number in this upload', 'blocked'))
        existing = db.scalar(select(ConsentedCallingLead).where(
            ConsentedCallingLead.campaign_id == ALLSTATE_CAMPAIGN_ID,
            ConsentedCallingLead.phone_number == phone,
        )) if is_canadian_e164(phone) else None
        if existing:
            reasons.append(('DUPLICATE_EXISTING', 'Phone number already exists in this campaign', 'blocked'))
        if is_canadian_e164(phone):
            seen.add(phone)
        suppressed = db.scalar(select(SuppressionEntry.id).where(
            SuppressionEntry.kind == 'phone', SuppressionEntry.value == phone,
        )) if is_canadian_e164(phone) else None
        if suppressed:
            reasons.append(('DNC', 'Phone number is on the suppression list', 'blocked'))
        unique_reasons = {code: (code, message, category) for code, message, category in reasons}
        reasons = list(unique_reasons.values())
        classification = 'ready'
        if any(category == 'blocked' for _, _, category in reasons):
            classification = 'blocked'
        elif reasons:
            classification = 'review'

        lead = None
        if classification == 'ready':
            lead = ConsentedCallingLead(
                company_id=ALLSTATE_COMPANY_ID,
                campaign_id=ALLSTATE_CAMPAIGN_ID,
                source_profile_id=profile.id,
                first_name=first_name,
                last_name=str(raw.get('last_name') or '').strip() or None,
                phone_number=phone,
                timezone=str(raw.get('timezone') or profile.default_timezone or 'America/Toronto').strip(),
                province=profile.default_province or 'Ontario',
                product_interest=str(raw.get('product_interest') or 'Auto and property insurance').strip(),
                consent_status='verified',
                consent_type='express_automated_call',
                consent_source=profile.name,
                consent_text=profile.approved_consent_language,
                consent_timestamp=consent_timestamp,
                consented_number=phone,
                automated_or_synthesized_call_consent=True,
                organization_authorized=True,
                consent_proof=f'{profile.consent_proof_method}: {consent_reference}',
                consent_reference=consent_reference,
                consent_withdrawn=False,
                renewal_month=str(raw.get('renewal_month') or '').strip() or None,
                preferred_call_time=str(raw.get('preferred_call_time') or '').strip() or None,
                notes=str(raw.get('notes') or '').strip() or None,
                dncl_status='clear',
                internal_dnc_clear=True,
                suppression_clear=True,
                eligibility_status='Ready for AI call',
                eligibility_reasons=[],
                created_by=user.id,
                created_at=now,
                updated_at=now,
                is_test=raw_is_test,
            )
            db.add(lead)
            db.flush()
            evaluated = evaluate_calling_lead(db, lead, now=now)
            if not evaluated.ready:
                classification = evaluated.classification
                reasons = [(item.code, item.message, item.category) for item in evaluated.blockers]
                db.delete(lead)
                db.flush()
                lead = None
        counts[classification] += 1
        for code, _, _ in reasons:
            reason_counts[code] += 1
        normalized = {
            'first_name': first_name,
            'last_name': str(raw.get('last_name') or '').strip() or None,
            'phone_number': phone if is_canadian_e164(phone) else None,
            'consent_timestamp': consent_timestamp.isoformat() if consent_timestamp else None,
            'consent_reference': consent_reference or None,
            'product_interest': str(raw.get('product_interest') or 'Auto and property insurance').strip(),
            'renewal_month': str(raw.get('renewal_month') or '').strip() or None,
            'preferred_call_time': str(raw.get('preferred_call_time') or '').strip() or None,
            'timezone': str(raw.get('timezone') or profile.default_timezone or 'America/Toronto').strip(),
            'notes': str(raw.get('notes') or '').strip() or None,
        }
        db.add(CallContactImportRow(
            batch_id=batch.id,
            company_id=ALLSTATE_COMPANY_ID,
            campaign_id=ALLSTATE_CAMPAIGN_ID,
            row_number=row_number,
            first_name=first_name or None,
            phone_number=phone if is_canadian_e164(phone) else None,
            classification=classification,
            blocker_codes=[code for code, _, _ in reasons],
            blocker_messages=[message for _, message, _ in reasons],
            normalized=normalized,
            canonical_lead_id=lead.id if lead else None,
            is_test=raw_is_test,
            created_at=now,
        ))
    batch.uploaded_count = len(rows)
    batch.ready_count = counts['ready']
    batch.review_count = counts['review']
    batch.blocked_count = counts['blocked']
    batch.reason_counts = dict(reason_counts)
    return batch


def import_batch_payload(db: Session, batch: CallContactImportBatch | None) -> dict | None:
    if not batch:
        return None
    rows = db.scalars(select(CallContactImportRow).where(
        CallContactImportRow.batch_id == batch.id,
    ).order_by(CallContactImportRow.row_number)).all()
    return {
        'id': batch.id,
        'filename': batch.filename,
        'status': batch.status,
        'uploaded': batch.uploaded_count,
        'ready': batch.ready_count,
        'needs_review': batch.review_count,
        'blocked': batch.blocked_count,
        'reason_counts': batch.reason_counts or {},
        'created_at': batch.created_at,
        'rows': [{
            'id': item.id,
            'row_number': item.row_number,
            'first_name': item.first_name,
            'phone_number_masked': masked_phone(item.phone_number or ''),
            'classification': item.classification,
            'blocker_codes': item.blocker_codes or [],
            'blocker_messages': item.blocker_messages or [],
        } for item in rows[:200]],
    }


def blocked_rows_csv(db: Session, batch_id: str) -> str:
    rows = db.scalars(select(CallContactImportRow).where(
        CallContactImportRow.batch_id == batch_id,
        CallContactImportRow.classification.in_(['blocked', 'review']),
    ).order_by(CallContactImportRow.row_number)).all()
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=PRIMARY_COLUMNS + OPTIONAL_COLUMNS + ['classification', 'reasons'])
    writer.writeheader()
    for item in rows:
        normalized = item.normalized or {}
        writer.writerow({
            **{key: normalized.get(key) for key in PRIMARY_COLUMNS + OPTIONAL_COLUMNS},
            'classification': item.classification,
            'reasons': '; '.join(item.blocker_messages or []),
        })
    return output.getvalue()


def _published_script(db: Session) -> CallScriptVersion | None:
    return db.scalar(select(CallScriptVersion).where(
        CallScriptVersion.campaign_id == ALLSTATE_CAMPAIGN_ID,
        CallScriptVersion.status == 'published',
    ))


def queue_eligible_contacts(db: Session, *, execution_mode: str = 'live', scheduled_after: datetime | None = None) -> tuple[int, int]:
    script = _published_script(db)
    if not script:
        return 0, 0
    leads = db.scalars(select(ConsentedCallingLead).where(
        ConsentedCallingLead.campaign_id == ALLSTATE_CAMPAIGN_ID,
        ConsentedCallingLead.is_test == (execution_mode == 'mock'),
    )).all()
    created = 0
    blocked = 0
    for lead in leads:
        result = evaluate_calling_lead(db, lead)
        if not result.ready:
            blocked += 1
            continue
        dedupe_key = f'{lead.campaign_id}:{lead.id}:initial'
        existing = db.scalar(select(CallQueueItem).where(CallQueueItem.dedupe_key == dedupe_key))
        if existing:
            continue
        db.add(CallQueueItem(
            company_id=lead.company_id,
            campaign_id=lead.campaign_id,
            canonical_lead_id=lead.id,
            phone_number=lead.phone_number,
            dedupe_key=dedupe_key,
            script_version_id=script.id,
            script_version=script.version_number,
            provider_agent_id=script.retell_agent_id,
            provider_agent_version=script.retell_agent_version,
            consent_snapshot={
                'source_profile_id': lead.source_profile_id,
                'consent_reference': lead.consent_reference,
                'consent_timestamp': lead.consent_timestamp.isoformat(),
                'consented_number': lead.consented_number,
                'automated_call_permission': lead.automated_or_synthesized_call_consent,
                'organization_authorized': lead.organization_authorized,
            },
            status='queued',
            priority=100,
            scheduled_after=scheduled_after,
            attempts=0,
            execution_mode=execution_mode,
            created_at=_now(),
            updated_at=_now(),
        ))
        created += 1
    return created, blocked


def campaign_readiness(db: Session, health: dict) -> dict:
    row = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == ALLSTATE_CAMPAIGN_ID))
    script = _published_script(db)
    profiles = db.scalars(select(ConsentSourceProfile).where(
        ConsentSourceProfile.campaign_id == ALLSTATE_CAMPAIGN_ID,
    ).order_by(ConsentSourceProfile.created_at.desc())).all()
    profile_ready = any(not _profile_blockers(profile, _now()) for profile in profiles)
    leads = db.scalars(select(ConsentedCallingLead).where(
        ConsentedCallingLead.campaign_id == ALLSTATE_CAMPAIGN_ID,
        ConsentedCallingLead.is_test == False,  # noqa: E712
    )).all()
    ready = sum(evaluate_calling_lead(db, lead).ready for lead in leads)
    compliance = int(db.scalar(select(func.count(CallComplianceItem.id)).where(
        CallComplianceItem.campaign_id == ALLSTATE_CAMPAIGN_ID,
        CallComplianceItem.mandatory == True,  # noqa: E712
        CallComplianceItem.status == 'approved',
    )) or 0)
    timezone_name = row.timezone if row else 'America/Toronto'
    in_window, next_window, _ = calling_window(timezone_name, row)
    checks = [
        ('provider', 'Provider', bool(health.get('api_authenticated') and health.get('agent_exists'))),
        ('caller_id', 'Caller ID', bool(health.get('outbound_agent_correctly_assigned'))),
        ('script', 'Script', bool(script and script.version_number == 8 and script.retell_agent_version == 8 and script.retell_flow_version == 8 and (health.get('response_engine') or {}).get('type') == 'conversation-flow' and int((health.get('response_engine') or {}).get('version') or 0) == 8)),
        ('compliance', 'Compliance', compliance >= 19),
        ('consent_source', 'Consent source', profile_ready),
        ('contacts', 'Contacts uploaded', bool(leads)),
        ('eligible', 'Eligible contacts', ready > 0),
        ('dnc', 'DNC protection', True),
        ('appointments', 'Appointments', bool(health.get('tool_token_configured'))),
        ('callbacks', 'Callbacks', bool(health.get('webhook_signature_key_configured'))),
    ]
    blockers = [{'code': code, 'label': label} for code, label, passed in checks if not passed]
    return {
        'ready': not blockers,
        'checks': [{'code': code, 'label': label, 'ready': passed} for code, label, passed in checks],
        'blockers': blockers,
        'eligible_contacts': ready,
        'calling_now': in_window,
        'next_calling_window': next_window,
        'daily_limit': row.daily_call_limit if row else 20,
        'concurrency': row.concurrent_call_limit if row else 1,
        'confirmation_required': START_CONFIRMATION,
    }


def dry_run_contacts(db: Session, health: dict) -> dict:
    readiness = campaign_readiness(db, health)
    total = int(db.scalar(select(func.count(ConsentedCallingLead.id)).where(
        ConsentedCallingLead.campaign_id == ALLSTATE_CAMPAIGN_ID,
        ConsentedCallingLead.is_test == False,  # noqa: E712
    )) or 0)
    would_call = readiness['eligible_contacts']
    daily = min(would_call, readiness['daily_limit'])
    return {
        'uploaded': total,
        'would_call': would_call,
        'would_block': max(0, total - would_call),
        'would_call_today': daily,
        'first_call': _now() if readiness['calling_now'] else readiness['next_calling_window'],
        'estimated_cost_usd': {'low': round(daily * 0.15, 2), 'high': round(daily * 1.0, 2)},
        'ready': readiness['ready'],
        'blockers': readiness['blockers'],
    }


def start_campaign(db: Session, user: User, health: dict, confirmation: str, *, execution_mode: str = 'live', defer_mock_seconds: int = 0) -> dict:
    if confirmation != START_CONFIRMATION:
        raise ValueError(f'Confirmation must exactly match {START_CONFIRMATION}')
    if execution_mode not in {'live', 'mock'}:
        raise ValueError('Unsupported execution mode')
    readiness = campaign_readiness(db, health)
    if execution_mode == 'live' and not readiness['ready']:
        raise ValueError('; '.join(item['label'] for item in readiness['blockers']) or 'Campaign is not ready')
    row = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == ALLSTATE_CAMPAIGN_ID))
    if execution_mode != 'mock' and defer_mock_seconds:
        raise ValueError('Deferred execution is available only for mock QA')
    defer_mock_seconds = max(0, min(int(defer_mock_seconds or 0), 7200))
    scheduled_after = _now() + timedelta(seconds=defer_mock_seconds) if execution_mode == 'mock' and defer_mock_seconds else None
    created, blocked = queue_eligible_contacts(db, execution_mode=execution_mode, scheduled_after=scheduled_after)
    if created == 0 and not db.scalar(select(CallQueueItem.id).where(
        CallQueueItem.campaign_id == ALLSTATE_CAMPAIGN_ID,
        CallQueueItem.status == 'queued',
        CallQueueItem.execution_mode == execution_mode,
    )):
        raise ValueError('No eligible contacts are available to call')
    now = _now()
    row.campaign_status = 'running' if readiness['calling_now'] or execution_mode == 'mock' else 'waiting_for_window'
    row.prospect_calling_enabled = execution_mode == 'live'
    row.automated_queue_enabled = True
    row.started_at = row.started_at or now
    row.status_changed_at = now
    row.updated_at = now
    db.add(ActivityLog(company_id=ALLSTATE_COMPANY_ID, user_id=user.id, action='Allstate Calling Campaign Started', entity_type='CallCampaignSettings', entity_id=row.id, metadata_json={'queued': created, 'blocked': blocked, 'execution_mode': execution_mode}))
    return {'ok': True, 'status': row.campaign_status, 'queued': created, 'blocked': blocked}


def control_campaign(db: Session, user: User, action: str) -> dict:
    if action not in {'pause', 'resume', 'stop'}:
        raise ValueError('Unknown campaign action')
    row = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == ALLSTATE_CAMPAIGN_ID))
    now = _now()
    if action == 'pause':
        row.campaign_status = 'paused'
        row.paused_at = now
        row.automated_queue_enabled = False
    elif action == 'stop':
        row.campaign_status = 'stopped'
        row.stopped_at = now
        row.prospect_calling_enabled = False
        row.automated_queue_enabled = False
        db.execute(update(CallQueueItem).where(
            CallQueueItem.campaign_id == ALLSTATE_CAMPAIGN_ID,
            CallQueueItem.status == 'queued',
        ).values(status='cancelled', outcome='stopped', completed_at=now, updated_at=now))
    else:
        pending = db.scalar(select(CallQueueItem.id).where(
            CallQueueItem.campaign_id == ALLSTATE_CAMPAIGN_ID,
            CallQueueItem.status == 'queued',
        ))
        if not pending:
            raise ValueError('No queued contacts remain')
        row.campaign_status = 'running'
        row.automated_queue_enabled = True
    row.status_changed_at = now
    row.updated_at = now
    db.add(ActivityLog(company_id=ALLSTATE_COMPANY_ID, user_id=user.id, action=f'Allstate Calling Campaign {action.title()}', entity_type='CallCampaignSettings', entity_id=row.id, metadata_json={}))
    return {'ok': True, 'status': row.campaign_status}


def update_campaign_limits(db: Session, payload: dict) -> CallCampaignSettings:
    row = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == ALLSTATE_CAMPAIGN_ID))
    if 'daily_call_limit' in payload:
        value = int(payload['daily_call_limit'])
        if not 1 <= value <= 500:
            raise ValueError('Daily call limit must be between 1 and 500')
        row.daily_call_limit = value
    if 'concurrent_call_limit' in payload:
        value = int(payload['concurrent_call_limit'])
        if value not in {1, 2, 3, 5}:
            raise ValueError('Concurrency must be 1, 2, 3 or 5')
        row.concurrent_call_limit = value
    row.updated_at = _now()
    return row


def queue_summary(db: Session) -> dict:
    row = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == ALLSTATE_CAMPAIGN_ID))
    items = db.scalars(select(CallQueueItem).where(
        CallQueueItem.campaign_id == ALLSTATE_CAMPAIGN_ID,
    ).order_by(CallQueueItem.created_at.desc()).limit(500)).all()
    counts = Counter(item.status for item in items)
    callbacks = [item for item in items if item.callback_at and item.status == 'queued']
    attempts = db.scalars(select(CallAttempt).where(
        CallAttempt.campaign_id == ALLSTATE_CAMPAIGN_ID,
    ).order_by(CallAttempt.created_at.desc()).limit(200)).all()
    day_start, day_end = _local_day_bounds(row.timezone if row else 'America/Toronto')
    today_attempts = [item for item in attempts if day_start <= item.requested_at < day_end]
    today_queue = [item for item in items if item.completed_at and day_start <= item.completed_at < day_end]
    total_cost = sum(float(item.provider_cost_cents or 0) for item in today_attempts) / 100
    return {
        'status': row.campaign_status,
        'progress': {
            'completed': sum(counts[key] for key in TERMINAL_QUEUE_STATUSES),
            'total': len(items),
            'queued': counts['queued'],
            'calling': counts['calling'] + counts['answered'],
        },
        'today': {
            'attempts': len(today_attempts),
            'answered': sum(item.answered_at is not None for item in today_attempts),
            'appointments': sum(item.status == 'appointment' for item in today_queue),
            'callbacks': len(callbacks),
            'dnc': sum(item.status == 'dnc' for item in today_queue),
            'no_answer': sum(item.status == 'no_answer' for item in today_queue),
        },
        'cost_today': round(total_cost, 2),
        'average_cost': round(total_cost / len(today_attempts), 2) if today_attempts else 0,
        'callbacks': [{
            'id': item.id,
            'lead_id': item.canonical_lead_id,
            'callback_at': item.callback_at,
            'timezone': item.callback_timezone,
            'reason': item.callback_reason,
            'status': item.status,
        } for item in callbacks],
        'queue_items': [queue_item_payload(db, item) for item in items[:100]],
    }


def queue_item_payload(db: Session, item: CallQueueItem) -> dict:
    lead = db.get(ConsentedCallingLead, item.canonical_lead_id)
    attempt = db.get(CallAttempt, item.call_attempt_id) if item.call_attempt_id else None
    disposition = db.scalar(select(CallDisposition).where(CallDisposition.call_attempt_id == attempt.id)) if attempt else None
    transcript = db.scalar(select(CallTranscript).where(CallTranscript.call_attempt_id == attempt.id)) if attempt else None
    appointments = db.scalars(select(CallAppointment).where(CallAppointment.call_attempt_id == attempt.id)).all() if attempt else []
    return {
        'id': item.id,
        'lead_id': item.canonical_lead_id,
        'name': ' '.join(filter(None, [lead.first_name, lead.last_name])) if lead else '-',
        'phone_number_masked': masked_phone(item.phone_number),
        'status': item.status,
        'outcome': item.outcome,
        'scheduled_after': item.scheduled_after,
        'attempts': item.attempts,
        'provider_call_id': item.provider_call_id,
        'failure_code': item.failure_code,
        'error_message': item.error_message,
        'callback_at': item.callback_at,
        'callback_timezone': item.callback_timezone,
        'callback_reason': item.callback_reason,
        'created_at': item.created_at,
        'started_at': item.started_at,
        'completed_at': item.completed_at,
        'duration_seconds': attempt.duration_seconds if attempt else None,
        'cost_usd': float(attempt.provider_cost_cents or 0) / 100 if attempt else None,
        'summary': transcript.summary if transcript else None,
        'transcript': transcript.transcript if transcript else None,
        'recording_url': transcript.recording_url if transcript else None,
        'sales_score': transcript.sales_score if transcript else None,
        'objections': transcript.objections if transcript else [],
        'disposition': disposition.disposition if disposition else None,
        'renewal_month': lead.renewal_month if lead else None,
        'appointment': bool(appointments),
        'advanced': {
            'provider_call_id': item.provider_call_id,
            'agent_id': item.provider_agent_id,
            'agent_version': item.provider_agent_version,
            'script_version': item.script_version,
        },
    }


def _dynamic_variables(lead: ConsentedCallingLead, attempt: CallAttempt, script: CallScriptVersion) -> dict[str, str]:
    return {
        'customer_name': lead.first_name,
        'assistant_name': 'Ava',
        'agent_name': 'Himanshu Soni',
        'agent_role': 'Allstate Sales Agent',
        'company_name': 'Allstate',
        'agency_location': 'Scarborough, Ontario',
        'campaign_name': 'Allstate Quote Appointment Calling',
        'call_purpose': script.purpose_statement,
        'insurance_interest': lead.product_interest,
        'product_interest': lead.product_interest,
        'consent_source': lead.consent_source,
        'consent_date': lead.consent_timestamp.date().isoformat(),
        'booking_timezone': lead.timezone,
        'internal_test': 'false',
        'recording_disclosure_enabled': 'true',
        'recording_disclosure': 'This call may be recorded and transcribed for quality and appointment notes.',
        'consent_validated_for_called_number': 'true',
        'renewal_month': lead.renewal_month or '',
        'slot_one': '', 'slot_two': '', 'callback_date': '', 'callback_time': '',
        'voryx_call_attempt_id': attempt.id,
    }


async def process_next_queue_item(db: Session, provider: RetellCallingProvider | None = None) -> bool:
    now = _now()
    item = db.scalar(select(CallQueueItem).where(
        CallQueueItem.status == 'queued',
        (CallQueueItem.scheduled_after == None) | (CallQueueItem.scheduled_after <= now),  # noqa: E711
    ).order_by(CallQueueItem.priority, CallQueueItem.created_at).with_for_update(skip_locked=True).limit(1))
    if not item:
        return False
    campaign = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == item.campaign_id))
    if not campaign or campaign.campaign_status not in RUNNING_CAMPAIGN_STATUSES or not campaign.automated_queue_enabled:
        return False
    lead = db.get(ConsentedCallingLead, item.canonical_lead_id)
    script = db.get(CallScriptVersion, item.script_version_id)
    if not lead or not script:
        item.status = 'blocked'
        item.failure_code = 'SOURCE_RECORD_MISSING'
        item.error_message = 'Lead or script snapshot is missing'
        item.completed_at = now
        item.updated_at = now
        return True
    eligibility = evaluate_calling_lead(
        db, lead, now=now, require_window=item.execution_mode == 'live',
        require_campaign_running=True, exclude_queue_item_id=item.id,
    )
    waiting = next((blocker for blocker in eligibility.blockers if blocker.code == 'OUTSIDE_CALLING_WINDOW'), None)
    if waiting:
        item.scheduled_after = eligibility.next_window_at
        item.updated_at = now
        campaign.campaign_status = 'waiting_for_window'
        campaign.status_changed_at = now
        return True
    if not eligibility.ready:
        item.status = 'blocked'
        item.failure_code = eligibility.blockers[0].code
        item.error_message = '; '.join(blocker.message for blocker in eligibility.blockers)
        item.completed_at = now
        item.updated_at = now
        return True
    day_start, next_day_start = _local_day_bounds(lead.timezone, now)
    today_count = int(db.scalar(select(func.count(CallAttempt.id)).where(
        CallAttempt.campaign_id == item.campaign_id,
        CallAttempt.requested_at >= day_start,
        CallAttempt.internal_test == False,  # noqa: E712
    )) or 0)
    if today_count >= campaign.daily_call_limit:
        item.scheduled_after = next_day_start
        item.updated_at = now
        return True
    active_count = int(db.scalar(select(func.count(CallQueueItem.id)).where(
        CallQueueItem.campaign_id == item.campaign_id,
        CallQueueItem.status.in_(['calling', 'answered']),
    )) or 0)
    if active_count >= campaign.concurrent_call_limit:
        return False
    attempt = CallAttempt(
        company_id=item.company_id,
        campaign_id=item.campaign_id,
        consented_calling_lead_id=lead.id,
        script_version_id=script.id,
        provider='mock' if item.execution_mode == 'mock' else 'retell',
        provider_agent_id=item.provider_agent_id,
        provider_agent_version=item.provider_agent_version,
        from_number=normalize_phone(settings.retell_from_number),
        to_number=lead.phone_number,
        mode='bulk_mock' if item.execution_mode == 'mock' else 'consented_campaign',
        status='requested',
        requested_at=now,
        internal_test=item.execution_mode == 'mock',
        metadata_json={'queue_item_id': item.id, 'automatic_retry': False, 'baseline': 'v8'},
        created_at=now,
        updated_at=now,
    )
    db.add(attempt)
    db.flush()
    item.call_attempt_id = attempt.id
    item.started_at = now
    item.attempts += 1
    item.updated_at = now
    if item.execution_mode == 'mock':
        attempt.status = 'ended'
        attempt.ended_at = now
        item.status = 'no_answer'
        item.outcome = 'no_answer'
        item.completed_at = now
        return True
    provider = provider or calling_provider()
    campaign.campaign_status = 'running'
    campaign.status_changed_at = now
    try:
        receipt = await provider.place_call(
            to_number=lead.phone_number,
            call_attempt_id=attempt.id,
            dynamic_variables=_dynamic_variables(lead, attempt, script),
            agent_version=item.provider_agent_version,
            mode='consented_campaign',
        )
    except Exception as exc:
        attempt.status = 'provider_failed'
        attempt.termination_reason = str(exc)[:1000]
        attempt.ended_at = now
        item.status = 'provider_failed'
        item.failure_code = 'PROVIDER_REJECTED'
        item.error_message = str(exc)[:1000]
        item.completed_at = now
        return True
    item.provider_call_id = str(receipt.get('call_id') or receipt.get('provider_call_id') or '') or None
    item.status = 'calling'
    attempt.provider_call_id = item.provider_call_id
    attempt.provider_receipt = {'call_id': item.provider_call_id, 'agent_id': item.provider_agent_id}
    attempt.status = 'initiated'
    return True


async def reconcile_active_calls(db: Session, provider: RetellCallingProvider | None = None) -> int:
    provider = provider or calling_provider()
    items = db.scalars(select(CallQueueItem).where(
        CallQueueItem.status.in_(['calling', 'answered']),
        CallQueueItem.call_attempt_id.is_not(None),
    )).all()
    reconciled = 0
    for item in items:
        attempt = db.get(CallAttempt, item.call_attempt_id)
        if not attempt or not attempt.provider_call_id:
            continue
        try:
            await sync_call_attempt_from_retell(db, attempt, provider)
            reconcile_queue_from_attempt(db, attempt)
            item.error_message = None
            reconciled += 1
        except Exception as exc:
            item.error_message = f'Provider temporarily unavailable: {str(exc)[:500]}'
            item.updated_at = _now()
    return reconciled


def reconcile_queue_from_attempt(db: Session, attempt: CallAttempt) -> None:
    item = db.scalar(select(CallQueueItem).where(CallQueueItem.call_attempt_id == attempt.id))
    if not item:
        return
    now = _now()
    disposition = db.scalar(select(CallDisposition).where(CallDisposition.call_attempt_id == attempt.id))
    transcript = db.scalar(select(CallTranscript).where(CallTranscript.call_attempt_id == attempt.id))
    appointments = db.scalars(select(CallAppointment).where(CallAppointment.call_attempt_id == attempt.id)).all()
    if disposition and disposition.do_not_call_requested:
        item.status = 'dnc'
        item.outcome = 'dnc'
        phone = normalize_phone(item.phone_number)
        existing = db.scalar(select(SuppressionEntry).where(
            SuppressionEntry.company_id == item.company_id,
            SuppressionEntry.kind == 'phone',
            SuppressionEntry.value == phone,
        ))
        if not existing:
            db.add(SuppressionEntry(company_id=item.company_id, kind='phone', value=phone, reason='Retell call disposition requested DNC', source='retell_webhook'))
        suppress_queued_phone(db, phone, 'Caller requested do not call')
    elif appointments or (disposition and disposition.appointment_booked):
        item.status = 'appointment'
        item.outcome = 'appointment'
    elif disposition and disposition.callback_requested:
        item.status = 'callback'
        item.outcome = 'callback'
        extracted = transcript.extracted_fields if transcript else {}
        callback_date = str(extracted.get('callback_date') or '').strip()
        callback_time = str(extracted.get('callback_time') or '').strip()
        callback_timezone = str(extracted.get('callback_timezone') or extracted.get('timezone') or 'America/Toronto').strip()
        callback_at = None
        if callback_date:
            try:
                local = datetime.fromisoformat(' '.join(part for part in (callback_date, callback_time or '10:00') if part))
                callback_at = local.replace(tzinfo=ZoneInfo(callback_timezone)).astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                callback_at = None
        callback_consent = bool(extracted.get('callback_consent') or extracted.get('consent_to_reconnect'))
        item.callback_at = callback_at
        item.callback_timezone = callback_timezone
        item.callback_reason = str(extracted.get('callback_reason') or ('Renewal follow-up' if extracted.get('renewal_month') else 'Requested callback'))
        item.callback_consent = callback_consent
        if callback_at and callback_consent:
            callback_key = f'{item.campaign_id}:{item.canonical_lead_id}:callback:{callback_at.isoformat()}'
            if not db.scalar(select(CallQueueItem.id).where(CallQueueItem.dedupe_key == callback_key)):
                db.add(CallQueueItem(
                    company_id=item.company_id,
                    campaign_id=item.campaign_id,
                    canonical_lead_id=item.canonical_lead_id,
                    phone_number=item.phone_number,
                    dedupe_key=callback_key,
                    script_version_id=item.script_version_id,
                    script_version=item.script_version,
                    provider_agent_id=item.provider_agent_id,
                    provider_agent_version=item.provider_agent_version,
                    consent_snapshot=item.consent_snapshot,
                    status='queued',
                    priority=50,
                    scheduled_after=callback_at,
                    callback_at=callback_at,
                    callback_timezone=callback_timezone,
                    callback_reason=item.callback_reason,
                    callback_consent=True,
                    execution_mode=item.execution_mode,
                    created_at=now,
                    updated_at=now,
                ))
    elif attempt.status in {'ended', 'analyzed'}:
        outcome = str(disposition.disposition if disposition else attempt.termination_reason or 'completed').lower().replace(' ', '_')
        item.outcome = outcome
        item.status = outcome if outcome in TERMINAL_QUEUE_STATUSES else 'completed'
    elif attempt.status in {'ongoing', 'registered', 'initiated'}:
        item.status = 'answered' if attempt.answered_at else 'calling'
    item.provider_call_id = attempt.provider_call_id
    item.updated_at = now
    if item.status in TERMINAL_QUEUE_STATUSES or item.status == 'callback':
        item.completed_at = now
    active = db.scalar(select(CallQueueItem.id).where(
        CallQueueItem.campaign_id == item.campaign_id,
        CallQueueItem.status.in_(['queued', 'calling', 'answered']),
    ))
    if not active:
        campaign = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == item.campaign_id))
        if campaign and campaign.campaign_status in RUNNING_CAMPAIGN_STATUSES:
            campaign.campaign_status = 'completed'
            campaign.completed_at = now
            campaign.status_changed_at = now
            campaign.prospect_calling_enabled = False
            campaign.automated_queue_enabled = False


def suppress_queued_phone(db: Session, phone: str, reason: str) -> int:
    now = _now()
    rows = db.scalars(select(CallQueueItem).where(
        CallQueueItem.phone_number == normalize_phone(phone),
        CallQueueItem.status.in_(['eligible', 'queued']),
    )).all()
    for item in rows:
        item.status = 'dnc'
        item.outcome = 'dnc'
        item.failure_code = 'DNC'
        item.error_message = reason
        item.completed_at = now
        item.updated_at = now
    return len(rows)


def complete_appointment_queue(db: Session, attempt_id: str) -> None:
    item = db.scalar(select(CallQueueItem).where(CallQueueItem.call_attempt_id == attempt_id))
    if item:
        item.status = 'appointment'
        item.outcome = 'appointment'
        item.completed_at = _now()
        item.updated_at = _now()


def retry_failed_item(db: Session, item: CallQueueItem) -> None:
    if item.status != 'provider_failed':
        raise ValueError('Only provider-failed calls can be retried')
    item.status = 'queued'
    item.failure_code = None
    item.error_message = None
    item.scheduled_after = None
    item.completed_at = None
    item.updated_at = _now()
