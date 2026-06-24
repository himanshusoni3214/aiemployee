from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict, EmailStr

class LoginIn(BaseModel): email: EmailStr; password: str
class TokenOut(BaseModel): access_token: str; token_type: str='bearer'
class CompanyIn(BaseModel): name: str; logo: str|None=None; website: str|None=None; industry: str|None=None; status: str='Active'
class EmployeeIn(BaseModel): company_id: str; name: str; employee_type: str; prompt: str=''; daily_limits: dict[str, Any]={}; status: str='Stopped'; rate_limit_per_hour: int=20; daily_email_limit: int=50
class CampaignIn(BaseModel): company_id: str; name: str; industry: str|None=None; daily_lead_goal: int=0; daily_email_goal: int=0; status: str='Active'
class LeadIn(BaseModel): company_id: str; campaign_id: str|None=None; name: str|None=None; business: str|None=None; email: str|None=None; phone: str|None=None; website: str|None=None; status: str='Generated'
class ScheduleIn(BaseModel): employee_id: str; name: str; cron: str; task_type: str; payload: dict[str, Any]={}; is_paused: bool=False
class JobIn(BaseModel): employee_id: str|None=None; campaign_id: str|None=None; connector: str='hermes'; task_type: str; payload: dict[str, Any]={}; max_attempts: int=1
class AnyOut(BaseModel): model_config=ConfigDict(from_attributes=True)
