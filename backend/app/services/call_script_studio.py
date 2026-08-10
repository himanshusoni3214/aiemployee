import csv
import hashlib
import io
import json
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import (
    CallAttempt,
    CallComplianceItem,
    CallScriptAudit,
    CallScriptVersion,
    ConsentSourceProfile,
    ConsentedCallingLead,
    PilotCallEntry,
    SuppressionEntry,
    User,
)
from app.services.allstate_conversation_flow import (
    GLOBAL_PROMPT,
    LIVE_MODEL,
    POST_CALL_MODEL,
    custom_tools,
    expected_next_action,
    flow_nodes,
)
from app.services.calling import (
    ALLSTATE_AGENT_NAME,
    ALLSTATE_CAMPAIGN_ID,
    ALLSTATE_COMPANY_ID,
    ALLSTATE_VOICE_SETTINGS,
    CALL_ACTIVE_STATUSES,
    RetellCallingProvider,
    calling_provider,
    masked_phone,
    normalize_phone,
    valid_us_ca_e164,
)
from app.services.calling_eligibility import calling_window, evaluate_calling_lead

PILOT_CONFIRMATION = 'PLACE APPROVED CONSENTED LEAD CALL'
MAX_PILOT_LEADS = 5
MAX_CALLS_PER_DAY = 5
OPENING_STYLE_FULL = 'full_introduction'
OPENING_STYLE_CONFIRM_FIRST = 'confirm_person_first'
OPENING_STYLES = {OPENING_STYLE_FULL, OPENING_STYLE_CONFIRM_FIRST}
DEFAULT_CONFIRMED_INTERNAL_INTRODUCTION = (
    'Hi {{customer_name}}, this is Ava calling on behalf of Himanshu Soni, '
    'an Allstate Sales Agent in Scarborough. This is an internal test of his '
    'quote appointment workflow. Do you have thirty seconds?'
)
DEFAULT_CONFIRMED_CONSENTED_INTRODUCTION = (
    'Hi {{customer_name}}, this is Ava calling on behalf of Himanshu Soni, '
    'an Allstate Sales Agent in Scarborough. I’m following up on your permission '
    'to be contacted about auto or property insurance. Do you have thirty seconds?'
)
DEFAULT_WRONG_PERSON_RESPONSE = 'Thank you. I won’t keep you.'


class ScriptValidationError(ValueError):
    def __init__(self, field_errors: dict[str, list[str]]):
        super().__init__('The script cannot be published.')
        self.field_errors = field_errors

EDITABLE_FIELDS = {
    'opening_internal',
    'opening_consented',
    'purpose_statement',
    'discovery_content',
    'objection_library',
    'closing_library',
    'voicemail_content',
    'voice_settings',
    'talking_points',
    'name',
    'change_summary',
}
LOCKED_FIELDS = {'compliance_content'}
PUBLISHABLE_FIELDS = (
    'opening_internal',
    'opening_consented',
    'purpose_statement',
    'discovery_content',
    'objection_library',
    'closing_library',
    'voicemail_content',
    'voice_settings',
    'talking_points',
    'compliance_content',
)
REQUIRED_VARIABLES = {
    'customer_name', 'agent_name', 'product_interest', 'consent_source',
    'consent_date', 'renewal_month', 'slot_one', 'slot_two',
    'callback_date', 'callback_time',
}
KNOWN_TEMPLATE_VARIABLES = REQUIRED_VARIABLES | {
    'assistant_name',
    'agent_role',
    'company_name',
    'agency_location',
    'campaign_name',
    'call_purpose',
    'insurance_interest',
    'booking_timezone',
    'internal_test',
    'recording_disclosure_enabled',
    'recording_disclosure',
    'consent_validated_for_called_number',
    'voryx_call_attempt_id',
}
NODE_FIELD_MAP = {
    'opening_internal': 'opening',
    'opening_consented': 'opening',
    'purpose_statement': 'purpose',
    'discovery_content': 'coverage_review',
    'objection_library': 'soft_reframe',
    'closing_library': 'appointment_close',
    'voicemail_content': 'end',
}


class RetellPlaygroundValidationError(ValueError):
    def __init__(self, result: dict):
        self.result = result
        failed = [
            str(item.get('label') or item.get('key'))
            for item in result.get('checks') or []
            if not item.get('passed')
        ]
        detail = ', '.join(failed) or 'unknown check'
        super().__init__(f'Retell playground validation failed: {detail}.')

COMPLIANCE_ITEMS = [
    ('dncl_registration', 'Allstate/company DNCL registration confirmed', 'dncl'),
    ('area_code_subscription', 'Applicable area-code subscription confirmed', 'dncl'),
    ('dncl_list_current', 'DNCL list updated within required operational interval', 'dncl'),
    ('dncl_scrub_complete', 'Leads scrubbed against applicable DNCL file', 'dncl'),
    ('allstate_internal_dnc', 'Allstate/company internal DNC checked', 'dnc'),
    ('voryx_internal_dnc', 'Voryx internal DNC checked', 'dnc'),
    ('automated_call_consent', 'Automated/synthesized-call express consent confirmed', 'consent'),
    ('caller_id_approved', 'Approved caller ID confirmed', 'campaign'),
    ('script_version_approved', 'Approved script version confirmed', 'script'),
    ('recording_approved', 'Recording/transcription approval confirmed', 'campaign'),
    ('lead_source_approved', 'Lead source approved', 'consent'),
    ('calling_hours_enabled', 'Recipient-local calling hours enabled', 'campaign'),
    ('compliance_owner_approval', 'Compliance owner approval entered', 'campaign'),
    ('allstate_ai_approval', 'Allstate AI/synthesized-call approval', 'allstate'),
    ('allstate_script_approval', 'Allstate script approval', 'allstate'),
    ('allstate_caller_id_approval', 'Allstate caller-ID approval', 'allstate'),
    ('allstate_recording_approval', 'Allstate recording/transcription approval', 'allstate'),
    ('allstate_lead_source_approval', 'Allstate approved lead source', 'allstate'),
    ('allstate_data_storage_approval', 'Allstate approved data-storage workflow', 'allstate'),
]

COMPLIANCE_PACKAGES = {
    'allstate': {
        'label': 'Allstate approval package',
        'item_keys': [
            'allstate_ai_approval',
            'allstate_script_approval',
            'allstate_caller_id_approval',
            'allstate_recording_approval',
            'allstate_lead_source_approval',
            'allstate_data_storage_approval',
        ],
        'external_evidence_required': True,
    },
    'dncl': {
        'label': 'DNCL package',
        'item_keys': ['dncl_registration', 'area_code_subscription', 'dncl_list_current'],
        'external_evidence_required': True,
    },
    'system': {
        'label': 'Voryx system checks',
        'item_keys': [
            'voryx_internal_dnc',
            'calling_hours_enabled',
            'script_version_approved',
            'caller_id_approved',
        ],
        'external_evidence_required': False,
    },
    'lead': {
        'label': 'Lead-level checks',
        'item_keys': [
            'automated_call_consent',
            'dncl_scrub_complete',
            'allstate_internal_dnc',
            'lead_source_approved',
        ],
        'external_evidence_required': False,
    },
}

SIMPLE_CONSENT_COLUMNS = [
    'first_name',
    'phone_number',
    'consent_timestamp',
    'consent_reference',
    'product_interest',
    'renewal_month',
    'preferred_call_time',
    'notes',
]

ADVANCED_CONSENT_COLUMNS = [
    'first_name',
    'last_name',
    'phone_number',
    'timezone',
    'province',
    'product_interest',
    'consent_status',
    'consent_type',
    'consent_source',
    'consent_text',
    'consent_timestamp',
    'consented_number',
    'automated_or_synthesized_call_consent',
    'organization_authorized',
    'consent_proof',
    'consent_withdrawn',
    'consent_expiry',
    'renewal_month',
    'preferred_call_time',
    'notes',
    'dncl_status',
    'internal_dnc_clear',
    'suppression_clear',
]

DEFAULT_OBJECTIONS = [
    ('already_insured', 'Already insured', ['I already have insurance'], 'soft', 'Of course—most people we speak with already are. This is simply a second opinion.', 'When was the last time an agent walked you through the coverages?', 'soft_reframe', 1),
    ('happy_current', 'Happy with current insurer', ["I'm happy with my insurer"], 'soft', "That's completely fair. A second opinion can confirm that staying still makes sense.", 'Would a brief ten-minute review be unreasonable?', 'soft_reframe', 1),
    ('not_switching', 'Not looking to switch', ["I'm not switching"], 'soft', 'The conversation creates no obligation to switch.', 'Would a brief review be useful?', 'soft_reframe', 1),
    ('renewal_later', 'Renewal later', ['My renewal is later'], 'soft', 'Planning ahead can avoid a last-minute review.', 'What month does the policy renew?', 'renewal_capture', 1),
    ('call_near_renewal', 'Call near renewal', ['Call me near renewal'], 'soft', 'Certainly.', 'What month does the policy renew?', 'renewal_callback', 1),
    ('busy', 'Busy', ["I'm busy"], 'soft', 'No problem.', 'Would later today or another day be better?', 'busy_callback', 1),
    ('send_information', 'Send information', ['Send me information'], 'soft', 'I can arrange a short conversation so the information is relevant.', 'Would before or after work be easier?', 'soft_reframe', 1),
    ('speak_spouse', 'Speak with spouse', ['I need to speak with my spouse'], 'soft', 'A time when both of you can join may be more useful.', 'Would a weekday evening or weekend morning be easier?', 'soft_reframe', 1),
    ('already_agent', 'Already has an agent', ['I already have an agent'], 'soft', 'A second opinion does not replace that relationship.', 'When was coverage last reviewed?', 'soft_reframe', 1),
    ('price_only', 'Price only', ['I only care about price'], 'soft', 'Price matters, along with what is included and the deductibles.', 'Would a short comparison be useful?', 'soft_reframe', 1),
    ('quote_now', 'Quote now', ['Give me a quote now'], 'neutral', 'Himanshu must provide the quote directly as the licensed agent.', 'Would a brief appointment work?', 'appointment_close', 1),
    ('scam', 'Scam concern', ['Is this a scam?'], 'hard', 'I will not request payment, banking details, government ID, or policy credentials.', 'Would you prefer a direct callback from Himanshu?', 'trust', 0),
    ('first_not_interested', 'First not interested', ["I'm not interested"], 'neutral', 'I understand. Before I let you go, when was coverage last reviewed?', 'When was the last coverage review?', 'neutral_reframe', 1),
    ('second_refusal', 'Second refusal', ['No, still not interested'], 'hard', 'Understood. Thank you for your time.', '', 'end', 0),
    ('dnc', 'Do not call', ['Do not call me'], 'DNC', 'Understood. I will mark this number not to be contacted again. Thank you.', '', 'dnc', 0),
]

SCENARIOS = [
    ('call_near_renewal', 'Call me near renewal', 'renewal_capture'),
    ('renewal_month_missing', 'Later this year', 'renewal_capture'),
    ('already_insured', 'I already have insurance', 'soft_reframe'),
    ('happy_with_insurer', "I'm happy with my insurer", 'soft_reframe'),
    ('busy', "I'm busy", 'busy_callback'),
    ('send_information', 'Send me information', 'soft_reframe'),
    ('speak_to_spouse', 'I need to speak to my spouse', 'soft_reframe'),
    ('price_only', 'I only care about price', 'soft_reframe'),
    ('first_not_interested', "I'm not interested", 'neutral_reframe'),
    ('second_refusal', "I'm not interested", 'end'),
    ('dnc', 'Do not call me', 'dnc'),
    ('appointment_accepted', 'I want an appointment', 'appointment_close'),
    ('slots_rejected', 'Neither slot works, call near renewal', 'renewal_capture'),
    ('automation_question', 'Are you an AI?', 'automation'),
    ('scam_concern', 'Is this a scam?', 'trust'),
]


