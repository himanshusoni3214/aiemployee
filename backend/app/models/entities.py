import enum, uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

def uid(): return str(uuid.uuid4())
class Role(str, enum.Enum): admin='Admin'; manager='Manager'; viewer='Viewer'
class Status(str, enum.Enum): active='Active'; inactive='Inactive'; archived='Archived'
class EmployeeStatus(str, enum.Enum): running='Running'; paused='Paused'; stopped='Stopped'; error='Error'
class JobStatus(str, enum.Enum): queued='Queued'; running='Running'; completed='Completed'; failed='Failed'
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

class AIEmployee(Base):
    __tablename__='ai_employees'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'))
    name: Mapped[str]=mapped_column(String)
    employee_type: Mapped[str]=mapped_column(String)
    prompt: Mapped[str]=mapped_column(Text, default='')
    daily_limits: Mapped[dict]=mapped_column(JSON, default=dict)
    status: Mapped[EmployeeStatus]=mapped_column(Enum(EmployeeStatus), default=EmployeeStatus.stopped)
    rate_limit_per_hour: Mapped[int]=mapped_column(Integer, default=20)
    daily_email_limit: Mapped[int]=mapped_column(Integer, default=50)
    failure_count: Mapped[int]=mapped_column(Integer, default=0)
    circuit_breaker_open: Mapped[bool]=mapped_column(Boolean, default=False)
    paused_reason: Mapped[str|None]=mapped_column(Text, nullable=True)
    last_error: Mapped[str|None]=mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[datetime|None]=mapped_column(DateTime, nullable=True)
    company=relationship('Company')

class Campaign(Base):
    __tablename__='campaigns'
    id: Mapped[str]=mapped_column(String, primary_key=True, default=uid)
    company_id: Mapped[str]=mapped_column(ForeignKey('companies.id'))
    name: Mapped[str]=mapped_column(String)
    industry: Mapped[str|None]=mapped_column(String)
    daily_lead_goal: Mapped[int]=mapped_column(Integer, default=0)
    daily_email_goal: Mapped[int]=mapped_column(Integer, default=0)
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
