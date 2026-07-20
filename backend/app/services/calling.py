import base64
import hashlib
import hmac
import json
import re
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import (
    ActivityLog,
    CallAppointment,
    CallAttempt,
    CallCampaignSettings,
    CallDisposition,
    CallTranscript,
    Campaign,
    Company,
    LeadPhoneConsent,
    RetellWebhookEvent,
    SuppressionEntry,
    User,
)

ALLSTATE_COMPANY_ID = 'company-allstate-himanshu'
ALLSTATE_CAMPAIGN_ID = 'campaign-allstate-quote-calling'
ALLSTATE_AGENT_NAME = 'Voryx Allstate Quote Appointment Assistant'
RETELL_BASE_URL = 'https://api.retellai.com'
CALL_ACTIVE_STATUSES = {'requested', 'queued', 'initiated', 'registered', 'ringing', 'ongoing', 'in_progress', 'started'}
INTERNAL_CONFIRMATION = 'PLACE INTERNAL TEST CALL'
US_CA_E164_RE = re.compile(r'^\+1[2-9]\d{9}$')
TERMINAL_STATUS_MAP = {
    'call_started': 'started',
    'call_ended': 'ended',
    'call_analyzed': 'analyzed',
    'transcript_updated': 'in_progress',
}


class CallingProviderError(Exception):
    pass


def normalize_phone(value: str | None) -> str:
    if not value:
        return ''
    text = str(value).strip()
    if text.startswith('+'):
        return '+' + ''.join(ch for ch in text[1:] if ch.isdigit())
    digits = ''.join(ch for ch in text if ch.isdigit())
    if len(digits) == 10:
        return '+1' + digits
    if len(digits) == 11 and digits.startswith('1'):
        return '+' + digits
    return text


def valid_us_ca_e164(value: str | None) -> bool:
    return bool(US_CA_E164_RE.match(normalize_phone(value)))


def masked_phone(value: str | None) -> str:
    phone = normalize_phone(value)
    if len(phone) < 5:
        return '***'
    return f'***{phone[-4:]}'


def _now() -> datetime:
    return datetime.utcnow()


def _redact_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted = {}
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(word in lowered for word in ('key', 'token', 'secret', 'authorization', 'password')):
                redacted[key] = '[redacted]'
            elif lowered in {'to_number', 'from_number', 'phone_number'}:
                redacted[key] = masked_phone(str(value))
            else:
                redacted[key] = _redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [_redact_payload(item) for item in payload]
    return payload


def _event_hash(raw_body: bytes, event_type: str, call_id: str | None) -> str:
    digest = hashlib.sha256(raw_body).hexdigest()
    return hashlib.sha256(f'{event_type}:{call_id or ""}:{digest}'.encode()).hexdigest()


