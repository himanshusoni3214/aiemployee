import base64
import hashlib
import hmac
import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

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
CORRECTED_INTERNAL_CONFIRMATION = 'PLACE CORRECTED INTERNAL TEST CALL'
REFINED_INTERNAL_CONFIRMATION = 'PLACE REFINED INTERNAL TEST CALL'
INTERNAL_CONFIRMATIONS = {INTERNAL_CONFIRMATION, CORRECTED_INTERNAL_CONFIRMATION, REFINED_INTERNAL_CONFIRMATION}
US_CA_E164_RE = re.compile(r'^\+1[2-9]\d{9}$')
ALLSTATE_BEGIN_MESSAGE = (
    "Hi {{customer_name}}, this is Ava calling on behalf of Himanshu Soni, "
    "an Allstate Sales Agent in Scarborough. This is a test of his insurance quote "
    "appointment workflow. Is now a bad time for a quick conversation?"
)
ALLSTATE_CONSENTED_PROSPECT_BEGIN_MESSAGE = (
    "Hi {{customer_name}}, this is Ava calling on behalf of Himanshu Soni, "
    "an Allstate Sales Agent in Scarborough. You had given permission to be contacted "
    "about reviewing your insurance options. Is now a bad time for a quick conversation?"
)
ALLSTATE_RECORDING_DISCLOSURE = (
    "Before we continue, this call may be recorded and transcribed for quality and appointment notes."
)
REQUIRED_DYNAMIC_VARIABLES = [
    'customer_name',
    'assistant_name',
    'agent_name',
    'agent_role',
    'company_name',
    'agency_location',
    'campaign_name',
    'call_purpose',
    'insurance_interest',
    'consent_source',
    'consent_date',
    'booking_timezone',
    'internal_test',
    'recording_disclosure_enabled',
    'recording_disclosure',
    'consent_validated_for_called_number',
    'voryx_call_attempt_id',
]
ALLSTATE_VOICE_ID = 'retell-Della'
ALLSTATE_VOICE_NAME = 'Della'
ALLSTATE_VOICE_SETTINGS = {
    'voice_id': ALLSTATE_VOICE_ID,
    'voice_name': ALLSTATE_VOICE_NAME,
    'responsiveness': 0.78,
    'interruption_sensitivity': 0.75,
    'enable_backchannel': True,
    'backchannel_words': ['okay', 'right', 'I understand'],
    'ambient_sound': None,
    'denoising_mode': 'noise-cancellation',
    'pronunciation_guidance': {
        'Himanshu Soni': 'him-AHN-shoo SOH-nee',
        'Allstate': 'ALL-state',
        'Scarborough': 'SCAR-bur-oh',
        'Ontario': 'on-TAIR-ee-oh',
    },
}
ALLSTATE_REFINED_PROMPT = f"""## Identity and role

You are Ava, a professional automated calling assistant calling on behalf of Himanshu Soni, an Allstate Sales Agent in Scarborough, Ontario.

You are not human, and you are not a licensed insurance agent. Do not claim or imply that you are human. If directly asked whether you are automated, virtual, a robot or AI, answer truthfully.

Your job is to have a short, respectful conversation and arrange a quote appointment with Himanshu when appropriate. Do not complete an insurance quote during this call.

## Openings

For an internal test, begin exactly:
"{ALLSTATE_BEGIN_MESSAGE}"

For a consented prospect, begin exactly:
"{ALLSTATE_CONSENTED_PROSPECT_BEGIN_MESSAGE}"

Only use the consented-prospect wording when all consent variables are present and {{{{consent_validated_for_called_number}}}} is "true". Required consent data is consent_source, consent_date, consent tied to the called number, and automated or synthesized-call consent. Never invent permission.

If {{{{recording_disclosure_enabled}}}} is "true", state near the beginning:
"{ALLSTATE_RECORDING_DISCLOSURE}"

If the person objects to recording or transcription, do not continue a prospect call. Offer a human callback if supported; otherwise end politely. Mark recording_objection in the call outcome and avoid storing unnecessary conversation content.

Immediately after permission to continue, say:
"The reason for my call is to see whether reviewing your auto or property insurance would be useful and, if it is, arrange a short quote appointment with Himanshu."

## Honest automation disclosure

When asked "Are you AI?", "Are you a robot?", "Are you a real person?", or similar, say:
"Yes, I'm an automated calling assistant helping Himanshu with initial conversations and scheduling. I can't provide insurance advice or quote prices, but I can arrange a conversation with him."

Never evade the question. Never say or imply that Ava is human.

## Conversation style

Use one question at a time. Keep most replies to one or two sentences. Acknowledge the answer before the next question. Use contractions naturally. Avoid repeating the person's name. Do not repeat Allstate in every response. Avoid excessive enthusiasm, sales cliches and lists. Stop speaking when interrupted. Allow brief natural pauses. Never pressure. Do not continue qualifying after a clear rejection. Do not sound like a general-purpose assistant.

Use these acknowledgements sparingly: "Okay.", "That makes sense.", "Understood.", "Got it."

Do not say: "Absolutely!", "Great question!", "I'd be happy to help!", or "As an AI..."

## Scope restrictions

Never quote a premium, promise savings, guarantee eligibility, bind coverage, compare exact coverage without approved information, provide final coverage advice, claim the person is currently an Allstate customer, request banking information, request payment-card information, request a Social Insurance Number, request a driver's licence number during the initial automated call, or request unnecessary sensitive information.

For detailed coverage, eligibility, underwriting or pricing questions say:
"Himanshu would need to review that with you directly because he is the licensed agent. I can arrange a convenient time for that conversation."

## Scepticism handling

When asked "Is this really Allstate?" say:
"I'm calling on behalf of Himanshu Soni, an Allstate Sales Agent. I can arrange for you to speak with him directly, and I won't ask for banking or payment information."

When asked "How did you get my number?" use only:
"Our record shows permission was provided through {{{{consent_source}}}} on {{{{consent_date}}}}."

If consent_source or consent_date is absent, do not fabricate an explanation. Apologize, flag consent_review_required, and end the call.

When asked "Is this a scam?" say:
"I understand the concern. I'm calling on behalf of Himanshu Soni, an Allstate Sales Agent. I won't ask for payment, banking details or sensitive identity information. I can arrange a direct callback with Himanshu instead."

When told "I already have insurance" say:
"That makes sense. This would only be an optional comparison of coverage and service. Would reviewing it near your renewal date be useful?"

When told "I'm not interested" say:
"Understood. Thank you for your time."

Do not continue selling after rejection.

## Do not call

If the person says do not call, stop calling, remove my number, or take me off the list, say:
"Understood. I'll mark this number not to be contacted again. Thank you."

Invoke voryx_mark_do_not_call immediately and end the call.

## Appointment

When the person agrees to speak with Himanshu, confirm the insurance interest, preferred date, preferred time, and America/Toronto timezone. Invoke voryx_book_quote_appointment and repeat the confirmed appointment naturally. Do not claim the appointment is booked unless the function confirms success.

## Internal testing

When {{{{internal_test}}}} is "true", state that it is a test of Himanshu's quote appointment workflow, exercise the same insurance conversation flow, do not create a prospect lead, and do not change a real prospect's consent or status.

## Pronunciation

Himanshu Soni: him-AHN-shoo SOH-nee.
Allstate: ALL-state.
Scarborough: SCAR-bur-oh.
Ontario: on-TAIR-ee-oh.
"""
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


