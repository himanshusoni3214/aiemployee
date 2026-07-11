import enum, uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

def uid(): return str(uuid.uuid4())
class Role(str, enum.Enum): admin='Admin'; manager='Manager'; viewer='Viewer'
class Status(str, enum.Enum): active='Active'; inactive='Inactive'; archived='Archived'
class EmployeeStatus(str, enum.Enum): running='Running'; scheduled='Scheduled'; paused='Paused'; stopped='Stopped'; error='Error'; archived='Archived'
class JobStatus(str, enum.Enum): queued='Queued'; running='Running'; completed='Completed'; failed='Failed'; blocked='Blocked'; cancelled='Cancelled'; skipped='Skipped'; imported='Imported'; synced='Synced'
class LeadStatus(str, enum.Enum): generated='Generated'; verified='Verified'; contacted='Contacted'; replied='Replied'; interested='Interested'; meeting_booked='Meeting Booked'; closed='Closed'

class User(Base):
    __tablename__='users'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    email: Mapped[str]=mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str]=mapped_column(String)
    role: Mapped[Role]=mapped_column(Enum(Role), default=Role.viewer)
    is_active: Mapped[bool]=mapped_column(Boolean, default=True)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class Company(Base):
    __tablename__='companies'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str]=mapped_column(String, index=True)
    logo: Mapped[str|None]=mapped_column(String, nullable=True)
    website: Mapped[str|None]=mapped_column(String, nullable=True)
    industry: Mapped[str|None]=mapped_column(String, nullable=True)
    status: Mapped[Status]=mapped_column(Enum(Status), default=Status.active)
    timezone: Mapped[str]=mapped_column(String, default='America/Toronto')
    default_report_recipient: Mapped[str|None]=mapped_column(String, nullable=True)
    daily_email_limit: Mapped[int]=mapped_column(Integer, default=50)
    notes: Mapped[str|None]=mapped_column(Text, nullable=True)

class AIEmployee(Base):
    __tablename__='ai_employees'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'))
    campaign_id: Mapped[str|None]=mapped_column(ForeignKey('campaigns.id'), nullable=True)
    name: Mapped[str]=mapped_column(String)
    employee_type: Mapped[str]=mapped_column(String)
    hermes_job_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    approved_script: Mapped[str|None]=mapped_column(String, nullable=True)
    working_directory: Mapped[str|None]=mapped_column(String, nullable=True)
    prompt: Mapped[str]=mapped_column(Text, default='')
    daily_limits: Mapped[dict]=mapped_column(JSON, default=dict)
    status: Mapped[EmployeeStatus]=mapped_column(Enum(EmployeeStatus), default=EmployeeStatus.stopped)
    dry_run_mode: Mapped[bool]=mapped_column(Boolean, default=True)
    rate_limit_per_hour: Mapped[int]=mapped_column(Integer, default=20)
    daily_email_limit: Mapped[int]=mapped_column(Integer, default=50)
    failure_count: Mapped[int]=mapped_column(Integer, default=0)
    circuit_breaker_open: Mapped[bool]=mapped_column(Boolean, default=False)
    paused_reason: Mapped[str|None]=mapped_column(Text, nullable=True)
    last_error: Mapped[str|None]=mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)
    last_successful_run_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)
    last_failed_run_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)
    company=relationship('Company')

class Campaign(Base):
    __tablename__='campaigns'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'))
    name: Mapped[str]=mapped_column(String)
    description: Mapped[str|None]=mapped_column(Text, nullable=True)
    industry: Mapped[str|None]=mapped_column(String)
    target_audience: Mapped[str|None]=mapped_column(Text, nullable=True)
    geographic_area: Mapped[str|None]=mapped_column(String, nullable=True)
    daily_lead_goal: Mapped[int]=mapped_column(Integer, default=0)
    daily_email_goal: Mapped[int]=mapped_column(Integer, default=0)
    daily_email_limit: Mapped[int]=mapped_column(Integer, default=0)
    campaign_type: Mapped[str]=mapped_column(String, default='custom', index=True)
    provisioning_state: Mapped[str]=mapped_column(String, default='Draft', index=True)
    provisioning_result: Mapped[dict]=mapped_column(JSON, default=dict)
    timezone: Mapped[str]=mapped_column(String, default='America/Toronto')
    allowed_sending_days: Mapped[list]=mapped_column(JSON, default=list)
    allowed_sending_hours: Mapped[dict]=mapped_column(JSON, default=dict)
    internal_test_recipient: Mapped[str|None]=mapped_column(String, nullable=True)
    report_recipient: Mapped[str|None]=mapped_column(String, nullable=True)
    dry_run_mode: Mapped[bool]=mapped_column(Boolean, default=True)
    start_date: Mapped[str|None]=mapped_column(String, nullable=True)
    end_date: Mapped[str|None]=mapped_column(String, nullable=True)
    status: Mapped[Status]=mapped_column(Enum(Status), default=Status.active)