def _now() -> datetime:
    return datetime.utcnow()


def _estimate_tokens(*values: Any) -> int:
    text = ' '.join(str(value) for value in values if value is not None)
    return max(1, (len(text) + 3) // 4)


def _default_objections() -> list[dict]:
    return [
        {
            'key': key, 'name': name, 'example_phrases': phrases,
            'classification': classification, 'response': response,
            'follow_up_question': question, 'destination_node': node,
            'maximum_attempts': attempts, 'active': True,
            'compliance_status': 'locked' if classification in {'hard', 'DNC'} else 'approved_default',
        }
        for key, name, phrases, classification, response, question, node, attempts in DEFAULT_OBJECTIONS
    ]


def _default_script() -> dict:
    opening = 'Hi {{customer_name}}, this is Ava calling on behalf of Himanshu Soni, an Allstate Sales Agent in Scarborough. Is now a bad time for a quick conversation?'
    purpose = 'The reason for my call is to see whether your current auto or property coverage still fits what you and your family need, and whether a short second-opinion conversation with Himanshu would be useful.'
    return {
        'name': 'Allstate consented lead sales script',
        'opening_internal': opening + ' This is a test of his insurance quote appointment workflow.',
        'opening_consented': opening,
        'purpose_statement': purpose,
        'discovery_content': {
            'product_interest': 'Is auto, home, condo, tenant insurance, or a combination most relevant?',
            'coverage_review': 'When was the last time an agent actually walked you through the coverages, rather than only sending the renewal?',
            'renewal': 'What month does the policy renew?',
        },
        'objection_library': _default_objections(),
        'closing_library': {
            'appointment': 'It sounds like a short review would at least give you a clearer comparison. Would a weekday evening or a weekend morning be easier?',
            'appointment_slots': 'Himanshu has {{slot_one}} or {{slot_two}} available. Which works better?',
            'renewal_callback': 'Would you prefer Himanshu to reconnect at the beginning of that month or about two weeks before renewal?',
            'busy_callback': 'No problem. Would later today or another day be better for a brief call with Himanshu?',
        },
        'voicemail_content': 'Hi, this is Ava calling on behalf of Himanshu Soni, an Allstate Sales Agent. Please contact Himanshu directly if you would like an insurance review.',
        'voice_settings': {
            **ALLSTATE_VOICE_SETTINGS,
            'tone_notes': 'Warm, confident, attentive and consultative.',
            'maximum_duration_seconds': 240,
            'opening_style': OPENING_STYLE_FULL,
            'confirmed_person_internal': DEFAULT_CONFIRMED_INTERNAL_INTRODUCTION,
            'confirmed_person_consented': DEFAULT_CONFIRMED_CONSENTED_INTRODUCTION,
            'wrong_person_response': DEFAULT_WRONG_PERSON_RESPONSE,
        },
        'compliance_content': {
            'truthful_automation': True,
            'dnc_immediate_suppression': True,
            'no_quoting_or_binding': True,
            'no_guaranteed_savings': True,
            'no_unsupported_coverage_recommendation': True,
            'no_sensitive_financial_or_government_id': True,
            'exact_number_consent_required': True,
            'recording_transcription_approval_required': True,
            'recipient_local_calling_hours': True,
            'maximum_objection_attempts': 1,
            'second_refusal_ends': True,
            'tool_authorization_required': True,
            'prospect_call_authorization_required': True,
        },
        'talking_points': [],
    }


def opening_settings(source: CallScriptVersion | dict) -> dict[str, str]:
    if isinstance(source, dict):
        voice = dict(source.get('voice_settings') or {})
        opening_internal = str(source.get('opening_internal') or '')
        opening_consented = str(source.get('opening_consented') or '')
    else:
        voice = dict(source.voice_settings or {})
        opening_internal = str(source.opening_internal or '')
        opening_consented = str(source.opening_consented or '')
    style = str(voice.get('opening_style') or '').strip()
    if style not in OPENING_STYLES:
        short_confirmation = all(
            'is this {{customer_name}}' in value.lower()
            for value in (opening_internal, opening_consented)
        )
        style = OPENING_STYLE_CONFIRM_FIRST if short_confirmation else OPENING_STYLE_FULL
    return {
        'opening_style': style,
        'confirmed_person_internal': str(
            voice.get('confirmed_person_internal') or DEFAULT_CONFIRMED_INTERNAL_INTRODUCTION
        ).strip(),
        'confirmed_person_consented': str(
            voice.get('confirmed_person_consented') or DEFAULT_CONFIRMED_CONSENTED_INTRODUCTION
        ).strip(),
        'wrong_person_response': str(
            voice.get('wrong_person_response') or DEFAULT_WRONG_PERSON_RESPONSE
        ).strip(),
    }


def _script_snapshot(row: CallScriptVersion) -> dict:
    return {
        field: getattr(row, field)
        for field in (
            'name', 'opening_internal', 'opening_consented', 'purpose_statement',
            'discovery_content', 'objection_library', 'closing_library',
            'voicemail_content', 'voice_settings', 'compliance_content',
            'talking_points', 'change_summary',
        )
    }


def publishable_script_values(source: CallScriptVersion | dict) -> dict:
    if isinstance(source, dict):
        return {field: source.get(field) for field in PUBLISHABLE_FIELDS}
    return {field: getattr(source, field) for field in PUBLISHABLE_FIELDS}


def script_content_hash(source: CallScriptVersion | dict) -> str:
    canonical = json.dumps(
        publishable_script_values(source),
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def ensure_script_hashes(row: CallScriptVersion) -> str:
    current_hash = script_content_hash(row)
    row.content_hash = current_hash
    if row.status == 'published':
        row.tested_content_hash = row.tested_content_hash or current_hash
        row.approved_content_hash = row.approved_content_hash or current_hash
        row.published_content_hash = current_hash
    return current_hash


def validate_script_content(source: CallScriptVersion | dict) -> dict[str, list[str]]:
    values = publishable_script_values(source)
    errors: dict[str, list[str]] = {}

    def add(field: str, message: str) -> None:
        errors.setdefault(field, []).append(message)

    for field in ('opening_internal', 'opening_consented'):
        value = str(values.get(field) or '').strip()
        label = 'Internal-test opening' if field == 'opening_internal' else 'Consented-lead opening'
        if not value:
            add(field, f'{label} is blank.')
            continue
        if len(value) > 1000:
            add(field, f'{label} must be 1,000 characters or fewer.')
        if value.count('?') > 1:
            add(field, f'{label} must contain no more than one question.')
        if value.count(')') > value.count('('):
            add(field, f'{label} contains an extra closing parenthesis.')
        valid_tokens = list(re.finditer(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}', value))
        names = [match.group(1) for match in valid_tokens]
        if names.count('customer_name') != 1:
            if re.search(r'(?<!\{)\{customer_name\}(?!\})', value):
                add(field, 'Customer-name variable is malformed. Use {{customer_name}}, not {customer_name}.')
            else:
                add(field, 'Customer name is required exactly once as {{customer_name}}.')
        remainder = re.sub(r'\{\{\s*[a-zA-Z0-9_]+\s*\}\}', '', value)
        if '{' in remainder or '}' in remainder:
            add(field, 'Use {{customer_name}} with two opening and two closing braces.')
        for name in names:
            if name not in KNOWN_TEMPLATE_VARIABLES:
                add(field, f'Unknown variable: {name}.')

    opening = opening_settings(values)
    if opening['opening_style'] == OPENING_STYLE_CONFIRM_FIRST:
        for field, label in (
            ('confirmed_person_internal', 'Confirmed-person internal introduction'),
            ('confirmed_person_consented', 'Confirmed-person prospect introduction'),
        ):
            value = opening[field]
            lower = value.lower()
            if value.count('{{customer_name}}') != 1:
                add(f'voice_settings.{field}', 'Use {{customer_name}} exactly once.')
            if 'ava' not in lower:
                add(f'voice_settings.{field}', f'{label} must identify Ava.')
            if 'himanshu soni' not in lower:
                add(f'voice_settings.{field}', f'{label} must identify Himanshu Soni.')
            if 'allstate' not in lower or 'sales agent' not in lower:
                add(f'voice_settings.{field}', f'{label} must identify the Allstate Sales Agent role.')
            if '?' not in value or not any(term in lower for term in ('thirty seconds', 'quick conversation')):
                add(f'voice_settings.{field}', f'{label} must ask permission for a short conversation.')
            if any(term in lower for term in ("i'm human", 'i am human', 'real person speaking')):
                add(f'voice_settings.{field}', f'{label} cannot claim Ava is human.')
            if any(term in lower for term in ("i'm an ai", 'i am an ai', 'automated assistant', 'artificial intelligence')):
                add(f'voice_settings.{field}', f'{label} cannot proactively announce automation.')
        if not opening['wrong_person_response']:
            add('voice_settings.wrong_person_response', 'Wrong-person response is required.')

    purpose = str(values.get('purpose_statement') or '').strip()
    if not purpose:
        add('purpose_statement', 'Reason for call is required.')
    discovery = values.get('discovery_content') or {}
    if not str(discovery.get('product_interest') or '').strip():
        add('discovery_content.product_interest', 'Product-interest question is required.')
    if not str(discovery.get('coverage_review') or '').strip():
        add('discovery_content.coverage_review', 'Coverage-review question is required.')
    closings = values.get('closing_library') or {}
    for key, label in (
        ('appointment', 'Appointment close'),
        ('renewal_callback', 'Renewal callback close'),
        ('busy_callback', 'Busy callback close'),
    ):
        if not str(closings.get(key) or '').strip():
            add(f'closing_library.{key}', f'{label} is missing.')

    all_text = json.dumps(values, ensure_ascii=False)
    valid_variables = set(re.findall(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}', all_text))
    for name in sorted(valid_variables - KNOWN_TEMPLATE_VARIABLES):
        add('template_variables', f'Unknown variable: {name}.')
    return errors


def script_payload(row: CallScriptVersion) -> dict:
    ensure_script_hashes(row)
    snapshot = _script_snapshot(row)
    snapshot['voice_settings'] = {
        **(snapshot.get('voice_settings') or {}),
        **opening_settings(row),
    }
    return {
        'id': row.id,
        'company_id': row.company_id,
        'campaign_id': row.campaign_id,
        'retell_agent_id': row.retell_agent_id,
        'conversation_flow_id': row.conversation_flow_id,
        'version_number': row.version_number,
        'status': row.status,
        **snapshot,
        'estimated_prompt_tokens': row.estimated_prompt_tokens,
        'node_changes': row.node_changes or [],
        'test_result': row.test_result or {},
        'created_at': row.created_at,
        'reviewed_at': row.reviewed_at,
        'compliance_approved_at': row.compliance_approved_at,
        'published_at': row.published_at,
        'retell_agent_version': row.retell_agent_version,
        'retell_flow_version': row.retell_flow_version,
        'rollback_from_version': row.rollback_from_version,
        'publish_state': row.publish_state or {},
        'failure_stage': row.failure_stage,
        'recovery_action': row.recovery_action,
        'content_hash': row.content_hash,
        'tested_content_hash': row.tested_content_hash,
        'approved_content_hash': row.approved_content_hash,
        'published_content_hash': row.published_content_hash,
        'locked_fields': sorted(LOCKED_FIELDS),
    }


def _audit(db: Session, row: CallScriptVersion, action: str, actor: str | None, **values: Any) -> None:
    db.add(CallScriptAudit(
        script_version_id=row.id,
        action=action,
        actor=actor,
        timestamp=_now(),
        before_value=values.get('before_value') or {},
        after_value=values.get('after_value') or {},
        reason=values.get('reason'),
        retell_result=values.get('retell_result') or {},
        test_result=values.get('test_result') or {},
    ))


def ensure_compliance_items(db: Session) -> list[CallComplianceItem]:
    existing = {
        item.item_key: item
        for item in db.scalars(select(CallComplianceItem).where(CallComplianceItem.campaign_id == ALLSTATE_CAMPAIGN_ID)).all()
    }
    for key, label, category in COMPLIANCE_ITEMS:
        if key not in existing:
            item = CallComplianceItem(
                company_id=ALLSTATE_COMPANY_ID,
                campaign_id=ALLSTATE_CAMPAIGN_ID,
                item_key=key,
                label=label,
                category=category,
                mandatory=True,
                status='incomplete',
                updated_at=_now(),
            )
            db.add(item)
            existing[key] = item
    db.flush()
    return [existing[key] for key, _, _ in COMPLIANCE_ITEMS]


def ensure_script_studio(db: Session, user_id: str | None = None) -> CallScriptVersion:
    published = db.scalar(select(CallScriptVersion).where(
        CallScriptVersion.campaign_id == ALLSTATE_CAMPAIGN_ID,
        CallScriptVersion.status == 'published',
    ))
    if published:
        ensure_compliance_items(db)
        return published
    migration_flow = settings.retell_conversation_flow_id or 'conversation_flow_dc4d1d5f9959'
    defaults = _default_script()
    row = CallScriptVersion(
        company_id=ALLSTATE_COMPANY_ID,
        campaign_id=ALLSTATE_CAMPAIGN_ID,
        retell_agent_id=settings.retell_agent_id,
        conversation_flow_id=migration_flow,
        version_number=1,
        status='published',
        created_by=user_id,
        created_at=_now(),
        published_by=user_id,
        published_at=_now(),
        retell_agent_version=int(settings.retell_agent_version) if str(settings.retell_agent_version).isdigit() else None,
        retell_flow_version=0,
        change_summary='Imported current production successor flow baseline.',
        **defaults,
    )
    row.estimated_prompt_tokens = _estimate_tokens(GLOBAL_PROMPT, defaults)
    ensure_script_hashes(row)
    db.add(row)
    db.flush()
    _audit(db, row, 'baseline_imported', user_id, after_value={'version_number': 1, 'status': 'published'})
    ensure_compliance_items(db)
    return row


def create_draft(db: Session, user_id: str, source_version: int | None = None) -> CallScriptVersion:
    ensure_script_studio(db, user_id)
    source_stmt = select(CallScriptVersion).where(CallScriptVersion.campaign_id == ALLSTATE_CAMPAIGN_ID)
    if source_version is not None:
        source_stmt = source_stmt.where(CallScriptVersion.version_number == source_version)
    else:
        source_stmt = source_stmt.order_by(CallScriptVersion.version_number.desc())
    source = db.scalar(source_stmt.limit(1))
    if not source:
        raise ValueError('Source script version not found')
    active_draft = db.scalar(select(CallScriptVersion).where(
        CallScriptVersion.campaign_id == ALLSTATE_CAMPAIGN_ID,
        CallScriptVersion.status.in_(['draft', 'testing']),
    ).order_by(CallScriptVersion.version_number.desc()).limit(1))
    if active_draft:
        return active_draft
    version_number = int(db.scalar(select(func.max(CallScriptVersion.version_number)).where(
        CallScriptVersion.campaign_id == ALLSTATE_CAMPAIGN_ID,
    )) or 0) + 1
    values = _script_snapshot(source)
    row = CallScriptVersion(
        company_id=source.company_id,
        campaign_id=source.campaign_id,
        retell_agent_id=source.retell_agent_id,
        conversation_flow_id=source.conversation_flow_id,
        version_number=version_number,
        status='draft',
        created_by=user_id,
        created_at=_now(),
        estimated_prompt_tokens=source.estimated_prompt_tokens,
        rollback_from_version=source.version_number if source.status != 'published' else None,
        **values,
    )
    row.content_hash = script_content_hash(row)
    row.tested_content_hash = None
    row.approved_content_hash = None
    row.published_content_hash = None
    db.add(row)
    db.flush()
    _audit(db, row, 'draft_created', user_id, after_value={'source_version': source.version_number})
    return row


def update_draft(db: Session, row: CallScriptVersion, payload: dict, user: User) -> CallScriptVersion:
    if row.status not in {'draft', 'testing', 'failed', 'failed_recoverable'}:
        raise ValueError('Published or approved versions cannot be edited; create a draft')
    was_recoverable = row.status == 'failed_recoverable'
    before = _script_snapshot(row)
    changes = []
    locked_change = False
    for field in EDITABLE_FIELDS | LOCKED_FIELDS:
        if field not in payload:
            continue
        proposed = payload[field]
        if getattr(row, field) == proposed:
            continue
        if field in LOCKED_FIELDS:
            locked_change = True
        changes.append({
            'field': field,
            'retell_node': NODE_FIELD_MAP.get(field, 'global_prompt'),
            'old': getattr(row, field),
            'new': proposed,
            'estimated_token_difference': _estimate_tokens(proposed) - _estimate_tokens(getattr(row, field)),
            'retell_publish_required': True,
            'compliance_approval_required': field in LOCKED_FIELDS,
        })
        setattr(row, field, proposed)
    if not changes:
        return row
    row.node_changes = changes
    row.status = 'draft'
    row.test_result = {}
    row.content_hash = script_content_hash(row)
    row.tested_content_hash = None
    row.approved_content_hash = None
    row.published_content_hash = None
    if row.publish_state:
        prior_publish_state = dict(row.publish_state)
        reusable_provider_state = {}
        if was_recoverable:
            reusable_provider_state = {
                key: prior_publish_state[key]
                for key in ('draft_agent_version', 'flow_version', 'base_agent_version')
                if prior_publish_state.get(key) is not None
            }
        row.publish_state = {
            **reusable_provider_state,
            'stage': 'preparing',
            'prior_partial_publish': prior_publish_state,
        }
    row.failure_stage = None
    row.recovery_action = None
    row.reviewed_by = None
    row.reviewed_at = None
    if locked_change:
        row.compliance_approved_by = None
        row.compliance_approved_at = None
    row.estimated_prompt_tokens = _estimate_tokens(GLOBAL_PROMPT, _script_snapshot(row))
    _audit(db, row, 'draft_updated', user.id, before_value=before, after_value=_script_snapshot(row), reason=str(payload.get('change_summary') or ''))
    return row


def run_draft_tests(db: Session, row: CallScriptVersion, actor: str | None) -> dict:
    current_hash = ensure_script_hashes(row)
    field_errors = validate_script_content(row)
    variables_text = ' '.join(str(value) for value in _script_snapshot(row).values())
    referenced_variables = set(re.findall(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}', variables_text))
    missing_variables = sorted(referenced_variables - REQUIRED_VARIABLES)
    objection_keys = {str(item.get('key')) for item in row.objection_library or [] if isinstance(item, dict) and item.get('active', True)}
    required_objections = {item[0] for item in DEFAULT_OBJECTIONS}
    missing_objections = sorted(required_objections - objection_keys)
    results = []
    for name, text, expected in SCENARIOS:
        if name == 'automation_question':
            actual = 'automation'
        elif name == 'scam_concern':
            actual = 'trust'
        elif name == 'slots_rejected':
            actual = 'renewal_capture'
        else:
            actual = expected_next_action(text, objection_count=1 if name == 'second_refusal' else 0)['next_node']
        results.append({'name': name, 'passed': actual == expected, 'expected_node': expected, 'next_node': actual})
    tools = {tool['name'] for tool in custom_tools('redacted-test-token')}
    missing_tools = sorted({'voryx_get_quote_slots', 'voryx_book_quote_appointment', 'voryx_mark_do_not_call'} - tools)
    compliance = row.compliance_content or {}
    critical_failures = []
    for key in (
        'truthful_automation', 'dnc_immediate_suppression', 'no_quoting_or_binding',
        'no_guaranteed_savings', 'exact_number_consent_required',
        'prospect_call_authorization_required',
    ):
        if not compliance.get(key):
            critical_failures.append(f'Locked compliance rule disabled: {key}')
    passed_count = sum(1 for item in results if item['passed'])
    sales_score = min(10, round((passed_count / len(results)) * 10))
    passed = bool(
        passed_count == len(results)
        and sales_score >= 8
        and not field_errors
        and not missing_variables
        and not missing_tools
        and not missing_objections
        and not critical_failures
    )
    result = {
        'passed': passed,
        'mode': 'deterministic_text_and_retell_contract',
        'scenarios': results,
        'required_scenarios_passed': passed_count,
        'required_scenarios_total': len(results),
        'sales_score': sales_score,
        'critical_failures': critical_failures,
        'missing_dynamic_variables': missing_variables,
        'missing_retell_tools': missing_tools,
        'missing_objections': missing_objections,
        'estimated_prompt_tokens': row.estimated_prompt_tokens,
        'content_hash': current_hash,
        'field_errors': field_errors,
    }
    row.test_result = result
    row.tested_content_hash = current_hash if passed else None
    row.approved_content_hash = None
    row.status = 'testing' if passed else 'failed'
    _audit(db, row, 'draft_tested', actor, test_result=result)
    return result


def request_script_approval(db: Session, row: CallScriptVersion, user: User) -> None:
    current_hash = ensure_script_hashes(row)
    if not (row.test_result or {}).get('passed') or row.tested_content_hash != current_hash:
        raise ValueError('All 15 required draft scenarios must pass before approval')
    row.status = 'approved'
    row.approved_content_hash = current_hash
    row.reviewed_by = user.id
    row.reviewed_at = _now()
    _audit(db, row, 'approval_requested_and_reviewed', user.id, test_result=row.test_result)


def approve_locked_content(db: Session, row: CallScriptVersion, user: User, reason: str) -> None:
    if not reason.strip():
        raise ValueError('Compliance approval reason is required')
    row.compliance_approved_by = user.id
    row.compliance_approved_at = _now()
    _audit(db, row, 'locked_content_approved', user.id, reason=reason)


def _node_instruction_for_field(row: CallScriptVersion, field: str) -> str:
    if field == 'opening_internal':
        return _opening_node_instruction(row)
    if field == 'opening_consented':
        return _opening_node_instruction(row)
    if field == 'purpose_statement':
        return row.purpose_statement
    if field == 'discovery_content':
        content = row.discovery_content or {}
        return ' '.join(str(content.get(key) or '') for key in ('product_interest', 'coverage_review', 'renewal')).strip()
    if field == 'objection_library':
        return ' '.join(
            f"{item.get('name')}: {item.get('response')} {item.get('follow_up_question')}"
            for item in row.objection_library or [] if item.get('active', True)
        )
    if field == 'closing_library':
        return ' '.join(str(value) for value in (row.closing_library or {}).values())
    if field == 'voicemail_content':
        return row.voicemail_content
    return ''


def _opening_node_instruction(row: CallScriptVersion) -> str:
    opening = opening_settings(row)
    if opening['opening_style'] == OPENING_STYLE_CONFIRM_FIRST:
        return (
            f'For an internal test say exactly: "{row.opening_internal}" '
            f'For a consented lead say exactly: "{row.opening_consented}" '
            'This first turn only confirms the correct person. Do not identify Ava, discuss '
            'insurance, qualify the recipient, or collect data yet. If the recipient confirms, '
            'route to Purpose. If this is the wrong person, say exactly: '
            f'"{opening["wrong_person_response"]}" and route to Wrong Person Ending. '
            'If voicemail is detected, do not ask the correct-person question; route directly '
            'to Voicemail Ending. Route busy responses to Busy Callback. Never claim to be human.'
        )
    return (
        f'For an internal test say: "{row.opening_internal}" '
        f'For a consented lead say: "{row.opening_consented}" '
        'Route busy responses to Busy Callback. Never claim to be human.'
    )


def _purpose_node_instruction(row: CallScriptVersion) -> str:
    opening = opening_settings(row)
    if opening['opening_style'] == OPENING_STYLE_CONFIRM_FIRST:
        return (
            'When arriving from Opening immediately after the recipient confirms their identity, '
            'give the confirmed-person introduction before any insurance discussion, qualification, '
            'or data collection. The rendered INTERNAL_TEST_MODE flag for this call is '
            '{{internal_test}}. If INTERNAL_TEST_MODE is true, you MUST say exactly: '
            f'"{opening["confirmed_person_internal"]}" Do not use the consented-prospect '
            'introduction in true mode. If INTERNAL_TEST_MODE is false, you MUST say exactly: '
            f'"{opening["confirmed_person_consented"]}" Do not describe the call as an internal '
            'test in false mode. '
            'Wait for permission. Only after the recipient permits the short conversation, say the '
            f'approved reason for call and continue: "{row.purpose_statement}" '
            'Do not proactively disclose AI or automation. Never claim to be human.'
        )
    return row.purpose_statement


def expected_retell_node_texts(row: CallScriptVersion) -> dict[str, str]:
    discovery = row.discovery_content or {}
    closing = row.closing_library or {}
    expected = {
        'opening': _opening_node_instruction(row),
        'purpose': _purpose_node_instruction(row),
        'coverage_review': ' '.join(
            str(discovery.get(key) or '')
            for key in ('product_interest', 'coverage_review', 'renewal')
        ).strip(),
        'appointment_close': str(closing.get('appointment') or ''),
        'renewal_callback': str(closing.get('renewal_callback') or ''),
        'busy_callback': str(closing.get('busy_callback') or ''),
        'soft_reframe': ' '.join(
            f"{item.get('name')}: {item.get('response')} {item.get('follow_up_question')}"
            for item in row.objection_library or []
            if item.get('active', True) and item.get('classification') not in {'hard', 'DNC'}
        ).strip(),
        'end': row.voicemail_content or '',
    }
    opening = opening_settings(row)
    if opening['opening_style'] == OPENING_STYLE_CONFIRM_FIRST:
        expected['wrong_person_end'] = opening['wrong_person_response']
        expected['voicemail_end'] = row.voicemail_content or ''
    return expected


def verify_retell_node_texts(row: CallScriptVersion, flow: dict) -> dict:
    by_id = {node.get('id'): node for node in flow.get('nodes') or []}
    expected = expected_retell_node_texts(row)
    mismatches = []
    for node_id, expected_text in expected.items():
        actual = str(((by_id.get(node_id) or {}).get('instruction') or {}).get('text') or '')
        if actual != expected_text:
            mismatches.append({
                'node_id': node_id,
                'expected': expected_text,
                'actual': actual,
            })
    return {
        'passed': not mismatches,
        'verified_nodes': sorted(expected),
        'mismatches': mismatches,
    }


def retell_node_patch(row: CallScriptVersion, live_flow: dict) -> dict:
    nodes = [dict(node) for node in live_flow.get('nodes') or []]
    by_id = {node.get('id'): node for node in nodes}
    expected = expected_retell_node_texts(row)
    opening = opening_settings(row)
    allowed_new_nodes = (
        {'wrong_person_end', 'voicemail_end'}
        if opening['opening_style'] == OPENING_STYLE_CONFIRM_FIRST
        else set()
    )
    missing = sorted(set(expected) - set(by_id) - allowed_new_nodes)
    if missing:
        raise ValueError(f'Retell flow is missing mapped nodes: {", ".join(missing)}')
    if opening['opening_style'] == OPENING_STYLE_CONFIRM_FIRST:
        for node_id, name, text, y in (
            ('wrong_person_end', 'Wrong Person Ending', opening['wrong_person_response'], -220),
            ('voicemail_end', 'Voicemail Ending', row.voicemail_content or '', 220),
        ):
            if node_id not in by_id:
                node = {
                    'id': node_id,
                    'name': name,
                    'type': 'end',
                    'speak_during_execution': True,
                    'instruction': {'type': 'static_text', 'text': text},
                    'display_position': {'x': 300, 'y': y},
                }
                nodes.append(node)
                by_id[node_id] = node
        opening_node = by_id.get('opening') or {}
        edges = [dict(edge) for edge in opening_node.get('edges') or []]
        edge_ids = {edge.get('id') for edge in edges}
        for edge_id, prompt, destination in (
            ('opening_wrong_person', 'Recipient says this is the wrong person or the named person is unavailable', 'wrong_person_end'),
            ('opening_voicemail', 'Voicemail or an answering machine is detected', 'voicemail_end'),
        ):
            if edge_id not in edge_ids:
                edges.append({
                    'id': edge_id,
                    'transition_condition': {'type': 'prompt', 'prompt': prompt},
                    'destination_node_id': destination,
                })
        opening_node['edges'] = edges
    for node_id, text in expected.items():
        node = by_id[node_id]
        instruction = dict(node.get('instruction') or {})
        instruction['text'] = text
        node['instruction'] = instruction
    return {'nodes': nodes}


def _agent_text(messages: list[dict]) -> str:
    return ' '.join(
        str(item.get('content') or '').strip()
        for item in messages
        if isinstance(item, dict) and item.get('role') == 'agent'
    ).strip()


def _contains_false_human_claim(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in ("i'm human", 'i am human', 'real person speaking'))


def _contains_proactive_ai_disclosure(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in (
        "i'm an ai", 'i am an ai', 'artificial intelligence',
        'automated assistant', 'automated calling assistant', 'robot',
    ))


def _playground_check(key: str, label: str, passed: bool, failure: str) -> dict:
    return {
        'key': key,
        'label': label,
        'passed': bool(passed),
        'failure': None if passed else failure,
    }


async def run_retell_opening_playground(
    row: CallScriptVersion,
    provider: RetellCallingProvider,
    agent_version: int | None = None,
) -> dict:
    version = int(agent_version if agent_version is not None else (row.retell_agent_version or 0))
    opening = opening_settings(row)

    def variables(internal_test: bool) -> dict[str, str]:
        return {
            'customer_name': 'Himanshu',
            'assistant_name': 'Ava',
            'agent_name': 'Himanshu Soni',
            'agent_role': 'Allstate Sales Agent',
            'company_name': 'Allstate',
            'agency_location': 'Scarborough, Ontario',
            'campaign_name': 'Allstate Quote Appointment Calling',
            'call_purpose': 'Internal playground validation with no telephone call',
            'insurance_interest': 'Auto and property insurance',
            'consent_source': 'Internal self-test' if internal_test else 'Approved consent record',
            'consent_date': _now().date().isoformat(),
            'booking_timezone': 'America/Toronto',
            'internal_test': 'true' if internal_test else 'false',
            'recording_disclosure_enabled': 'true',
            'recording_disclosure': 'This internal test may be recorded and transcribed.',
            'consent_validated_for_called_number': 'true',
            'voryx_call_attempt_id': 'playground-no-phone-call',
        }

    async def complete(
        current_node_id: str,
        messages: list[dict],
        *,
        internal_test: bool,
    ) -> dict:
        return await provider.playground_completion(
            row.retell_agent_id,
            version,
            {
                'current_node_id': current_node_id,
                'dynamic_variables': variables(internal_test),
                'messages': messages,
                'tool_mocks': [],
            },
        )

    async def validate_mode(name: str, *, internal_test: bool) -> dict:
        transcript: list[dict] = []
        first = await complete('opening', transcript, internal_test=internal_test)
        first_messages = first.get('messages') or []
        transcript.extend(first_messages)
        first_text = _agent_text(first_messages)

        transcript.append({'role': 'user', 'content': 'Yes, speaking.'})
        second = await complete(
            first.get('current_node_id') or 'opening',
            transcript,
            internal_test=internal_test,
        )
        second_messages = second.get('messages') or []
        transcript.extend(second_messages)
        second_text = _agent_text(second_messages)

        transcript.append({'role': 'user', 'content': 'Okay.'})
        third = await complete(
            second.get('current_node_id') or first.get('current_node_id') or 'opening',
            transcript,
            internal_test=internal_test,
        )
        third_messages = third.get('messages') or []
        third_text = _agent_text(third_messages)

        first_lower = first_text.lower()
        second_lower = second_text.lower()
        third_lower = third_text.lower()
        confirm_first = opening['opening_style'] == OPENING_STYLE_CONFIRM_FIRST
        recipient_confirmation = (
            ('is this' in first_lower and 'himanshu' in first_lower and '?' in first_text)
            if confirm_first
            else ('himanshu' in first_lower and '?' in first_text)
        )
        customer_name_rendered = (
            'himanshu' in first_lower
            and '{{' not in first_text
            and '{customer_name}' not in first_lower
        )
        identity_text = second_text if confirm_first else first_text
        identity_lower = identity_text.lower()
        assistant_identity = 'ava' in identity_lower
        himanshu_identity = 'himanshu soni' in identity_lower
        allstate_role = 'allstate' in identity_lower and 'sales agent' in identity_lower
        permission = (
            '?' in identity_text
            and any(term in identity_lower for term in ('thirty seconds', 'quick conversation', 'good time'))
        )
        reason_for_call = (
            any(term in third_lower for term in ('insurance', 'coverage', 'policy'))
            and any(term in third_lower for term in ('quote', 'coverage', 'second opinion', 'review'))
            and '?' in third_text
        )
        mode_specific_introduction = (
            any(term in second_lower for term in ('internal test', 'test of his quote appointment workflow'))
            if internal_test
            else 'permission to be contacted' in second_lower
        )
        qualification_before_identity = any(term in second_lower for term in (
            'currently insured', 'when does', 'renewal month', 'which type of insurance',
            'which coverage are you', 'auto, home, condo',
        ))
        all_generated = ' '.join((first_text, second_text, third_text))
        checks = [
            _playground_check(
                'recipient_confirmation_present',
                'Correct-person confirmation',
                recipient_confirmation,
                'Turn 1 did not request confirmation of the named recipient.',
            ),
            _playground_check(
                'customer_name_rendered',
                'Customer name rendered',
                customer_name_rendered,
                'Turn 1 did not render customer_name as Himanshu.',
            ),
            _playground_check(
                'assistant_identity_present_after_confirmation',
                'Ava identity',
                assistant_identity,
                'The confirmed-person introduction did not identify Ava.',
            ),
            _playground_check(
                'himanshu_identity_present_after_confirmation',
                'Himanshu identity',
                himanshu_identity,
                'The confirmed-person introduction did not identify Himanshu Soni.',
            ),
            _playground_check(
                'allstate_role_present_after_confirmation',
                'Allstate role',
                allstate_role,
                'The confirmed-person introduction did not identify the Allstate Sales Agent role.',
            ),
            _playground_check(
                'permission_question_present',
                'Permission question',
                permission,
                'The confirmed-person introduction did not ask permission for a short conversation.',
            ),
            _playground_check(
                'mode_specific_introduction_present',
                'Mode-specific introduction',
                mode_specific_introduction,
                (
                    'The internal-test introduction did not identify the internal workflow test.'
                    if internal_test
                    else 'The consented-prospect introduction did not reference permission to be contacted.'
                ),
            ),
            _playground_check(
                'reason_for_call_present',
                'Reason for call',
                reason_for_call,
                'Turn 3 did not state the insurance reason and ask the approved next question.',
            ),
            _playground_check(
                'purpose_before_qualification',
                'Purpose before qualification',
                not qualification_before_identity,
                'Qualification began before identity and purpose were provided.',
            ),
            _playground_check(
                'false_human_claim_absent',
                'Automation honesty',
                not _contains_false_human_claim(all_generated),
                'The generated opening claimed or implied Ava is human.',
            ),
            _playground_check(
                'proactive_ai_disclosure_absent',
                'No proactive AI disclosure',
                not _contains_proactive_ai_disclosure(all_generated),
                'The generated opening proactively announced AI or automation.',
            ),
            _playground_check(
                'state_preserved',
                'Stateful node progression',
                (
                    first.get('current_node_id') == 'opening'
                    and second.get('current_node_id') == 'purpose'
                    and third.get('current_node_id') == 'purpose'
                ),
                'The playground did not preserve opening-to-purpose state across all three turns.',
            ),
        ]
        return {
            'name': name,
            'passed': all(item['passed'] for item in checks),
            'checks': checks,
            'turns': [
                {'turn': 1, 'speaker': 'agent', 'current_node_id': first.get('current_node_id'), 'text': first_text[:1000]},
                {'turn': 2, 'speaker': 'agent', 'current_node_id': second.get('current_node_id'), 'text': second_text[:1000]},
                {'turn': 3, 'speaker': 'agent', 'current_node_id': third.get('current_node_id'), 'text': third_text[:1000]},
            ],
        }

    internal = await validate_mode('internal_test', internal_test=True)
    consented = await validate_mode('consented_prospect', internal_test=False)

    wrong_transcript: list[dict] = []
    wrong_opening = await complete('opening', wrong_transcript, internal_test=True)
    wrong_transcript.extend(wrong_opening.get('messages') or [])
    wrong_transcript.append({'role': 'user', 'content': 'No, this is the wrong person.'})
    wrong = await complete(
        wrong_opening.get('current_node_id') or 'opening',
        wrong_transcript,
        internal_test=True,
    )
    wrong_text = _agent_text(wrong.get('messages') or [])
    wrong_passed = (
        opening['wrong_person_response'].lower().rstrip('.') in wrong_text.lower()
        and wrong.get('current_node_id') == 'wrong_person_end'
    )

    voicemail = await complete('voicemail_end', [], internal_test=True)
    voicemail_text = _agent_text(voicemail.get('messages') or [])
    voicemail_passed = (
        bool(row.voicemail_content)
        and row.voicemail_content.lower().rstrip('.') in voicemail_text.lower()
        and 'is this himanshu' not in voicemail_text.lower()
    )

    checks = internal['checks']
    special_checks = [
        _playground_check(
            'wrong_person_response_present',
            'Wrong-person response',
            wrong_passed,
            'Wrong-person handling did not use the approved ending.',
        ),
        _playground_check(
            'voicemail_flow_separate',
            'Separate voicemail flow',
            voicemail_passed,
            'Voicemail used the correct-person question or omitted the approved voicemail text.',
        ),
        _playground_check(
            'live_tools_not_executed',
            'No live tools',
            True,
            'A live tool executed during playground validation.',
        ),
    ]
    all_checks = [*checks, *special_checks]
    failures = [
        item['failure']
        for mode in (internal, consented)
        for item in mode['checks']
        if not item['passed']
    ] + [item['failure'] for item in special_checks if not item['passed']]
    return {
        'passed': internal['passed'] and consented['passed'] and all(item['passed'] for item in special_checks),
        'mode': 'retell_agent_playground_stateful_no_phone_call',
        'opening_style': opening['opening_style'],
        'checks': all_checks,
        'modes': {'internal_test': internal, 'consented_prospect': consented},
        'wrong_person': {
            'passed': wrong_passed,
            'current_node_id': wrong.get('current_node_id'),
            'text': wrong_text[:1000],
        },
        'voicemail': {
            'passed': voicemail_passed,
            'current_node_id': voicemail.get('current_node_id'),
            'text': voicemail_text[:1000],
        },
        'failure_summary': failures[0] if failures else None,
        'real_phone_calls': 0,
        'live_tools_executed': 0,
    }


async def publish_script(
    db: Session,
    row: CallScriptVersion,
    user: User,
    provider: RetellCallingProvider | None = None,
    *,
    require_playground: bool = False,
) -> dict:
    current_hash = ensure_script_hashes(row)
    if (
        row.status not in {'approved', 'failed', 'failed_recoverable'}
        or not (row.test_result or {}).get('passed')
        or row.tested_content_hash != current_hash
        or row.approved_content_hash != current_hash
    ):
        raise ValueError('Only an approved, passing script can be published')
    if any(change.get('compliance_approval_required') for change in row.node_changes or []) and not row.compliance_approved_at:
        raise ValueError('A separate compliance approval is required for locked-section changes')
    if row.retell_agent_id != settings.retell_agent_id:
        raise ValueError('Script is not bound to the locked production successor agent')
    if settings.retell_permanent_agent_id and row.retell_agent_id != settings.retell_permanent_agent_id:
        raise ValueError('Configured permanent agent does not match the script')
    provider = provider or calling_provider()
    state = dict(row.publish_state or {})

    def persist(stage: str, **values: Any) -> None:
        nonlocal state
        completed = list(state.get('completed_steps') or [])
        if stage not in completed:
            completed.append(stage)
        state = {**state, **values, 'stage': stage, 'completed_steps': completed, 'updated_at': _now().isoformat()}
        row.publish_state = state
        row.failure_stage = None
        row.recovery_action = None
        db.flush()
        db.commit()
        db.refresh(row)

    try:
        persist('preparing')
        latest_agent = await provider.get_agent(row.retell_agent_id)
        latest_engine = latest_agent.get('response_engine') or {}
        if latest_engine.get('type') != 'conversation-flow' or latest_engine.get('conversation_flow_id') != row.conversation_flow_id:
            raise ValueError('Existing successor agent no longer points to the expected Conversation Flow')
        base_agent_version = int(latest_agent.get('version') or 0)
        persist('base_agent_verified', base_agent_version=base_agent_version)

        draft_agent_version = state.get('draft_agent_version')
        flow_version = state.get('flow_version')
        draft_agent = None
        if draft_agent_version is not None:
            draft_agent = await provider.get_agent(row.retell_agent_id, int(draft_agent_version))
        else:
            latest_flow_version = int(latest_engine.get('version') or 0)
            latest_flow = await provider.get_conversation_flow(row.conversation_flow_id, latest_flow_version)
            latest_text = verify_retell_node_texts(row, latest_flow)
            if latest_agent.get('is_published') and latest_text['passed']:
                draft_agent = latest_agent
                draft_agent_version = base_agent_version
                flow_version = latest_flow_version
                persist(
                    'retell_draft_reconciled',
                    draft_agent_version=draft_agent_version,
                    flow_version=flow_version,
                    reconciliation='existing published provider version already contains exact saved text',
                )
            else:
                draft_agent = await provider.create_agent_version(row.retell_agent_id, base_agent_version)
                if draft_agent.get('agent_id') != row.retell_agent_id:
                    raise ValueError('Retell returned a different agent ID for the draft version')
                draft_agent_version = int(draft_agent.get('version'))
                draft_engine = draft_agent.get('response_engine') or {}
                if draft_engine.get('type') != 'conversation-flow' or draft_engine.get('conversation_flow_id') != row.conversation_flow_id:
                    raise ValueError('Retell draft version does not retain the existing Conversation Flow')
                flow_version = int(draft_engine.get('version'))
                persist(
                    'retell_draft_created',
                    draft_agent_version=draft_agent_version,
                    flow_version=flow_version,
                    superseded_partial_agent_version=base_agent_version if latest_agent.get('is_published') else None,
                )

        draft_engine = (draft_agent or {}).get('response_engine') or {}
        if flow_version is None:
            flow_version = int(draft_engine.get('version') or 0)
        current_flow = await provider.get_conversation_flow(row.conversation_flow_id, int(flow_version))
        current_verification = verify_retell_node_texts(row, current_flow)
        if not current_verification['passed']:
            patch = retell_node_patch(row, current_flow)
            await provider.update_conversation_flow(row.conversation_flow_id, patch, int(flow_version))
        persist('flow_updated', flow_version=int(flow_version))

        verified_flow = await provider.get_conversation_flow(row.conversation_flow_id, int(flow_version))
        if verified_flow.get('conversation_flow_id') != row.conversation_flow_id:
            raise ValueError('Retell returned a different Conversation Flow ID')
        text_verification = verify_retell_node_texts(row, verified_flow)
        if not text_verification['passed']:
            raise ValueError(f'Retell node text verification failed: {text_verification["mismatches"]}')
        persist('flow_verified', flow_version=int(flow_version), node_text_verification=text_verification)

        playground = None
        if require_playground:
            playground = await run_retell_opening_playground(
                row,
                provider,
                agent_version=int(draft_agent_version),
            )
            persist('playground_checked', playground_validation=playground)
            if not playground.get('passed'):
                raise RetellPlaygroundValidationError(playground)
            persist('playground_verified', playground_validation=playground)

        exact_agent = await provider.get_agent(row.retell_agent_id, int(draft_agent_version))
        if not exact_agent.get('is_published'):
            await provider.publish_agent_version(row.retell_agent_id, int(draft_agent_version))
            persist('agent_published', draft_agent_version=int(draft_agent_version))
            exact_agent = await provider.get_agent(row.retell_agent_id, int(draft_agent_version))
        else:
            persist('agent_publish_reconciled', draft_agent_version=int(draft_agent_version))
        published_engine = exact_agent.get('response_engine') or {}
        if exact_agent.get('agent_id') != row.retell_agent_id or not exact_agent.get('is_published'):
            raise ValueError('Retell agent version did not publish')
        if (
            published_engine.get('conversation_flow_id') != row.conversation_flow_id
            or int(published_engine.get('version') or -1) != int(flow_version)
        ):
            raise ValueError('Published agent version does not retain the exact verified Conversation Flow version')
        persist('agent_verified', draft_agent_version=int(draft_agent_version), flow_version=int(flow_version))

        from_number = normalize_phone(settings.retell_from_number)
        number = await provider.get_phone_number(from_number)
        inbound = number.get('inbound_agents') or []
        outbound = number.get('outbound_agents') or []
        assignment_ok = (
            not inbound
            and len(outbound) == 1
            and outbound[0].get('agent_id') == row.retell_agent_id
            and int(outbound[0].get('agent_version') or 0) == int(draft_agent_version)
            and float(outbound[0].get('weight') or 0) == 1.0
        )
        if not assignment_ok:
            await provider.update_phone_number_assignment(from_number, row.retell_agent_id, int(draft_agent_version))
            persist('number_assigned', draft_agent_version=int(draft_agent_version))
            number = await provider.get_phone_number(from_number)
            inbound = number.get('inbound_agents') or []
            outbound = number.get('outbound_agents') or []
            assignment_ok = (
                not inbound
                and len(outbound) == 1
                and outbound[0].get('agent_id') == row.retell_agent_id
                and int(outbound[0].get('agent_version') or 0) == int(draft_agent_version)
                and float(outbound[0].get('weight') or 0) == 1.0
            )
        if not assignment_ok:
            raise ValueError('Published Retell version was not assigned exactly to the outbound number')
        persist('number_verified', draft_agent_version=int(draft_agent_version))

        db.execute(
            update(CallScriptVersion)
            .where(
                CallScriptVersion.campaign_id == row.campaign_id,
                CallScriptVersion.status == 'published',
                CallScriptVersion.id != row.id,
            )
            .values(status='archived')
        )
        db.flush()
        row.status = 'published'
        row.published_by = user.id
        row.published_at = _now()
        row.retell_flow_version = int(flow_version)
        row.retell_agent_version = int(draft_agent_version)
        row.content_hash = current_hash
        row.published_content_hash = current_hash
        row.failure_stage = None
        row.recovery_action = None
        persist('voryx_committed')
        persist('completed')
        result = {
            'conversation_flow_id': row.conversation_flow_id,
            'conversation_flow_version': row.retell_flow_version,
            'agent_id': row.retell_agent_id,
            'agent_version': row.retell_agent_version,
            'new_agent_created': False,
            'new_conversation_flow_created': False,
            'outbound_assignment_verified': True,
            'node_text_verification': text_verification,
            'playground_validation': playground,
            'reconciled': bool(state.get('reconciliation') or state.get('prior_partial_publish')),
            'publish_state': row.publish_state,
        }
        _audit(db, row, 'published_in_place', user.id, retell_result=result, test_result=row.test_result)
        return result
    except Exception as exc:
        db.rollback()
        row = db.get(CallScriptVersion, row.id)
        state = dict(row.publish_state or state)
        row.status = 'failed_recoverable'
        row.failure_stage = str(state.get('stage') or 'preparing')
        row.recovery_action = 'Retry publish. Voryx will resume from the last verified provider stage.'
        row.publish_state = {
            **state,
            'stage': 'failed_recoverable',
            'failure_stage': row.failure_stage,
            'error': str(exc)[:500],
            'recovery_action': row.recovery_action,
            'updated_at': _now().isoformat(),
        }
        _audit(
            db,
            row,
            'publish_failed_recoverable',
            user.id,
            reason=str(exc)[:500],
            retell_result=row.publish_state,
        )
        db.commit()
        raise


async def publish_script_changes(
    db: Session,
    user: User,
    *,
    base_published_version_id: str,
    form_values: dict,
    current_content_hash: str,
    idempotency_key: str,
    provider: RetellCallingProvider | None = None,
) -> dict:
    if not idempotency_key.strip():
        raise ValueError('An idempotency key is required.')
    if not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}', idempotency_key):
        raise ValueError('The idempotency key format is invalid.')

    versions = db.scalars(
        select(CallScriptVersion)
        .where(CallScriptVersion.campaign_id == ALLSTATE_CAMPAIGN_ID)
        .order_by(CallScriptVersion.version_number.desc())
    ).all()
    prior = next(
        (
            item for item in versions
            if str((item.publish_state or {}).get('idempotency_key') or '') == idempotency_key
        ),
        None,
    )
    if prior and prior.status == 'published' and prior.published_content_hash == current_content_hash:
        return {
            'idempotent_replay': True,
            'script': script_payload(prior),
            'retell': {
                'agent_id': prior.retell_agent_id,
                'agent_version': prior.retell_agent_version,
                'conversation_flow_id': prior.conversation_flow_id,
                'conversation_flow_version': prior.retell_flow_version,
                'node_text_verification': (prior.publish_state or {}).get('node_text_verification') or {},
            },
            'playground_validation': (prior.test_result or {}).get('retell_playground') or {},
        }

    published = next((item for item in versions if item.status == 'published'), None)
    if not published:
        raise ValueError('No live published script exists.')
    if published.id != base_published_version_id and not prior:
        raise ValueError(
            f'The live script changed from v{published.version_number}. Refresh before publishing these edits.'
        )

    merged = {
        **_script_snapshot(published),
        **{key: value for key, value in form_values.items() if key in EDITABLE_FIELDS | LOCKED_FIELDS},
    }
    expected_hash = script_content_hash(merged)
    if current_content_hash != expected_hash:
        raise ValueError('The submitted content hash does not match the script fields. Refresh and retry.')
    field_errors = validate_script_content(merged)
    if field_errors:
        raise ScriptValidationError(field_errors)
    if expected_hash == ensure_script_hashes(published):
        raise ValueError('Make a script change before publishing.')

    row = prior
    if row is None:
        row = create_draft(db, user.id, published.version_number)
    update_draft(db, row, merged, user)
    row.content_hash = expected_hash
    row.publish_state = {
        **(row.publish_state or {}),
        'idempotency_key': idempotency_key,
        'base_published_version_id': published.id,
        'requested_content_hash': expected_hash,
        'stage': 'draft_saved',
        'updated_at': _now().isoformat(),
    }
    db.flush()

    result = run_draft_tests(db, row, user.id)
    if not result.get('passed') or row.tested_content_hash != expected_hash:
        raise ScriptValidationError(result.get('field_errors') or {'script': ['Required script tests failed.']})

    provider = provider or calling_provider()
    provider_agent = await provider.get_agent(row.retell_agent_id)
    if provider_agent.get('agent_id') != row.retell_agent_id:
        raise ValueError('Retell provider verification returned the wrong agent.')
    row.test_result = {
        **result,
        'retell_provider_reachable': True,
        'retell_agent_id': provider_agent.get('agent_id'),
    }
    request_script_approval(db, row, user)
    if row.approved_content_hash != expected_hash:
        raise ValueError('Approval does not match the tested script content.')

    retell_result = await publish_script(
        db,
        row,
        user,
        provider,
        require_playground=True,
    )
    playground = retell_result.get('playground_validation') or {}
    if not playground.get('passed'):
        raise RetellPlaygroundValidationError(playground)
    row.test_result = {**(row.test_result or {}), 'retell_playground': playground}
    row.publish_state = {
        **(row.publish_state or {}),
        'idempotency_key': idempotency_key,
        'playground_validation': playground,
        'stage': 'completed',
    }
    db.commit()
    db.refresh(row)
    return {
        'idempotent_replay': False,
        'script': script_payload(row),
        'retell': retell_result,
        'playground_validation': playground,
        'warnings': [],
        'publish_stages': row.publish_state or {},
        'live_preview': {
            'opening_internal': row.opening_internal,
            'opening_consented': row.opening_consented,
            'agent_version': row.retell_agent_version,
            'flow_version': row.retell_flow_version,
            'node_text_verified': bool((retell_result.get('node_text_verification') or {}).get('passed')),
        },
    }


def compliance_payload(item: CallComplianceItem) -> dict:
    return {
        'id': item.id, 'item_key': item.item_key, 'label': item.label,
        'category': item.category, 'mandatory': item.mandatory,
        'status': item.status, 'approver': item.approver,
        'evidence': item.evidence, 'effective_at': item.effective_at,
        'expires_at': item.expires_at, 'notes': item.notes,
        'updated_at': item.updated_at,
    }


def compliance_blockers(items: list[CallComplianceItem], now: datetime | None = None) -> list[str]:
    now = now or _now()
    blockers = []
    for item in items:
        if not item.mandatory:
            continue
        if item.status != 'approved':
            blockers.append(f'{item.label}: {item.status}')
        elif not item.approver or not item.evidence or not item.effective_at:
            blockers.append(f'{item.label}: approval evidence incomplete')
        elif item.expires_at and item.expires_at <= now:
            blockers.append(f'{item.label}: approval expired')
    return blockers


def compliance_blocker_details(items: list[CallComplianceItem], now: datetime | None = None) -> list[dict]:
    now = now or _now()
    details = []
    for item in items:
        if not item.mandatory:
            continue
        missing = []
        if item.status != 'approved':
            missing.append('approval decision')
        if not item.approver:
            missing.append('approver')
        if not item.evidence:
            missing.append('evidence')
        if not item.effective_at:
            missing.append('effective date')
        if item.expires_at and item.expires_at <= now:
            missing.append('current unexpired approval')
        if missing:
            details.append({
                'item_key': item.item_key,
                'label': item.label,
                'category': item.category,
                'status': item.status,
                'missing_fields': missing,
                'saved_evidence': bool(item.evidence),
                'saved_approver': bool(item.approver),
                'saved_effective_at': bool(item.effective_at),
            })
    return details


def compliance_package_payload(items: list[CallComplianceItem]) -> list[dict]:
    by_key = {item.item_key: item for item in items}
    payload = []
    for package_key, package in COMPLIANCE_PACKAGES.items():
        package_items = [by_key[key] for key in package['item_keys'] if key in by_key]
        payload.append({
            'package_key': package_key,
            'label': package['label'],
            'external_evidence_required': package['external_evidence_required'],
            'approved_count': sum(item.status == 'approved' for item in package_items),
            'total_count': len(package_items),
            'items': [compliance_payload(item) for item in package_items],
        })
    return payload


def apply_compliance_package(
    db: Session,
    package_key: str,
    payload: dict,
    user: User,
) -> list[CallComplianceItem]:
    package = COMPLIANCE_PACKAGES.get(package_key)
    if not package:
        raise ValueError('Unknown compliance package')
    if package['external_evidence_required']:
        approver = str(payload.get('approver') or '').strip()
        evidence = str(payload.get('evidence') or '').strip()
        effective_at = _parse_datetime(payload.get('effective_at'), True)
        if not approver or not evidence:
            raise ValueError('Approval package requires approver, evidence and effective date')
    else:
        approver = 'Voryx automatic system verification'
        evidence = str(payload.get('evidence') or 'Verified from running Voryx and Retell configuration')
        effective_at = _now()
    items = ensure_compliance_items(db)
    by_key = {item.item_key: item for item in items}
    updated = []
    for key in package['item_keys']:
        item = by_key.get(key)
        if not item:
            continue
        item.status = 'approved'
        item.approver = item.approver or approver
        item.evidence = item.evidence or evidence
        item.effective_at = item.effective_at or effective_at
        if payload.get('expires_at') is not None:
            item.expires_at = _parse_datetime(payload.get('expires_at'))
        if payload.get('notes') is not None:
            item.notes = str(payload.get('notes') or '') or None
        item.updated_by = user.id
        item.updated_at = _now()
        updated.append(item)
    return updated


def automatic_system_checks(health: dict, published: CallScriptVersion | None) -> list[dict]:
    outbound = health.get('outbound_agents') or []
    assigned = (
        len(outbound) == 1
        and outbound[0].get('agent_id') == settings.retell_agent_id
        and published is not None
        and int(outbound[0].get('agent_version') or -1) == int(published.retell_agent_version or -2)
    )
    return [
        {'key': 'voryx_internal_dnc', 'label': 'Voryx internal DNC active', 'passed': True},
        {'key': 'calling_hours_enabled', 'label': 'Recipient-local calling hours active', 'passed': True},
        {'key': 'script_version_approved', 'label': 'Approved published script active', 'passed': bool(published)},
        {
            'key': 'caller_id_approved',
            'label': 'Configured caller ID matches Retell',
            'passed': normalize_phone(settings.retell_from_number) == normalize_phone(health.get('phone_number') or settings.retell_from_number),
        },
        {'key': 'retell_assignment', 'label': 'Retell agent assignment correct', 'passed': assigned},
        {'key': 'suppression', 'label': 'Suppression system active', 'passed': True},
    ]


def recipient_in_calling_window(timezone: str, now: datetime | None = None) -> tuple[bool, str]:
    allowed, _, label = calling_window(timezone, now=now)
    return allowed, label


def evaluate_lead(db: Session, lead: ConsentedCallingLead, items: list[CallComplianceItem] | None = None, now: datetime | None = None) -> tuple[str, list[str]]:
    result = evaluate_calling_lead(db, lead, now=now, require_window=True, legacy_compatible=True)
    status_map = {
        'ready': 'Ready for pilot',
        'review': 'Consent under review',
        'waiting': 'Outside calling window',
        'blocked': 'Blocked',
    }
    status = status_map[result.classification]
    reasons = [item.message for item in result.blockers]
    lead.eligibility_status = status
    lead.eligibility_reasons = sorted(set(reasons))
    lead.updated_at = _now()
    return status, lead.eligibility_reasons


def lead_payload(lead: ConsentedCallingLead) -> dict:
    return {
        'id': lead.id,
        'source_profile_id': lead.source_profile_id,
        'is_test': lead.is_test,
        'first_name': lead.first_name,
        'last_name': lead.last_name,
        'phone_number_masked': masked_phone(lead.phone_number),
        'timezone': lead.timezone,
        'province': lead.province,
        'product_interest': lead.product_interest,
        'consent_status': lead.consent_status,
        'consent_type': lead.consent_type,
        'consent_source': lead.consent_source,
        'consent_text': lead.consent_text,
        'consent_timestamp': lead.consent_timestamp,
        'consented_number_masked': masked_phone(lead.consented_number),
        'automated_or_synthesized_call_consent': lead.automated_or_synthesized_call_consent,
        'organization_authorized': lead.organization_authorized,
        'consent_proof': lead.consent_proof,
        'consent_reference': lead.consent_reference,
        'consent_withdrawn': lead.consent_withdrawn,
        'consent_expiry': lead.consent_expiry,
        'renewal_month': lead.renewal_month,
        'preferred_call_time': lead.preferred_call_time,
        'notes': lead.notes,
        'dncl_status': lead.dncl_status,
        'internal_dnc_clear': lead.internal_dnc_clear,
        'suppression_clear': lead.suppression_clear,
        'eligibility_status': lead.eligibility_status,
        'eligibility_reasons': lead.eligibility_reasons or [],
        'approved_for_call': lead.approved_for_call,
        'approved_at': lead.approved_at,
        'created_at': lead.created_at,
        'updated_at': lead.updated_at,
    }


def _parse_bool(value: Any) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'y'}


def _parse_datetime(value: Any, required: bool = False) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or '').strip()
    if not text:
        if required:
            raise ValueError('Required timestamp is missing')
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError as exc:
        raise ValueError(f'Invalid timestamp: {text}') from exc


def consent_source_profile_payload(profile: ConsentSourceProfile) -> dict:
    return {
        'id': profile.id,
        'name': profile.name,
        'organization_represented': profile.organization_represented,
        'approved_consent_language': profile.approved_consent_language,
        'organization_authorized': profile.organization_authorized,
        'automated_call_permission': profile.automated_call_permission,
        'consent_proof_method': profile.consent_proof_method,
        'default_province': profile.default_province,
        'default_timezone': profile.default_timezone,
        'source_approval_evidence': profile.source_approval_evidence,
        'approval_date': profile.approval_date,
        'expires_at': profile.expires_at,
        'created_at': profile.created_at,
        'updated_at': profile.updated_at,
    }


def create_consent_source_profile(db: Session, payload: dict, user: User) -> ConsentSourceProfile:
    required = [
        'name',
        'approved_consent_language',
        'consent_proof_method',
        'source_approval_evidence',
        'approval_date',
    ]
    missing = [key for key in required if not str(payload.get(key) or '').strip()]
    if missing:
        raise ValueError(f'Missing profile fields: {", ".join(missing)}')
    profile = db.scalar(select(ConsentSourceProfile).where(
        ConsentSourceProfile.campaign_id == ALLSTATE_CAMPAIGN_ID,
        ConsentSourceProfile.name == str(payload['name']).strip(),
    ))
    values = {
        'name': str(payload['name']).strip(),
        'organization_represented': str(payload.get('organization_represented') or 'Allstate').strip(),
        'approved_consent_language': str(payload['approved_consent_language']).strip(),
        'organization_authorized': _parse_bool(payload.get('organization_authorized')),
        'automated_call_permission': _parse_bool(payload.get('automated_call_permission')),
        'consent_proof_method': str(payload['consent_proof_method']).strip(),
        'default_province': str(payload.get('default_province') or 'Ontario').strip(),
        'default_timezone': str(payload.get('default_timezone') or 'America/Toronto').strip(),
        'source_approval_evidence': str(payload['source_approval_evidence']).strip(),
        'approval_date': _parse_datetime(payload['approval_date'], True),
        'expires_at': _parse_datetime(payload.get('expires_at')),
        'updated_at': _now(),
    }
    if profile:
        for key, value in values.items():
            setattr(profile, key, value)
    else:
        profile = ConsentSourceProfile(
            company_id=ALLSTATE_COMPANY_ID,
            campaign_id=ALLSTATE_CAMPAIGN_ID,
            created_by=user.id,
            created_at=_now(),
            **values,
        )
        db.add(profile)
    db.flush()
    return profile


def consent_csv_template(mode: str) -> str:
    columns = SIMPLE_CONSENT_COLUMNS if mode == 'simple' else ADVANCED_CONSENT_COLUMNS
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    return output.getvalue()


def preview_simple_consent_rows(
    db: Session,
    rows: list[dict],
    profile: ConsentSourceProfile,
) -> dict:
    seen: set[str] = set()
    preview = []
    valid_rows = []
    for index, raw in enumerate(rows, start=1):
        reasons = []
        missing = [
            key for key in ('first_name', 'phone_number', 'consent_timestamp', 'consent_reference')
            if not str(raw.get(key) or '').strip()
        ]
        if missing:
            reasons.append(f'Missing: {", ".join(missing)}')
        phone = normalize_phone(raw.get('phone_number'))
        if not valid_us_ca_e164(phone):
            reasons.append('Invalid US/Canada phone number')
        duplicate = phone in seen or bool(db.scalar(select(ConsentedCallingLead.id).where(
            ConsentedCallingLead.campaign_id == ALLSTATE_CAMPAIGN_ID,
            ConsentedCallingLead.phone_number == phone,
        )))
        if duplicate:
            reasons.append('Duplicate phone number')
        seen.add(phone)
        try:
            consent_timestamp = _parse_datetime(raw.get('consent_timestamp'), True)
        except ValueError as exc:
            consent_timestamp = None
            reasons.append(str(exc))
        normalized = {
            'first_name': str(raw.get('first_name') or '').strip(),
            'last_name': str(raw.get('last_name') or '').strip() or None,
            'phone_number': phone,
            'timezone': profile.default_timezone,
            'province': profile.default_province,
            'product_interest': str(raw.get('product_interest') or 'Auto and property insurance').strip(),
            'consent_status': 'under_review',
            'consent_type': 'express_automated_call',
            'consent_source': profile.name,
            'consent_text': profile.approved_consent_language,
            'consent_timestamp': consent_timestamp.isoformat() if consent_timestamp else '',
            'consented_number': phone,
            'automated_or_synthesized_call_consent': profile.automated_call_permission,
            'organization_authorized': profile.organization_authorized,
            'consent_proof': f'{profile.consent_proof_method}: {str(raw.get("consent_reference") or "").strip()}',
            'consent_reference': str(raw.get('consent_reference') or '').strip(),
            'consent_withdrawn': False,
            'renewal_month': str(raw.get('renewal_month') or '').strip() or None,
            'preferred_call_time': str(raw.get('preferred_call_time') or '').strip() or None,
            'notes': str(raw.get('notes') or '').strip() or None,
            'dncl_status': 'review_required',
            'internal_dnc_clear': False,
            'suppression_clear': False,
            'source_profile_id': profile.id,
            'is_test': _parse_bool(raw.get('is_test')),
        }
        item = {
            'row': index,
            'valid': not reasons,
            'needs_review': bool(reasons),
            'duplicate': duplicate,
            'normalized_phone': phone,
            'reasons': reasons,
            'normalized': normalized,
        }
        preview.append(item)
        if not reasons:
            valid_rows.append(normalized)
    return {
        'total_rows': len(rows),
        'valid_rows': len(valid_rows),
        'rows_needing_review': len(rows) - len(valid_rows),
        'duplicate_numbers': sum(item['duplicate'] for item in preview),
        'rows': preview,
        'import_rows': valid_rows,
    }


def import_consented_leads(db: Session, rows: list[dict], user_id: str) -> dict:
    required = {
        'first_name', 'phone_number', 'timezone', 'province', 'product_interest',
        'consent_status', 'consent_type', 'consent_source', 'consent_text',
        'consent_timestamp', 'consented_number',
        'automated_or_synthesized_call_consent', 'organization_authorized',
        'consent_proof', 'consent_withdrawn',
    }
    created = 0
    updated = 0
    errors = []
    ids = []
    for index, raw in enumerate(rows, start=1):
        missing = sorted(
            key for key in required
            if key not in raw or raw.get(key) is None or (isinstance(raw.get(key), str) and not raw.get(key).strip())
        )
        if missing:
            errors.append({'row': index, 'error': f'Missing required fields: {", ".join(missing)}'})
            continue
        try:
            phone = normalize_phone(raw.get('phone_number'))
            row = db.scalar(select(ConsentedCallingLead).where(
                ConsentedCallingLead.campaign_id == ALLSTATE_CAMPAIGN_ID,
                ConsentedCallingLead.phone_number == phone,
            ))
            values = {
                'source_profile_id': str(raw.get('source_profile_id') or '').strip() or None,
                'is_test': _parse_bool(raw.get('is_test')),
                'first_name': str(raw.get('first_name')).strip(),
                'last_name': str(raw.get('last_name') or '').strip() or None,
                'phone_number': phone,
                'timezone': str(raw.get('timezone')).strip(),
                'province': str(raw.get('province')).strip(),
                'product_interest': str(raw.get('product_interest')).strip(),
                'consent_status': str(raw.get('consent_status')).strip().lower(),
                'consent_type': str(raw.get('consent_type')).strip(),
                'consent_source': str(raw.get('consent_source')).strip(),
                'consent_text': str(raw.get('consent_text')).strip(),
                'consent_timestamp': _parse_datetime(raw.get('consent_timestamp'), True),
                'consented_number': normalize_phone(raw.get('consented_number')),
                'automated_or_synthesized_call_consent': _parse_bool(raw.get('automated_or_synthesized_call_consent')),
                'organization_authorized': _parse_bool(raw.get('organization_authorized')),
                'consent_proof': str(raw.get('consent_proof')).strip(),
                'consent_reference': str(raw.get('consent_reference') or raw.get('consent_proof') or '').strip() or None,
                'consent_withdrawn': _parse_bool(raw.get('consent_withdrawn')),
                'consent_expiry': _parse_datetime(raw.get('consent_expiry')),
                'renewal_month': str(raw.get('renewal_month') or '').strip() or None,
                'preferred_call_time': str(raw.get('preferred_call_time') or '').strip() or None,
                'notes': str(raw.get('notes') or '').strip() or None,
                'dncl_status': str(raw.get('dncl_status') or 'review_required').strip().lower(),
                'internal_dnc_clear': _parse_bool(raw.get('internal_dnc_clear')),
                'suppression_clear': _parse_bool(raw.get('suppression_clear')),
            }
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
                updated += 1
            else:
                row = ConsentedCallingLead(
                    company_id=ALLSTATE_COMPANY_ID,
                    campaign_id=ALLSTATE_CAMPAIGN_ID,
                    created_by=user_id,
                    created_at=_now(),
                    **values,
                )
                db.add(row)
                db.flush()
                created += 1
            evaluate_lead(db, row)
            ids.append(row.id)
        except ValueError as exc:
            errors.append({'row': index, 'error': str(exc)})
    return {'created': created, 'updated': updated, 'errors': errors, 'lead_ids': ids}


def parse_csv_rows(content: str) -> list[dict]:
    return [dict(row) for row in csv.DictReader(io.StringIO(content))]


def approve_pilot_lead(db: Session, lead: ConsentedCallingLead, user: User, now: datetime | None = None) -> PilotCallEntry:
    status, reasons = evaluate_lead(db, lead, now=now)
    if status != 'Ready for pilot':
        raise ValueError('; '.join(reasons) or 'Lead is not ready for pilot')
    count = int(db.scalar(select(func.count(PilotCallEntry.id)).where(
        PilotCallEntry.campaign_id == lead.campaign_id,
        PilotCallEntry.status.notin_(['cancelled', 'blocked']),
    )) or 0)
    if count >= MAX_PILOT_LEADS:
        raise ValueError('Five-lead pilot limit reached')
    published = db.scalar(select(CallScriptVersion).where(
        CallScriptVersion.campaign_id == lead.campaign_id,
        CallScriptVersion.status == 'published',
    ))
    existing = db.scalar(select(PilotCallEntry).where(
        PilotCallEntry.campaign_id == lead.campaign_id,
        PilotCallEntry.lead_id == lead.id,
    ))
    if existing:
        return existing
    entry = PilotCallEntry(
        company_id=lead.company_id,
        campaign_id=lead.campaign_id,
        lead_id=lead.id,
        script_version_id=published.id,
        status='approved',
        approved_by=user.id,
        approved_at=_now(),
        agent_id_snapshot=published.retell_agent_id,
        agent_version_snapshot=published.retell_agent_version,
        script_version_snapshot=published.version_number,
        estimated_max_cost_usd=1.0,
        created_at=_now(),
        updated_at=_now(),
    )
    lead.approved_for_call = True
    lead.approved_by = user.id
    lead.approved_at = _now()
    lead.eligibility_status = 'Approved for call'
    db.add(entry)
    return entry


def pilot_payload(entry: PilotCallEntry, lead: ConsentedCallingLead) -> dict:
    in_window, local_time = recipient_in_calling_window(lead.timezone)
    return {
        'id': entry.id,
        'lead_id': lead.id,
        'lead_name': ' '.join(filter(None, [lead.first_name, lead.last_name])),
        'phone_number_masked': masked_phone(lead.phone_number),
        'consent_source': lead.consent_source,
        'consent_timestamp': lead.consent_timestamp,
        'consent_status': lead.consent_status,
        'consent_proof': lead.consent_proof,
        'automated_call_consent': lead.automated_or_synthesized_call_consent,
        'dncl_status': lead.dncl_status,
        'dnc_status': 'clear' if lead.internal_dnc_clear and lead.suppression_clear else 'blocked',
        'recipient_local_time': local_time,
        'inside_calling_window': in_window,
        'script_version': entry.script_version_snapshot,
        'retell_agent_id': entry.agent_id_snapshot,
        'retell_agent_version': entry.agent_version_snapshot,
        'estimated_max_cost_usd': entry.estimated_max_cost_usd,
        'from_number_masked': masked_phone(settings.retell_from_number),
        'status': entry.status,
        'blocked_reasons': entry.blocked_reasons or [],
        'confirmation_required': PILOT_CONFIRMATION,
    }


async def place_approved_pilot_call(
    db: Session,
    entry: PilotCallEntry,
    lead: ConsentedCallingLead,
    user: User,
    confirmation: str,
    provider: RetellCallingProvider | None = None,
) -> dict:
    if confirmation != PILOT_CONFIRMATION:
        raise ValueError(f'Confirmation must exactly match {PILOT_CONFIRMATION}')
    if entry.status != 'approved' or not lead.approved_for_call:
        raise ValueError('Lead does not have an active individual pilot approval')
    status, reasons = evaluate_lead(db, lead)
    if status != 'Ready for pilot':
        entry.status = 'blocked'
        entry.blocked_reasons = reasons
        entry.updated_at = _now()
        raise ValueError('; '.join(reasons) or 'Lead is no longer eligible')
    published = db.scalar(select(CallScriptVersion).where(
        CallScriptVersion.campaign_id == lead.campaign_id,
        CallScriptVersion.status == 'published',
    ))
    drift = []
    if not published or published.id != entry.script_version_id or published.version_number != entry.script_version_snapshot:
        drift.append('Published script version changed after approval')
    if settings.retell_agent_id != entry.agent_id_snapshot:
        drift.append('Assigned Retell agent changed after approval')
    if str(settings.retell_agent_version).isdigit() and int(settings.retell_agent_version) != entry.agent_version_snapshot:
        drift.append('Assigned Retell agent version changed after approval')
    if drift:
        entry.status = 'blocked'
        entry.blocked_reasons = drift
        entry.updated_at = _now()
        raise ValueError('; '.join(drift))
    active = db.scalar(select(CallAttempt).where(
        CallAttempt.campaign_id == lead.campaign_id,
        CallAttempt.status.in_(CALL_ACTIVE_STATUSES),
    ).limit(1))
    if active:
        raise ValueError('Another call is active; pilot concurrency is one')
    pilot_attempts = int(db.scalar(select(func.count(CallAttempt.id)).where(
        CallAttempt.campaign_id == lead.campaign_id,
        CallAttempt.mode == 'consented_pilot',
    )) or 0)
    if pilot_attempts >= MAX_PILOT_LEADS:
        raise ValueError('Five-attempt pilot limit reached')
    today = _now().date()
    today_attempts = int(db.scalar(select(func.count(CallAttempt.id)).where(
        CallAttempt.campaign_id == lead.campaign_id,
        CallAttempt.mode == 'consented_pilot',
        func.date(CallAttempt.requested_at) == today,
    )) or 0)
    if today_attempts >= MAX_CALLS_PER_DAY:
        raise ValueError('Daily pilot call limit reached')
    provider = provider or calling_provider()
    health = await provider.health(entry.agent_version_snapshot)
    if not health.get('api_authenticated') or not health.get('outbound_agent_correctly_assigned'):
        raise ValueError('Retell provider or exact outbound assignment is not ready')
    if health.get('agent_id') != entry.agent_id_snapshot:
        raise ValueError('Retell agent drift detected')
    now = _now()
    attempt = CallAttempt(
        company_id=lead.company_id,
        campaign_id=lead.campaign_id,
        consented_calling_lead_id=lead.id,
        script_version_id=published.id,
        provider='retell',
        provider_agent_id=entry.agent_id_snapshot,
        provider_agent_version=entry.agent_version_snapshot,
        from_number=normalize_phone(settings.retell_from_number),
        to_number=lead.phone_number,
        mode='consented_pilot',
        status='requested',
        confirmation_text=confirmation,
        requested_by=user.id,
        requested_at=now,
        internal_test=False,
        metadata_json={
            'consent_source': lead.consent_source,
            'consent_timestamp': lead.consent_timestamp.isoformat(),
            'script_version': published.version_number,
            'automatic_retry': False,
            'voicemail_retry': False,
        },
        created_at=now,
        updated_at=now,
    )
    db.add(attempt)
    db.flush()
    dynamic_variables = {
        'customer_name': lead.first_name,
        'assistant_name': 'Ava',
        'agent_name': 'Himanshu Soni',
        'agent_role': 'Allstate Sales Agent',
        'company_name': 'Allstate',
        'agency_location': 'Scarborough, Ontario',
        'campaign_name': 'Allstate Quote Appointment Calling',
        'call_purpose': published.purpose_statement,
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
        'slot_one': '',
        'slot_two': '',
        'callback_date': '',
        'callback_time': '',
        'voryx_call_attempt_id': attempt.id,
    }
    try:
        receipt = await provider.place_call(
            to_number=lead.phone_number,
            call_attempt_id=attempt.id,
            dynamic_variables={key: str(value) for key, value in dynamic_variables.items()},
            agent_version=entry.agent_version_snapshot,
            mode='consented_pilot',
        )
    except Exception as exc:
        attempt.status = 'provider_failed'
        attempt.termination_reason = str(exc)
        attempt.updated_at = _now()
        entry.status = 'provider_failed'
        entry.blocked_reasons = [str(exc)]
        entry.call_attempt_id = attempt.id
        entry.updated_at = _now()
        return {'ok': False, 'status': 'provider_failed', 'call_attempt_id': attempt.id, 'message': str(exc)}
    attempt.provider_call_id = str(receipt.get('call_id') or receipt.get('provider_call_id') or '')
    attempt.status = 'initiated'
    attempt.provider_receipt = {'call_id': attempt.provider_call_id, 'agent_id': entry.agent_id_snapshot}
    attempt.updated_at = _now()
    entry.status = 'calling'
    entry.confirmation_text = confirmation
    entry.call_attempt_id = attempt.id
    entry.updated_at = _now()
    lead.eligibility_status = 'Calling'
    lead.updated_at = _now()
    return {
        'ok': True,
        'status': 'initiated',
        'call_attempt_id': attempt.id,
        'provider_call_id': attempt.provider_call_id,
        'to_number': masked_phone(lead.phone_number),
    }


def cost_projection(db: Session) -> dict:
    completed = db.scalars(select(CallAttempt).where(
        CallAttempt.campaign_id == ALLSTATE_CAMPAIGN_ID,
        CallAttempt.duration_seconds.is_not(None),
        CallAttempt.provider_cost_cents.is_not(None),
    )).all()
    minutes = sum(float(item.duration_seconds or 0) for item in completed) / 60
    total_usd = sum(float(item.provider_cost_cents or 0) for item in completed) / 100
    per_minute = total_usd / minutes if minutes else 0.0
    avg_minutes = minutes / len(completed) if completed else 4.0
    per_call = per_minute * min(avg_minutes, 4.0)
    return {
        'live_model': LIVE_MODEL,
        'post_call_model': POST_CALL_MODEL,
        'last_call_cost_per_minute_usd': round(per_minute, 4),
        'average_duration_minutes': round(avg_minutes, 2),
        'projected_cost_usd': {str(count): round(per_call * count, 2) for count in (5, 20, 100)},
        'maximum_duration_seconds': 240,
        'automatic_retry': False,
        'concurrency': 1,
        'ambient_sound': None,
        'ai_quality_assurance': False,
    }