def _local_date(timezone: str = 'America/Toronto', now: datetime | None = None) -> str:
    now = now or _now()
    try:
        if now.tzinfo is None:
            return now.replace(tzinfo=ZoneInfo('UTC')).astimezone(ZoneInfo(timezone)).date().isoformat()
        return now.astimezone(ZoneInfo(timezone)).date().isoformat()
    except Exception:
        return now.date().isoformat()


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

    async def get_call(self, call_id: str) -> dict:
        return await self._request('GET', f'/v2/get-call/{call_id}')

    async def health(self) -> dict:
        blockers: list[str] = []
        api_authenticated = False
        agent_exists = False
        number_exists = False
        outbound_agent_correct = False
        agent_payload: dict = {}
        number_payload: dict = {}
        configured_agent_version = str(settings.retell_agent_version or 'latest_published')

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
                if agent_payload.get('agent_name') != ALLSTATE_AGENT_NAME:
                    blockers.append('Configured Retell agent is not the Voryx Allstate agent')
                if str(agent_payload.get('agent_name') or '').strip().lower() in {'call agent', 'generic call agent'}:
                    blockers.append('Generic Call Agent is selected')
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
                if len(outbound_agents) != 1:
                    blockers.append('Retell outbound number must have exactly one outbound agent for QA')
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
            'agent_id': agent_payload.get('agent_id') if isinstance(agent_payload, dict) else settings.retell_agent_id,
            'agent_version': agent_payload.get('version') if isinstance(agent_payload, dict) else None,
            'agent_is_published': agent_payload.get('is_published') if isinstance(agent_payload, dict) else None,
            'configured_agent_version': configured_agent_version,
            'response_engine': agent_payload.get('response_engine') if isinstance(agent_payload, dict) else None,
            'voice_id': agent_payload.get('voice_id') if isinstance(agent_payload, dict) else None,
            'responsiveness': agent_payload.get('responsiveness') if isinstance(agent_payload, dict) else None,
            'interruption_sensitivity': agent_payload.get('interruption_sensitivity') if isinstance(agent_payload, dict) else None,
            'enable_backchannel': agent_payload.get('enable_backchannel') if isinstance(agent_payload, dict) else None,
            'backchannel_words': agent_payload.get('backchannel_words') if isinstance(agent_payload, dict) else None,
            'ambient_sound': agent_payload.get('ambient_sound') if isinstance(agent_payload, dict) else None,
            'from_number_configured': bool(from_number),
            'number_exists': number_exists,
            'outbound_agent_correctly_assigned': outbound_agent_correct,
            'outbound_agents': [
                {
                    'agent_id': item.get('agent_id'),
                    'agent_version': item.get('agent_version'),
                    'weight': item.get('weight'),
                }
                for item in (number_payload.get('outbound_agents') or [])
                if isinstance(item, dict)
            ] if isinstance(number_payload, dict) else [],
            'webhook_url_configured': bool(settings.retell_webhook_url),
            'webhook_signature_key_configured': webhook_ready,
            'tool_token_configured': bool(settings.retell_tool_token),
            'internal_test_ready': internal_test_ready,
            'prospect_calling_ready': False,
            'blockers': blockers,
        }

    async def place_call(self, *, to_number: str, call_attempt_id: str, dynamic_variables: dict[str, str]) -> dict:
        override_version = str(settings.retell_agent_version or 'latest_published')
        payload = {
            'from_number': normalize_phone(settings.retell_from_number),
            'to_number': normalize_phone(to_number),
            'override_agent_id': settings.retell_agent_id,
            'override_agent_version': int(override_version) if override_version.isdigit() else override_version,
            'metadata': {'voryx_call_attempt_id': call_attempt_id, 'mode': 'internal_test', 'expected_agent_name': ALLSTATE_AGENT_NAME},
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
            call_recording_disclosure_enabled=True,
            appointment_booking_enabled=True,
            created_at=now,
            updated_at=now,
        )
        db.add(settings_row)
        created.append('call_settings')
    else:
        desired_values = {
            'provider_agent_id': settings.retell_agent_id or settings_row.provider_agent_id,
            'from_number': normalize_phone(settings.retell_from_number) or settings_row.from_number,
            'prospect_calling_enabled': False,
            'automated_queue_enabled': False,
            'concurrent_call_limit': max(1, settings_row.concurrent_call_limit or 1),
            'call_recording_disclosure_enabled': bool(
                settings_row.call_recording_disclosure_enabled
                or settings_row.recording_enabled
                or settings_row.transcription_enabled
            ),
        }
        changed = False
        for field, value in desired_values.items():
            if getattr(settings_row, field) != value:
                setattr(settings_row, field, value)
                changed = True
        if changed:
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