class Lead(Base):
    __tablename__='leads'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'))
    campaign_id: Mapped[str|None]=mapped_column(ForeignKey('campaigns.id'), nullable=True)
    name: Mapped[str|None]=mapped_column(String)
    business: Mapped[str|None]=mapped_column(String)
    email: Mapped[str|None]=mapped_column(String, index=True)
    phone: Mapped[str|None]=mapped_column(String)
    website: Mapped[str|None]=mapped_column(String)
    status: Mapped[LeadStatus]=mapped_column(Enum(LeadStatus), default=LeadStatus.generated)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class Schedule(Base):
    __tablename__='schedules'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    employee_id: Mapped[str]=mapped_column(ForeignKey('ai_employees.id'))
    name: Mapped[str]=mapped_column(String)
    cron: Mapped[str]=mapped_column(String)
    timezone: Mapped[str]=mapped_column(String, default='America/Toronto')
    task_type: Mapped[str]=mapped_column(String)
    payload: Mapped[dict]=mapped_column(JSON, default=dict)
    is_paused: Mapped[bool]=mapped_column(Boolean, default=False)
    last_run_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)

class Job(Base):
    __tablename__='jobs'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    employee_id: Mapped[str|None]=mapped_column(ForeignKey('ai_employees.id'), nullable=True)
    campaign_id: Mapped[str|None]=mapped_column(ForeignKey('campaigns.id'), nullable=True)
    connector: Mapped[str]=mapped_column(String, default='hermes')
    task_type: Mapped[str]=mapped_column(String)
    status: Mapped[JobStatus]=mapped_column(Enum(JobStatus), default=JobStatus.queued)
    payload: Mapped[dict]=mapped_column(JSON, default=dict)
    result: Mapped[dict|None]=mapped_column(JSON, nullable=True)
    logs: Mapped[list]=mapped_column(JSON, default=list)
    error_message: Mapped[str|None]=mapped_column(Text, nullable=True)
    attempts: Mapped[int]=mapped_column(Integer, default=0)
    max_attempts: Mapped[int]=mapped_column(Integer, default=1)
    retry_after: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[int|None]=mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)
    provider_message_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    recipient_email: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    sent_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True, index=True)
    delivery_status: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    evidence_type: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    source_output_path: Mapped[str|None]=mapped_column(String, nullable=True)
    verification_reason: Mapped[str|None]=mapped_column(Text, nullable=True)
    hermes_job_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    hermes_run_timestamp: Mapped[datetime|None]=mapped_column(DateTime, nullable=True, index=True)
    external_execution_key: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class ActivityLog(Base):
    __tablename__='activity_logs'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str|None]=mapped_column(ForeignKey('companies.id'), nullable=True)
    user_id: Mapped[str|None]=mapped_column(ForeignKey('users.id'), nullable=True)
    action: Mapped[str]=mapped_column(String)
    entity_type: Mapped[str]=mapped_column(String)
    entity_id: Mapped[str|None]=mapped_column(String)
    metadata_json: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class Credential(Base):
    __tablename__='credentials'
    __table_args__=(UniqueConstraint('company_id','provider','name'),)
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'))
    provider: Mapped[str]=mapped_column(String)
    name: Mapped[str]=mapped_column(String)
    encrypted_secret: Mapped[str]=mapped_column(Text)

class OutreachEvent(Base):
    __tablename__='outreach_events'
    event_id: Mapped[str]=mapped_column(String, primary_key=True)
    campaign_id: Mapped[str|None]=mapped_column(ForeignKey('campaigns.id'), nullable=True, index=True)
    company_id: Mapped[str|None]=mapped_column(ForeignKey('companies.id'), nullable=True, index=True)
    employee_id: Mapped[str|None]=mapped_column(ForeignKey('ai_employees.id'), nullable=True, index=True)
    lead_id: Mapped[str|None]=mapped_column(ForeignKey('leads.id'), nullable=True)
    recipient: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    business: Mapped[str|None]=mapped_column(String, nullable=True)
    subject: Mapped[str|None]=mapped_column(String, nullable=True)
    attempted_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True, index=True)
    sent_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str]=mapped_column(String, index=True)
    message_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    thread_id: Mapped[str|None]=mapped_column(String, nullable=True)
    provider: Mapped[str|None]=mapped_column(String, nullable=True)
    error_code: Mapped[str|None]=mapped_column(String, nullable=True)
    error_message: Mapped[str|None]=mapped_column(Text, nullable=True)
    dry_run: Mapped[bool]=mapped_column(Boolean, default=False, index=True)
    job_run_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    source_file: Mapped[str|None]=mapped_column(String, nullable=True)
    raw: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class ReportRun(Base):
    __tablename__='report_runs'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str|None]=mapped_column(ForeignKey('companies.id'), nullable=True, index=True)
    campaign_id: Mapped[str|None]=mapped_column(ForeignKey('campaigns.id'), nullable=True, index=True)
    report_date: Mapped[str]=mapped_column(String, index=True)
    timezone: Mapped[str]=mapped_column(String, default='America/Toronto')
    generated_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)
    artifact_path: Mapped[str|None]=mapped_column(String, nullable=True)
    metrics: Mapped[dict]=mapped_column(JSON, default=dict)
    evidence: Mapped[list]=mapped_column(JSON, default=list)
    delivery_result: Mapped[dict]=mapped_column(JSON, default=dict)
    status: Mapped[str]=mapped_column(String, default='generated', index=True)