class RetellCallingProvider:
    def __init__(self, api_key: str | None = None, webhook_key: str | None = None):
        self.api_key = api_key if api_key is not None else settings.retell_api_key
        self.webhook_key = webhook_key if webhook_key is not None else (settings.retell_webhook_api_key or settings.retell_api_key)

    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise CallingProviderError('RETELL_API_KEY is not configured')
        return {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.request(method, f'{RETELL_BASE_URL}{path}', headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise CallingProviderError(f'Retell request failed: {exc}') from exc
        if response.status_code >= 400:
            detail = response.text[:300]
            if response.status_code in {401, 403}:
                raise CallingProviderError('Retell API authentication failed')
            raise CallingProviderError(f'Retell API returned {response.status_code}: {detail}')
        try:
            return response.json()
        except ValueError as exc:
            raise CallingProviderError('Retell API returned invalid JSON') from exc

    async def get_agent(self, agent_id: str) -> dict:
        return await self._request('GET', f'/get-agent/{agent_id}')

    async def get_phone_number(self, phone_number: str) -> dict:
        return await self._request('GET', f'/get-phone-number/{phone_number}')

    async def health(self) -> dict:
        blockers: list[str] = []
        api_authenticated = False
        agent_exists = False
        number_exists = False
        outbound_agent_correct = False
        agent_payload: dict = {}
        number_payload: dict = {}

        if not self.api_key:
            blockers.append('RETELL_API_KEY missing')
        else:
            api_authenticated = True

        agent_id = settings.retell_agent_id
        if not agent_id:
            blockers.append('RETELL_AGENT_ID missing')
        elif self.api_key:
            try:
                agent_payload = await self.get_agent(agent_id)
                agent_exists = True
            except CallingProviderError as exc:
                blockers.append(str(exc))
                api_authenticated = 'authentication failed' not in str(exc).lower()

        from_number = normalize_phone(settings.retell_from_number)
        if not from_number:
            blockers.append('RETELL_FROM_NUMBER missing')
        elif self.api_key:
            try:
                number_payload = await self.get_phone_number(from_number)
                number_exists = True
                outbound_agents = number_payload.get('outbound_agents') or []
                outbound_agent_correct = any(str(item.get('agent_id')) == str(agent_id) for item in outbound_agents if isinstance(item, dict))
                if agent_id and not outbound_agent_correct:
                    blockers.append('Retell outbound number is not assigned to RETELL_AGENT_ID')
            except CallingProviderError as exc:
                blockers.append(str(exc))
                api_authenticated = 'authentication failed' not in str(exc).lower()

        webhook_ready = bool(settings.retell_webhook_api_key or settings.retell_api_key)
        if not webhook_ready:
            blockers.append('RETELL_WEBHOOK_API_KEY missing')
        if not settings.retell_tool_token:
            blockers.append('RETELL_TOOL_TOKEN missing')

        internal_test_ready = (
            settings.retell_internal_test_mode
            and api_authenticated
            and agent_exists
            and number_exists
            and outbound_agent_correct
            and webhook_ready
            and not any('missing' in item.lower() for item in blockers)
        )
        return {
            'configured': bool(self.api_key),
            'api_authenticated': api_authenticated,
            'agent_id_configured': bool(agent_id),
            'agent_exists': agent_exists,
            'agent_name': agent_payload.get('agent_name') if isinstance(agent_payload, dict) else None,
            'from_number_configured': bool(from_number),
            'number_exists': number_exists,
            'outbound_agent_correctly_assigned': outbound_agent_correct,
            'webhook_url_configured': bool(settings.retell_webhook_url),
            'webhook_signature_key_configured': webhook_ready,
            'tool_token_configured': bool(settings.retell_tool_token),
            'internal_test_ready': internal_test_ready,
            'prospect_calling_ready': False,
            'blockers': blockers,
        }

    async def place_call(self, *, to_number: str, call_attempt_id: str, dynamic_variables: dict[str, str]) -> dict:
        payload = {
            'from_number': normalize_phone(settings.retell_from_number),
            'to_number': normalize_phone(to_number),
            'override_agent_id': settings.retell_agent_id,
            'override_agent_version': 'latest_published',
            'metadata': {'voryx_call_attempt_id': call_attempt_id, 'mode': 'internal_test'},
            'retell_llm_dynamic_variables': {key: str(value) for key, value in dynamic_variables.items()},
        }
        result = await self._request('POST', '/v2/create-phone-call', payload)
        call_id = result.get('call_id') or result.get('call', {}).get('call_id') or result.get('provider_call_id')
        if not call_id:
            raise CallingProviderError('Retell did not return a real call ID')
        return result

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool:
        if not signature or not self.webhook_key:
            return False
        digest = hmac.new(self.webhook_key.encode(), raw_body, hashlib.sha256).digest()
        candidates = {
            digest.hex(),
            'sha256=' + digest.hex(),
            base64.b64encode(digest).decode(),
        }
        return any(hmac.compare_digest(signature, candidate) for candidate in candidates)


class MockCallingProvider:
    def __init__(self):
        self.calls: list[dict] = []

    async def health(self) -> dict:
        return {'configured': True, 'api_authenticated': True, 'internal_test_ready': True, 'prospect_calling_ready': False, 'blockers': []}

    async def place_call(self, *, to_number: str, call_attempt_id: str, dynamic_variables: dict[str, str]) -> dict:
        result = {'call_id': f'mock-call-{call_attempt_id}', 'to_number': masked_phone(to_number), 'metadata': dynamic_variables}
        self.calls.append(result)
        return result

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool:
        return signature == 'test-valid'


def calling_provider() -> RetellCallingProvider:
    return RetellCallingProvider()


def ensure_allstate_calling_campaign(db: Session, user_id: str | None = None) -> dict:
    now = _now()
    company = db.get(Company, ALLSTATE_COMPANY_ID)
    created = []
    if not company:
        company = Company(
            id=ALLSTATE_COMPANY_ID,
            name='Allstate - Himanshu',
            industry='Personal Insurance',
            status='Active',
            timezone='America/Toronto',
            notes='Insurance quote appointment workflow for Himanshu, a licensed Allstate sales agent.',
        )
        db.add(company)
        created.append('company')
        db.flush()

    campaign = db.get(Campaign, ALLSTATE_CAMPAIGN_ID)
    if not campaign:
        campaign = Campaign(
            id=ALLSTATE_CAMPAIGN_ID,
            company_id=ALLSTATE_COMPANY_ID,
            name='Allstate Quote Appointment Calling',
            description='Internal-test-only Retell calling workflow. Prospect calling, automation, queueing and retries are disabled.',
            industry='Personal Insurance',
            target_audience='Internal test recipient only',
            geographic_area='Ontario',
            daily_lead_goal=0,
            daily_email_goal=0,
            daily_email_limit=0,
            campaign_type='sales_calling',
            provisioning_state='Internal Testing',
            provisioning_result={'provider': 'retell', 'prospect_calling_enabled': False},
            timezone='America/Toronto',
            dry_run_mode=True,
            status='Active',
        )
        db.add(campaign)
        created.append('campaign')
        db.flush()

    settings_row = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == ALLSTATE_CAMPAIGN_ID))
    if not settings_row:
        settings_row = CallCampaignSettings(
            company_id=ALLSTATE_COMPANY_ID,
            campaign_id=ALLSTATE_CAMPAIGN_ID,
            provider='retell',
            provider_connected=False,
            provider_agent_id=settings.retell_agent_id or None,
            from_number=normalize_phone(settings.retell_from_number),
            timezone='America/Toronto',
            allowed_calling_days=[],
            allowed_calling_hours={},
            daily_call_limit=0,
            hourly_call_limit=0,
            concurrent_call_limit=1,
            internal_test_enabled=True,
            internal_test_numbers=[],
            prospect_calling_enabled=False,
            automated_queue_enabled=False,
            recording_enabled=True,
            transcription_enabled=True,
            appointment_booking_enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.add(settings_row)
        created.append('call_settings')
    else:
        settings_row.provider_agent_id = settings.retell_agent_id or settings_row.provider_agent_id
        settings_row.from_number = normalize_phone(settings.retell_from_number) or settings_row.from_number
        settings_row.prospect_calling_enabled = False
        settings_row.automated_queue_enabled = False
        settings_row.concurrent_call_limit = max(1, settings_row.concurrent_call_limit or 1)
        settings_row.updated_at = now

    db.flush()
    if created:
        db.add(ActivityLog(company_id=ALLSTATE_COMPANY_ID, user_id=user_id, action='Allstate Calling Provisioned', entity_type='Campaign', entity_id=ALLSTATE_CAMPAIGN_ID, metadata_json={'created': created}))
    return {'company_id': ALLSTATE_COMPANY_ID, 'campaign_id': ALLSTATE_CAMPAIGN_ID, 'created': created}


def call_settings(db: Session) -> CallCampaignSettings:
    ensure_allstate_calling_campaign(db)
    row = db.scalar(select(CallCampaignSettings).where(CallCampaignSettings.campaign_id == ALLSTATE_CAMPAIGN_ID))
    if not row:
        raise CallingProviderError('Allstate calling settings could not be provisioned')
    return row


def update_internal_test_number(db: Session, phone_number: str, allow: bool) -> dict:
    row = call_settings(db)
    phone = normalize_phone(phone_number)
    if not valid_us_ca_e164(phone):
        raise ValueError('Use a valid US/Canada E.164 phone number such as +14165551234')
    numbers = list(row.internal_test_numbers or [])
    if allow and phone not in numbers:
        numbers.append(phone)
    if not allow:
        numbers = [item for item in numbers if normalize_phone(item) != phone]
    row.internal_test_numbers = numbers
    row.updated_at = _now()
    return {'internal_test_numbers': [masked_phone(item) for item in numbers], 'count': len(numbers)}


async def authorize_internal_test_call(db: Session, user: User, phone_number: str, confirmation: str, provider: RetellCallingProvider | MockCallingProvider | None = None) -> tuple[bool, list[str], dict]:
    blockers: list[str] = []
    row = call_settings(db)
    provider = provider or calling_provider()
    phone = normalize_phone(phone_number)
    if not settings.retell_internal_test_mode:
        blockers.append('RETELL_INTERNAL_TEST_MODE is not enabled')
    if not row.internal_test_enabled:
        blockers.append('Internal test calling is disabled for this campaign')
    if row.prospect_calling_enabled or row.automated_queue_enabled:
        blockers.append('Prospect or automated calling must remain disabled for this milestone')
    if confirmation != INTERNAL_CONFIRMATION:
        blockers.append('Confirmation must exactly match PLACE INTERNAL TEST CALL')
    if not valid_us_ca_e164(phone):
        blockers.append('Phone number must be a valid US/Canada E.164 number')
    allowlist = {normalize_phone(item) for item in row.internal_test_numbers or []}
    if phone and phone not in allowlist:
        blockers.append('Phone number is not on the internal-test allowlist')
    active = db.scalars(select(CallAttempt).where(CallAttempt.campaign_id == ALLSTATE_CAMPAIGN_ID, CallAttempt.status.in_(CALL_ACTIVE_STATUSES))).first()
    if active:
        blockers.append('An internal test call is already active')
    health = await provider.health()
    if not health.get('internal_test_ready'):
        blockers.extend([str(item) for item in health.get('blockers') or []])
    return not blockers, blockers, health


async def create_internal_test_call(db: Session, user: User, payload: dict, provider: RetellCallingProvider | MockCallingProvider | None = None) -> dict:
    phone = normalize_phone(payload.get('phone_number'))
    confirmation = str(payload.get('confirmation_text') or '')
    provider = provider or calling_provider()
    allowed, blockers, health = await authorize_internal_test_call(db, user, phone, confirmation, provider)
    if not allowed:
        return {'ok': False, 'blocked': True, 'status': 'blocked', 'blockers': sorted(set(blockers)), 'health': health}

    now = _now()
    attempt = CallAttempt(
        company_id=ALLSTATE_COMPANY_ID,
        campaign_id=ALLSTATE_CAMPAIGN_ID,
        provider='retell',
        provider_agent_id=settings.retell_agent_id or None,
        from_number=normalize_phone(settings.retell_from_number),
        to_number=phone,
        mode='internal_test',
        status='requested',
        confirmation_text=confirmation,
        requested_by=user.id,
        requested_at=now,
        internal_test=True,
        metadata_json={
            'recipient_name': str(payload.get('recipient_name') or 'Himanshu'),
            'insurance_interest': str(payload.get('insurance_interest') or 'Auto and property insurance'),
            'booking_timezone': str(payload.get('booking_timezone') or 'America/Toronto'),
        },
        created_at=now,
        updated_at=now,
    )
    db.add(attempt)
    db.flush()

    consent = LeadPhoneConsent(
        company_id=ALLSTATE_COMPANY_ID,
        canonical_lead_id=None,
        phone_number=phone,
        consent_status='granted',
        consent_type='internal_self_test',
        consent_text='Internal self-test number entered in Voryx dashboard with explicit confirmation.',
        consent_source='user_entered_voryx_dashboard',
        consent_timestamp=now,
        consented_number=phone,
        automated_or_ai_call_consent=True,
        internal_self_test=True,
        verified_by=user.id,
        verified_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(consent)

    dynamic_variables = {
        'customer_name': str(payload.get('recipient_name') or 'Himanshu'),
        'agent_name': 'Himanshu',
        'company_name': 'Allstate',
        'campaign_name': 'Allstate Quote Appointment Calling',
        'insurance_interest': str(payload.get('insurance_interest') or 'Auto and property insurance'),
        'consent_source': 'Internal self-test',
        'consent_date': now.date().isoformat(),
        'booking_timezone': str(payload.get('booking_timezone') or 'America/Toronto'),
        'internal_test': 'true',
        'voryx_call_attempt_id': attempt.id,
    }
    try:
        receipt = await provider.place_call(to_number=phone, call_attempt_id=attempt.id, dynamic_variables=dynamic_variables)
    except Exception as exc:
        attempt.status = 'provider_failed'
        attempt.termination_reason = str(exc)
        attempt.updated_at = _now()
        db.add(ActivityLog(company_id=ALLSTATE_COMPANY_ID, user_id=user.id, action='Retell Internal Test Call Failed', entity_type='CallAttempt', entity_id=attempt.id, metadata_json={'error': str(exc), 'to_number': masked_phone(phone)}))
        return {'ok': False, 'blocked': False, 'status': 'provider_failed', 'message': str(exc), 'call_attempt_id': attempt.id}

    provider_call_id = receipt.get('call_id') or receipt.get('call', {}).get('call_id') or receipt.get('provider_call_id')
    attempt.provider_call_id = str(provider_call_id)
    attempt.status = 'initiated'
    attempt.provider_receipt = _redact_payload(receipt)
    attempt.updated_at = _now()
    db.add(ActivityLog(company_id=ALLSTATE_COMPANY_ID, user_id=user.id, action='Retell Internal Test Call Initiated', entity_type='CallAttempt', entity_id=attempt.id, metadata_json={'provider_call_id': attempt.provider_call_id, 'to_number': masked_phone(phone)}))
    return {'ok': True, 'status': attempt.status, 'call_attempt_id': attempt.id, 'retell_call_id': attempt.provider_call_id, 'from_number': attempt.from_number, 'to_number': masked_phone(phone)}


def _extract_call(payload: dict) -> dict:
    call = payload.get('call') or payload.get('call_detail') or payload.get('data') or {}
    return call if isinstance(call, dict) else {}


def _extract_call_id(payload: dict) -> str | None:
    call = _extract_call(payload)
    return str(call.get('call_id') or payload.get('call_id') or payload.get('provider_call_id') or '') or None


def _timestamp_ms_to_dt(value: Any) -> datetime | None:
    try:
        if value is None:
            return None
        number = float(value)
        if number > 10_000_000_000:
            number = number / 1000
        return datetime.utcfromtimestamp(number)
    except Exception:
        return None


def process_retell_webhook(db: Session, raw_body: bytes, payload: dict) -> dict:
    event_type = str(payload.get('event') or payload.get('event_type') or 'unknown')
    provider_call_id = _extract_call_id(payload)
    event_hash = _event_hash(raw_body, event_type, provider_call_id)
    existing = db.scalar(select(RetellWebhookEvent).where(RetellWebhookEvent.event_hash == event_hash))
    if existing:
        return {'ok': True, 'duplicate': True, 'event_id': existing.id}

    now = _now()
    event = RetellWebhookEvent(
        provider_call_id=provider_call_id,
        event_type=event_type,
        event_hash=event_hash,
        received_at=now,
        processing_status='received',
        payload_redacted=_redact_payload(payload),
    )
    db.add(event)
    db.flush()
    try:
        call = _extract_call(payload)
        attempt = db.scalar(select(CallAttempt).where(CallAttempt.provider_call_id == provider_call_id)) if provider_call_id else None
        if attempt:
            attempt.status = TERMINAL_STATUS_MAP.get(event_type, str(call.get('call_status') or attempt.status))
            attempt.updated_at = now
            attempt.started_at = _timestamp_ms_to_dt(call.get('start_timestamp')) or attempt.started_at
            attempt.ended_at = _timestamp_ms_to_dt(call.get('end_timestamp')) or attempt.ended_at
            attempt.duration_seconds = int(call.get('duration_ms') / 1000) if isinstance(call.get('duration_ms'), (int, float)) else attempt.duration_seconds
            attempt.termination_reason = str(call.get('disconnection_reason') or attempt.termination_reason or '') or None
            transcript_text = call.get('transcript') or payload.get('transcript') or ''
            analysis = call.get('call_analysis') or call.get('analysis') or payload.get('call_analysis') or {}
            if transcript_text or analysis or call.get('recording_url'):
                transcript = db.scalar(select(CallTranscript).where(CallTranscript.call_attempt_id == attempt.id))
                if not transcript:
                    transcript = CallTranscript(call_attempt_id=attempt.id, created_at=now, updated_at=now)
                    db.add(transcript)
                transcript.transcript = transcript_text or transcript.transcript
                transcript.transcript_segments = call.get('transcript_object') or call.get('transcript_segments') or transcript.transcript_segments or []
                transcript.summary = analysis.get('call_summary') or analysis.get('summary') or transcript.summary
                transcript.recording_url = call.get('recording_url') or transcript.recording_url
                transcript.sentiment = analysis.get('user_sentiment') or analysis.get('sentiment') or transcript.sentiment
                transcript.extracted_fields = {**(transcript.extracted_fields or {}), **(analysis if isinstance(analysis, dict) else {})}
                transcript.updated_at = now
            if analysis:
                disposition = db.scalar(select(CallDisposition).where(CallDisposition.call_attempt_id == attempt.id))
                if not disposition:
                    disposition = CallDisposition(call_attempt_id=attempt.id, created_at=now, updated_at=now)
                    db.add(disposition)
                outcome = analysis.get('call_outcome') or analysis.get('call_successful') or 'incomplete'
                disposition.disposition = str(outcome)
                disposition.interested = bool(analysis.get('interested') or analysis.get('appointment_requested'))
                disposition.appointment_requested = bool(analysis.get('appointment_requested'))
                disposition.callback_requested = bool(analysis.get('callback_requested'))
                disposition.do_not_call_requested = bool(analysis.get('do_not_call_requested'))
                disposition.notes = analysis.get('summary') or analysis.get('call_summary') or disposition.notes
                disposition.updated_at = now
        event.processing_status = 'processed'
        event.processed_at = now
    except Exception as exc:
        event.processing_status = 'failed'
        event.error = str(exc)
        raise
    return {'ok': True, 'duplicate': False, 'event_id': event.id, 'event_type': event_type, 'provider_call_id': provider_call_id}


def validate_tool_token(token: str | None) -> bool:
    return bool(settings.retell_tool_token and token and hmac.compare_digest(settings.retell_tool_token, token))


def book_quote_appointment(db: Session, payload: dict) -> dict:
    if isinstance(payload.get('args'), dict):
        payload = payload['args']
    call_attempt_id = str(payload.get('voryx_call_attempt_id') or '')
    attempt = db.get(CallAttempt, call_attempt_id)
    if not attempt:
        return {'ok': False, 'blocker': 'call_attempt_not_found'}
    start = ' '.join(part for part in [str(payload.get('appointment_date') or '').strip(), str(payload.get('appointment_time') or '').strip()] if part)
    timezone = str(payload.get('timezone') or 'America/Toronto')
    existing = db.scalar(select(CallAppointment).where(CallAppointment.call_attempt_id == attempt.id, CallAppointment.start_time == start, CallAppointment.timezone == timezone))
    if existing:
        return {'ok': True, 'appointment_id': existing.id, 'idempotent': True}
    appointment = CallAppointment(
        call_attempt_id=attempt.id,
        assigned_agent='Himanshu',
        start_time=start,
        timezone=timezone,
        status='requested',
        insurance_interest=str(payload.get('insurance_interest') or ''),
        notes=str(payload.get('notes') or ''),
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(appointment)
    disposition = db.scalar(select(CallDisposition).where(CallDisposition.call_attempt_id == attempt.id))
    if not disposition:
        disposition = CallDisposition(call_attempt_id=attempt.id, created_at=_now(), updated_at=_now())
        db.add(disposition)
    disposition.appointment_requested = True
    disposition.appointment_booked = True
    disposition.disposition = 'appointment_requested'
    disposition.updated_at = _now()
    return {'ok': True, 'appointment_id': appointment.id, 'status': appointment.status}


def mark_do_not_call(db: Session, payload: dict) -> dict:
    if isinstance(payload.get('args'), dict):
        payload = payload['args']
    call_attempt_id = str(payload.get('voryx_call_attempt_id') or '')
    attempt = db.get(CallAttempt, call_attempt_id)
    phone = normalize_phone(payload.get('phone_number') or getattr(attempt, 'to_number', ''))
    if not attempt:
        return {'ok': False, 'blocker': 'call_attempt_not_found'}
    existing = db.scalar(select(SuppressionEntry).where(SuppressionEntry.company_id == attempt.company_id, SuppressionEntry.kind == 'phone', SuppressionEntry.value == phone))
    if not existing:
        db.add(SuppressionEntry(company_id=attempt.company_id, kind='phone', value=phone, reason=str(payload.get('reason') or 'Retell do-not-call request'), source='retell_tool'))
    disposition = db.scalar(select(CallDisposition).where(CallDisposition.call_attempt_id == attempt.id))
    if not disposition:
        disposition = CallDisposition(call_attempt_id=attempt.id, created_at=_now(), updated_at=_now())
        db.add(disposition)
    disposition.do_not_call_requested = True
    disposition.disposition = 'do_not_call'
    disposition.notes = str(payload.get('reason') or disposition.notes or '')
    disposition.updated_at = _now()
    return {'ok': True, 'suppressed': True}