def internal_test_dynamic_variables(
    call_attempt_id: str,
    payload: dict | None = None,
    now: datetime | None = None,
    call_settings_row: CallCampaignSettings | None = None,
) -> dict[str, str]:
    payload = payload or {}
    now = now or _now()
    disclosure_enabled = True if call_settings_row is None else bool(
        call_settings_row.call_recording_disclosure_enabled
        and (call_settings_row.recording_enabled or call_settings_row.transcription_enabled)
    )
    values = {
        'customer_name': str(payload.get('recipient_name') or 'Himanshu'),
        'assistant_name': 'Ava',
        'agent_name': 'Himanshu Soni',
        'agent_role': 'Allstate Sales Agent',
        'company_name': 'Allstate',
        'agency_location': 'Scarborough, Ontario',
        'campaign_name': 'Allstate Quote Appointment Calling',
        'call_purpose': 'Internal test of an insurance quote appointment conversation',
        'insurance_interest': str(payload.get('insurance_interest') or 'Auto and home insurance'),
        'consent_source': 'Internal self-test entered in Voryx',
        'consent_date': _local_date(str(payload.get('booking_timezone') or 'America/Toronto'), now),
        'booking_timezone': str(payload.get('booking_timezone') or 'America/Toronto'),
        'internal_test': 'true',
        'recording_disclosure_enabled': 'true' if disclosure_enabled else 'false',
        'recording_disclosure': ALLSTATE_RECORDING_DISCLOSURE if disclosure_enabled else '',
        'consent_validated_for_called_number': 'true',
        'voryx_call_attempt_id': call_attempt_id,
    }
    return {key: str(values[key]) for key in REQUIRED_DYNAMIC_VARIABLES}