class GlobalModelPolicy(Base):
    __tablename__='global_model_policies'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str]=mapped_column(String, unique=True, default='default', index=True)
    provider: Mapped[str]=mapped_column(String, default='openrouter')
    model: Mapped[str]=mapped_column(String, default='nvidia/nemotron-3-super-120b-a12b')
    approved_models: Mapped[list]=mapped_column(JSON, default=list)
    blocked_models: Mapped[list]=mapped_column(JSON, default=list)
    fallback_enabled: Mapped[bool]=mapped_column(Boolean, default=False)
    fail_closed: Mapped[bool]=mapped_column(Boolean, default=True)
    daily_budget_usd: Mapped[int]=mapped_column(Integer, default=0)
    monthly_budget_usd: Mapped[int]=mapped_column(Integer, default=0)
    max_cost_per_run_usd: Mapped[int]=mapped_column(Integer, default=0)
    notes: Mapped[str|None]=mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class CompanyModelPolicy(Base):
    __tablename__='company_model_policies'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'), unique=True, index=True)
    provider: Mapped[str|None]=mapped_column(String, nullable=True)
    model: Mapped[str|None]=mapped_column(String, nullable=True)
    approved_models: Mapped[list]=mapped_column(JSON, default=list)
    blocked_models: Mapped[list]=mapped_column(JSON, default=list)
    fallback_enabled: Mapped[bool|None]=mapped_column(Boolean, nullable=True)
    fail_closed: Mapped[bool|None]=mapped_column(Boolean, nullable=True)
    daily_budget_usd: Mapped[int|None]=mapped_column(Integer, nullable=True)
    monthly_budget_usd: Mapped[int|None]=mapped_column(Integer, nullable=True)
    max_cost_per_run_usd: Mapped[int|None]=mapped_column(Integer, nullable=True)
    notes: Mapped[str|None]=mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class EmployeeModelPolicy(Base):
    __tablename__='employee_model_policies'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    employee_id: Mapped[str]=mapped_column(ForeignKey('ai_employees.id'), unique=True, index=True)
    company_id: Mapped[str|None]=mapped_column(ForeignKey('companies.id'), nullable=True, index=True)
    campaign_id: Mapped[str|None]=mapped_column(ForeignKey('campaigns.id'), nullable=True, index=True)
    hermes_job_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    provider: Mapped[str|None]=mapped_column(String, nullable=True)
    model: Mapped[str|None]=mapped_column(String, nullable=True)
    approved_models: Mapped[list]=mapped_column(JSON, default=list)
    blocked_models: Mapped[list]=mapped_column(JSON, default=list)
    fallback_enabled: Mapped[bool|None]=mapped_column(Boolean, nullable=True)
    fail_closed: Mapped[bool|None]=mapped_column(Boolean, nullable=True)
    daily_budget_usd: Mapped[int|None]=mapped_column(Integer, nullable=True)
    monthly_budget_usd: Mapped[int|None]=mapped_column(Integer, nullable=True)
    max_cost_per_run_usd: Mapped[int|None]=mapped_column(Integer, nullable=True)
    notes: Mapped[str|None]=mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class ModelUsageAudit(Base):
    __tablename__='model_usage_audits'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str|None]=mapped_column(ForeignKey('companies.id'), nullable=True, index=True)
    campaign_id: Mapped[str|None]=mapped_column(ForeignKey('campaigns.id'), nullable=True, index=True)
    employee_id: Mapped[str|None]=mapped_column(ForeignKey('ai_employees.id'), nullable=True, index=True)
    hermes_job_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    provider: Mapped[str]=mapped_column(String, default='openrouter', index=True)
    model: Mapped[str]=mapped_column(String, default='nvidia/nemotron-3-super-120b-a12b', index=True)
    normalized_model: Mapped[str]=mapped_column(String, default='openrouter/nvidia/nemotron-3-super-120b-a12b', index=True)
    task_type: Mapped[str|None]=mapped_column(String, nullable=True)
    status: Mapped[str]=mapped_column(String, default='allowed', index=True)
    reason: Mapped[str|None]=mapped_column(Text, nullable=True)
    estimated_cost_usd: Mapped[int|None]=mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow, index=True)

