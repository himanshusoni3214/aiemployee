from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, EmailStr

class LoginIn(BaseModel): email: EmailStr; password: str
class TokenOut(BaseModel): access_token: str; token_type: str='bearer'
class CompanyIn(BaseModel):
    name: str
    logo: str|None=None
    website: str|None=None
    industry: str|None=None
    status: str='Active'
    timezone: str='America/Toronto'
    default_report_recipient: str|None=None
    daily_email_limit: int=50
    notes: str|None=None

class EmployeeIn(BaseModel):
    company_id: str
    campaign_id: str|None=None
    name: str
    employee_type: str
    hermes_job_id: str|None=None
    approved_script: str|None=None
    working_directory: str|None=None
    prompt: str=''
    daily_limits: dict[str, Any]={}
    dry_run_mode: bool=True
    status: str='Stopped'
    rate_limit_per_hour: int=20
    daily_email_limit: int=50

class CampaignIn(BaseModel):
    company_id: str
    name: str
    description: str|None=None
    industry: str|None=None
    target_audience: str|None=None
    geographic_area: str|None=None
    daily_lead_goal: int=0
    daily_email_goal: int=0
    daily_email_limit: int=0
    timezone: str='America/Toronto'
    allowed_sending_days: list[str]=[]
    allowed_sending_hours: dict[str, Any]={}
    internal_test_recipient: str|None=None
    report_recipient: str|None=None
    dry_run_mode: bool=True
    start_date: str|None=None
    end_date: str|None=None
    status: str='Active'
class LeadIn(BaseModel): company_id: str; campaign_id: str|None=None; name: str|None=None; business: str|None=None; email: str|None=None; phone: str|None=None; website: str|None=None; status: str='Generated'
class ScheduleIn(BaseModel): employee_id: str; name: str; cron: str; timezone: str='America/Toronto'; task_type: str; payload: dict[str, Any]={}; is_paused: bool=False
class JobIn(BaseModel): employee_id: str|None=None; campaign_id: str|None=None; connector: str='hermes'; task_type: str; payload: dict[str, Any]={}; max_attempts: int=1
class AnyOut(BaseModel): model_config=ConfigDict(from_attributes=True)

class DailyReportRequest(BaseModel):
    report_date: str|None=None
    company_id: str|None=None
    campaign_id: str|None=None
    recipient: str|None=None
    send_email: bool=False
    report_only_acceptance: bool=False