def internal_test_preview_payload(
    call_attempt_id: str = '<created after click>',
    payload: dict | None = None,
    call_settings_row: CallCampaignSettings | None = None,
) -> dict:
    variables = internal_test_dynamic_variables(call_attempt_id, payload, call_settings_row=call_settings_row)
    missing = [key for key in REQUIRED_DYNAMIC_VARIABLES if not variables.get(key)]
    return {
        'begin_message': ALLSTATE_BEGIN_MESSAGE,
        'consented_prospect_begin_message': ALLSTATE_CONSENTED_PROSPECT_BEGIN_MESSAGE,
        'recording_disclosure': ALLSTATE_RECORDING_DISCLOSURE,
        'recording_disclosure_enabled': variables['recording_disclosure_enabled'] == 'true',
        'business_purpose': variables['call_purpose'],
        'dynamic_variables': variables,
        'required_dynamic_variables': REQUIRED_DYNAMIC_VARIABLES,
        'missing_dynamic_variables': missing,
        'override_agent_id': settings.retell_agent_id,
        'override_agent_version': str(settings.retell_agent_version or 'latest_published'),
        'from_number': normalize_phone(settings.retell_from_number),
        'expected_agent_name': ALLSTATE_AGENT_NAME,
        'voice': ALLSTATE_VOICE_SETTINGS,
    }


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
    if confirmation not in INTERNAL_CONFIRMATIONS:
        blockers.append(f'Confirmation must exactly match {REFINED_INTERNAL_CONFIRMATION}')
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
        **internal_test_dynamic_variables(attempt.id, payload, now, call_settings(db)),
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


def _sync_attempt_from_call_payload(db: Session, attempt: CallAttempt, call: dict) -> None:
    now = _now()
    attempt.status = str(call.get('call_status') or attempt.status)
    attempt.provider_agent_id = str(call.get('agent_id') or attempt.provider_agent_id or '') or None
    attempt.started_at = _timestamp_ms_to_dt(call.get('start_timestamp')) or attempt.started_at
    attempt.ended_at = _timestamp_ms_to_dt(call.get('end_timestamp')) or attempt.ended_at
    attempt.duration_seconds = int(call.get('duration_ms') / 1000) if isinstance(call.get('duration_ms'), (int, float)) else attempt.duration_seconds
    attempt.termination_reason = str(call.get('disconnection_reason') or attempt.termination_reason or '') or None
    receipt = _redact_payload({
        'call_id': call.get('call_id'),
        'agent_id': call.get('agent_id'),
        'agent_name': call.get('agent_name'),
        'agent_version': call.get('agent_version'),
        'call_status': call.get('call_status'),
        'disconnection_reason': call.get('disconnection_reason'),
        'duration_ms': call.get('duration_ms'),
        'metadata': call.get('metadata'),
        'retell_llm_dynamic_variables': call.get('retell_llm_dynamic_variables'),
        'from_number': call.get('from_number'),
        'to_number': call.get('to_number'),
    })
    attempt.provider_receipt = {**(attempt.provider_receipt or {}), **receipt}
    attempt.updated_at = now
    transcript_text = call.get('transcript') or ''
    analysis = call.get('call_analysis') or call.get('analysis') or {}
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
        custom = analysis.get('custom_analysis_data') if isinstance(analysis.get('custom_analysis_data'), dict) else {}
        transcript.extracted_fields = {**(transcript.extracted_fields or {}), **(analysis if isinstance(analysis, dict) else {}), **custom}
        transcript.updated_at = now
    if analysis:
        custom = analysis.get('custom_analysis_data') if isinstance(analysis.get('custom_analysis_data'), dict) else {}
        disposition = db.scalar(select(CallDisposition).where(CallDisposition.call_attempt_id == attempt.id))
        if not disposition:
            disposition = CallDisposition(call_attempt_id=attempt.id, created_at=now, updated_at=now)
            db.add(disposition)
        outcome = custom.get('call_outcome') or analysis.get('call_outcome') or analysis.get('call_successful') or 'incomplete'
        disposition.disposition = str(outcome)
        disposition.interested = bool(custom.get('interested') or custom.get('appointment_requested') or analysis.get('appointment_requested'))
        disposition.appointment_requested = bool(custom.get('appointment_requested') or analysis.get('appointment_requested'))
        disposition.callback_requested = bool(custom.get('callback_requested') or analysis.get('callback_requested'))
        disposition.do_not_call_requested = bool(custom.get('do_not_call_requested') or analysis.get('do_not_call_requested'))
        disposition.notes = custom.get('summary') or analysis.get('summary') or analysis.get('call_summary') or disposition.notes
        disposition.updated_at = now


async def sync_call_attempt_from_retell(db: Session, attempt: CallAttempt, provider: RetellCallingProvider | None = None) -> bool:
    if not attempt.provider_call_id:
        return False
    provider = provider or calling_provider()
    call = await provider.get_call(attempt.provider_call_id)
    _sync_attempt_from_call_payload(db, attempt, call)
    return True


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
            call_payload = {**call, **{key: payload[key] for key in ('transcript', 'call_analysis', 'analysis') if key in payload}}
            _sync_attempt_from_call_payload(db, attempt, call_payload)
            if event_type in TERMINAL_STATUS_MAP:
                attempt.status = TERMINAL_STATUS_MAP[event_type]
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