class CompanyOutreachSettings(Base):
    __tablename__='company_outreach_settings'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'), unique=True, index=True)
    sender_name: Mapped[str|None]=mapped_column(String, nullable=True)
    sender_email: Mapped[str|None]=mapped_column(String, nullable=True)
    reply_to_email: Mapped[str|None]=mapped_column(String, nullable=True)
    physical_mailing_address: Mapped[str|None]=mapped_column(Text, nullable=True)
    unsubscribe_text: Mapped[str|None]=mapped_column(Text, nullable=True)
    daily_send_limit: Mapped[int]=mapped_column(Integer, default=5)
    hourly_send_limit: Mapped[int]=mapped_column(Integer, default=1)
    allowed_sending_days: Mapped[list]=mapped_column(JSON, default=list)
    allowed_sending_hours: Mapped[dict]=mapped_column(JSON, default=dict)
    allowed_sending_start_date: Mapped[str|None]=mapped_column(String, nullable=True)
    allowed_sending_end_date: Mapped[str|None]=mapped_column(String, nullable=True)
    timezone: Mapped[str]=mapped_column(String, default='America/Toronto')
    approved_sender_connected: Mapped[bool]=mapped_column(Boolean, default=False)
    compliance_acknowledged: Mapped[bool]=mapped_column(Boolean, default=False)
    prospect_sending_enabled: Mapped[bool]=mapped_column(Boolean, default=False)
    internal_test_recipient: Mapped[str|None]=mapped_column(String, nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class SuppressionEntry(Base):
    __tablename__='suppression_entries'
    __table_args__=(UniqueConstraint('company_id','kind','value'),)
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'), index=True)
    kind: Mapped[str]=mapped_column(String, default='email', index=True)
    value: Mapped[str]=mapped_column(String, index=True)
    reason: Mapped[str|None]=mapped_column(Text, nullable=True)
    source: Mapped[str]=mapped_column(String, default='dashboard')
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class LeadApproval(Base):
    __tablename__='lead_approvals'
    __table_args__=(UniqueConstraint('campaign_id','lead_key'),)
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'), index=True)
    campaign_id: Mapped[str]=mapped_column(ForeignKey('campaigns.id'), index=True)
    employee_id: Mapped[str|None]=mapped_column(ForeignKey('ai_employees.id'), nullable=True, index=True)
    hermes_job_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    source_run_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    lead_key: Mapped[str]=mapped_column(String, index=True)
    email: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    domain: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    business: Mapped[str|None]=mapped_column(String, nullable=True)
    state: Mapped[str]=mapped_column(String, default='new', index=True)
    reason: Mapped[str|None]=mapped_column(Text, nullable=True)
    raw: Mapped[dict]=mapped_column(JSON, default=dict)
    history: Mapped[list]=mapped_column(JSON, default=list)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class OutreachDraft(Base):
    __tablename__='outreach_drafts'
    __table_args__=(UniqueConstraint('campaign_id','lead_key','version'),)
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'), index=True)
    campaign_id: Mapped[str]=mapped_column(ForeignKey('campaigns.id'), index=True)
    employee_id: Mapped[str|None]=mapped_column(ForeignKey('ai_employees.id'), nullable=True, index=True)
    hermes_job_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    source_run_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    lead_key: Mapped[str]=mapped_column(String, index=True)
    lead_email: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    business: Mapped[str|None]=mapped_column(String, nullable=True)
    subject: Mapped[str]=mapped_column(String)
    body: Mapped[str]=mapped_column(Text)
    status: Mapped[str]=mapped_column(String, default='draft_created', index=True)
    version: Mapped[int]=mapped_column(Integer, default=1)
    approved_by: Mapped[str|None]=mapped_column(ForeignKey('users.id'), nullable=True)
    approved_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)
    raw: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)

class ReplyMonitorEvent(Base):
    __tablename__='reply_monitor_events'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'), index=True)
    campaign_id: Mapped[str]=mapped_column(ForeignKey('campaigns.id'), index=True)
    employee_id: Mapped[str|None]=mapped_column(ForeignKey('ai_employees.id'), nullable=True, index=True)
    hermes_job_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    lead_key: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    recipient: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    thread_id: Mapped[str|None]=mapped_column(String, nullable=True, index=True)
    classification: Mapped[str]=mapped_column(String, default='unclassified', index=True)
    status: Mapped[str]=mapped_column(String, default='detected', index=True)
    raw: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime, default=datetime.utcnow)
