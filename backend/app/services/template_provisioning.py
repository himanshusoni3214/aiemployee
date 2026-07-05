import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from shlex import quote
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import AIEmployee, Campaign, EmployeeStatus, Job, JobStatus, Lead, LeadStatus, Schedule
from app.services.audit import log
from app.services.hermes_control import HermesControlError, HermesControlService
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT


APPROVED_INTERNAL_RECIPIENT = INTERNAL_REPORT_RECIPIENT
PROVISIONED_STATES = {"Provisioned", "Active", "Paused"}
TEMPLATE_TYPES = {"lead_research", "daily_reporting", "outreach_drafting", "custom"}
GENERIC_LEAD_RESEARCH_SCRIPT = "/opt/data/home/leads/voryx_generic_lead_research.py"


@dataclass(frozen=True)
class TemplateSpec:
    employee_type: str
    task_type: str
    schedule_name: str
    cron: str
    command: str
    working_directory: str
    description: str
    safety: dict[str, Any]
    prompt: str


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "campaign"


def _stable_job_id(campaign: Campaign) -> str:
    digest = hashlib.sha1(f"{campaign.id}:{campaign.campaign_type}".encode("utf-8")).hexdigest()[:10]
    return f"voryx-template-{_slug(campaign.name)}-{digest}"


def _company_workspace(company_id: str) -> str:
    return f"/opt/data/home/voryx_workspaces/{_slug(company_id)}"


def _campaign_workspace(campaign: Campaign) -> str:
    return f"{_company_workspace(campaign.company_id)}/{_slug(campaign.id)}"


def _container_to_data_path(container_path: str) -> Path:
    if not settings.hermes_data_path:
        raise ValueError("HERMES_DATA_PATH is required for template provisioning")
    value = container_path.strip()
    if not value.startswith("/opt/data/"):
        raise ValueError(f"Refusing to write outside /opt/data: {container_path}")
    relative = value.removeprefix("/opt/data/")
    return Path(settings.hermes_data_path) / relative


def _asset_path(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / name


def _ensure_generic_lead_script() -> None:
    source = _asset_path("voryx_generic_lead_research.py")
    destination = _container_to_data_path(GENERIC_LEAD_RESEARCH_SCRIPT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != source.read_text(encoding="utf-8"):
        shutil.copy2(source, destination)
    destination.chmod(0o755)


def _lead_research_config(campaign: Campaign) -> dict[str, Any]:
    industry = (campaign.industry or "").strip()
    location = (campaign.geographic_area or "").strip()
    if not industry:
        raise ValueError("Lead Research template requires Industry / niche.")
    if not location:
        raise ValueError("Lead Research template requires City / region.")
    limit = max(int(campaign.daily_lead_goal or 0), 1)
    return {
        "company_id": campaign.company_id,
        "campaign_id": campaign.id,
        "industry": industry,
        "location": location,
        "target_customer": (campaign.target_audience or "").strip(),
        "exclude": (campaign.description or "").strip(),
        "limit": limit,
        "notes": (campaign.provisioning_result or {}).get("notes") if isinstance(campaign.provisioning_result, dict) else None,
        "email_sending": False,
        "prospect_outreach": False,
    }


def _write_lead_research_config(campaign: Campaign, workspace: str) -> str:
    config = _lead_research_config(campaign)
    path = _container_to_data_path(f"{workspace}/lead_research_config.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f"{workspace}/lead_research_config.json"


def _lead_research_command(campaign: Campaign, workspace: str) -> tuple[str, str, dict[str, Any]]:
    _ensure_generic_lead_script()
    config = _lead_research_config(campaign)
    config_path = _write_lead_research_config(campaign, workspace)
    output_dir = f"{workspace}/leads"
    _container_to_data_path(output_dir).mkdir(parents=True, exist_ok=True)
    command = " ".join([
        "python3",
        quote(GENERIC_LEAD_RESEARCH_SCRIPT),
        "--company-id",
        quote(config["company_id"]),
        "--campaign-id",
        quote(config["campaign_id"]),
        "--industry",
        quote(config["industry"]),
        "--location",
        quote(config["location"]),
        "--target-customer",
        quote(config["target_customer"]),
        "--exclude",
        quote(config["exclude"]),
        "--limit",
        str(config["limit"]),
        "--output-dir",
        quote(output_dir),
        "--notes",
        quote(str(config.get("notes") or "")),
        "--no-email",
    ])
    config["config_path"] = config_path
    config["output_dir"] = output_dir
    config["script"] = GENERIC_LEAD_RESEARCH_SCRIPT
    return command, output_dir, config


def template_spec(campaign: Campaign) -> TemplateSpec | None:
    workspace = _campaign_workspace(campaign)
    campaign_type = (campaign.campaign_type or "custom").strip().lower()
    if campaign_type == "lead_research":
        command, output_dir, config = _lead_research_command(campaign, workspace)
        return TemplateSpec(
            employee_type="Lead Researcher",
            task_type="Generate Leads",
            schedule_name=f"{campaign.name} Lead Research",
            cron="0 13 * * *",
            command=command,
            working_directory=workspace,
            description="Voryx template Lead Research job. Generates leads only and never sends email.",
            safety={"prospect_outreach": False, "email_sending": False, "max_sample_leads": 5, "script": GENERIC_LEAD_RESEARCH_SCRIPT, "config": config},
            prompt=f"Generate lead research only for {config['industry']} in {config['location']}. Email sending disabled.",
        )
    if campaign_type == "daily_reporting":
        recipient = campaign.report_recipient or campaign.internal_test_recipient or APPROVED_INTERNAL_RECIPIENT
        return TemplateSpec(
            employee_type="CRM Manager",
            task_type="Daily Report",
            schedule_name=f"{campaign.name} Daily Reporting",
            cron="0 23 * * *",
            command=f"python3 /opt/data/home/leads/generate_daily_report.py --recipient {recipient} --send-email false",
            working_directory=workspace,
            description="Voryx template Daily Reporting job. Internal reports only.",
            safety={"prospect_outreach": False, "approved_recipient": APPROVED_INTERNAL_RECIPIENT, "send_email_default": False},
            prompt="Prepare internal daily reporting only for the approved recipient.",
        )
    if campaign_type == "outreach_drafting":
        return TemplateSpec(
            employee_type="Email Outreach",
            task_type="Draft Outreach",
            schedule_name=f"{campaign.name} Outreach Drafting",
            cron="0 15 * * *",
            command=f"python3 /opt/data/home/leads/draft_outreach.py --output-dir {workspace}/drafts --no-send",
            working_directory=workspace,
            description="Voryx template Outreach Drafting job. Draft-only automation with no send action.",
            safety={"prospect_outreach": False, "email_sending": False, "draft_only": True},
            prompt="Generate outreach draft copy only. Never send email.",
        )
    return None


def _desired_job(campaign: Campaign, employee: AIEmployee, schedule: Schedule, spec: TemplateSpec, job_id: str) -> dict[str, Any]:
    return {
        "id": job_id,
        "name": f"voryx-{_slug(campaign.name)}-{campaign.campaign_type}",
        "enabled": False,
        "state": "paused",
        "next_run_at": None,
        "paused_at": datetime.utcnow().isoformat() + "Z",
        "paused_reason": "Provisioned disabled by Voryx template; review and resume from dashboard only after approval.",
        "schedule": {"kind": "cron", "expr": spec.cron, "display": spec.cron, "timezone": campaign.timezone or "America/Toronto"},
        "schedule_display": spec.cron,
        "command": spec.command,
        "working_directory": spec.working_directory,
        "description": spec.description,
        "source": "voryx_template",
        "company_id": campaign.company_id,
        "campaign_id": campaign.id,
        "employee_id": employee.id,
        "schedule_id": schedule.id,
        "task_type": spec.task_type,
        "safety": spec.safety,
    }


def provision_campaign_template(db: Session, campaign: Campaign, user_id: str | None = None) -> dict[str, Any]:
    campaign.campaign_type = (campaign.campaign_type or "custom").strip().lower()
    if campaign.campaign_type not in TEMPLATE_TYPES:
        raise ValueError(f"Unsupported campaign template: {campaign.campaign_type}")
    if campaign.campaign_type == "custom":
        campaign.provisioning_state = campaign.provisioning_state or "Draft"
        campaign.provisioning_result = {"provisioned": False, "message": "Custom campaign requires manual Hermes provisioning."}
        return campaign.provisioning_result
    if campaign.campaign_type == "lead_research":
        _lead_research_config(campaign)
    if campaign.campaign_type in {"daily_reporting", "outreach_drafting"}:
        recipient = campaign.report_recipient or campaign.internal_test_recipient
        if campaign.campaign_type == "daily_reporting" and recipient and recipient != APPROVED_INTERNAL_RECIPIENT:
            raise ValueError(f"Daily Reporting template recipient must be {APPROVED_INTERNAL_RECIPIENT}")
    spec = template_spec(campaign)
    if not spec:
        raise ValueError(f"No provisioning spec for campaign template: {campaign.campaign_type}")

    campaign.provisioning_state = "Provisioning"
    db.flush()
    job_id = _stable_job_id(campaign)
    employee = db.scalar(select(AIEmployee).where(AIEmployee.hermes_job_id == job_id))
    if not employee:
        employee = AIEmployee(
            company_id=campaign.company_id,
            campaign_id=campaign.id,
            name=spec.schedule_name,
            employee_type=spec.employee_type,
            hermes_job_id=job_id,
            approved_script=spec.command,
            working_directory=spec.working_directory,
            prompt=spec.prompt,
            daily_limits={"campaign_type": campaign.campaign_type, "hermes_job_id": job_id, "safety": spec.safety},
            status=EmployeeStatus.paused,
            dry_run_mode=True,
            daily_email_limit=0 if campaign.campaign_type != "daily_reporting" else 1,
            paused_reason="Provisioned paused by template.",
        )
        db.add(employee)
        db.flush()
    else:
        employee.company_id = campaign.company_id
        employee.campaign_id = campaign.id
        employee.employee_type = spec.employee_type
        employee.approved_script = spec.command
        employee.working_directory = spec.working_directory
        employee.prompt = employee.prompt or spec.prompt
        employee.daily_limits = {**(employee.daily_limits or {}), "campaign_type": campaign.campaign_type, "hermes_job_id": job_id, "safety": spec.safety}
        if employee.status in {EmployeeStatus.running, EmployeeStatus.scheduled}:
            employee.status = EmployeeStatus.paused
            employee.paused_reason = "Template provisioning keeps new Hermes jobs paused until approved."

    schedule = db.scalar(select(Schedule).where(Schedule.employee_id == employee.id, Schedule.payload["hermes_job_id"].as_string() == job_id))
    if not schedule:
        schedule = Schedule(
            employee_id=employee.id,
            name=spec.schedule_name,
            cron=spec.cron,
            timezone=campaign.timezone or "America/Toronto",
            task_type=spec.task_type,
            payload={"source": "voryx_template", "hermes_job_id": job_id, "campaign_type": campaign.campaign_type, "safety": spec.safety},
            is_paused=True,
        )
        db.add(schedule)
        db.flush()
    else:
        schedule.name = spec.schedule_name
        schedule.cron = spec.cron
        schedule.timezone = campaign.timezone or "America/Toronto"
        schedule.task_type = spec.task_type
        schedule.payload = {**(schedule.payload or {}), "source": "voryx_template", "hermes_job_id": job_id, "campaign_type": campaign.campaign_type, "safety": spec.safety}
        schedule.is_paused = True

    desired_job = _desired_job(campaign, employee, schedule, spec, job_id)
    control = HermesControlService().upsert_provisioned_job(desired_job)
    campaign.provisioning_state = "Provisioned"
    campaign.provisioning_result = {
        "provisioned": True,
        "hermes_job_id": job_id,
        "employee_id": employee.id,
        "schedule_id": schedule.id,
        "template": campaign.campaign_type,
        "approved_script": spec.command,
        "working_directory": spec.working_directory,
        "safety": spec.safety,
        "hermes_control": control,
        "message": "Template provisioned disabled/paused Hermes job and paused Voryx schedule.",
    }
    log(db, "Campaign Template Provisioned", "Campaign", campaign.id, campaign.company_id, user_id, campaign.provisioning_result)
    return campaign.provisioning_result


def mark_provisioning_failed(campaign: Campaign, exc: Exception) -> None:
    campaign.provisioning_state = "Provisioning Failed"
    campaign.provisioning_result = {"provisioned": False, "error": str(exc)}


def validate_employee_operational_state(db: Session, employee: AIEmployee, next_status: EmployeeStatus | str | None = None) -> None:
    status = next_status if isinstance(next_status, EmployeeStatus) else None
    if status is None and next_status is not None:
        status = next((member for member in EmployeeStatus if str(next_status) in {member.value, member.name}), None)
    status = status or employee.status
    hermes_job_id = (employee.hermes_job_id or "").strip()
    if status in {EmployeeStatus.running, EmployeeStatus.scheduled} and not hermes_job_id:
        raise ValueError("Employee cannot become Running or Scheduled without a Hermes job ID.")
    if status == EmployeeStatus.scheduled:
        schedule = db.scalar(select(Schedule).where(Schedule.employee_id == employee.id).limit(1))
        if not schedule:
            raise ValueError("Employee cannot become Scheduled without a schedule.")


def create_template_sample_job(db: Session, campaign: Campaign, action: str, user_id: str | None = None) -> Job:
    campaign_type = (campaign.campaign_type or "custom").strip().lower()
    employee = db.scalar(select(AIEmployee).where(AIEmployee.campaign_id == campaign.id).order_by(AIEmployee.name).limit(1))
    now = datetime.utcnow()
    if action == "generate-sample" and campaign_type == "lead_research":
        limit = min(max(int(campaign.daily_lead_goal or 5), 1), 5)
        leads = []
        for index in range(limit):
            lead = Lead(
                company_id=campaign.company_id,
                campaign_id=campaign.id,
                name=f"QA Sample Lead {index + 1}",
                business=f"QA Sample Business {index + 1}",
                email=None,
                status=LeadStatus.generated,
            )
            db.add(lead)
            leads.append(lead)
        job = Job(
            employee_id=getattr(employee, "id", None),
            campaign_id=campaign.id,
            connector="hermes",
            task_type="Generate Lead Sample",
            status=JobStatus.completed,
            payload={"sample": True, "send_email": False, "limit": limit},
            result={"leads_generated": limit, "prospect_email_sent": 0},
            logs=["Generated QA sample leads only; no email was sent."],
            started_at=now,
            ended_at=now,
        )
    elif action == "send-internal-test" and campaign_type == "daily_reporting":
        recipient = campaign.report_recipient or campaign.internal_test_recipient or APPROVED_INTERNAL_RECIPIENT
        if recipient != APPROVED_INTERNAL_RECIPIENT:
            raise ValueError(f"Internal test recipient must be {APPROVED_INTERNAL_RECIPIENT}")
        job = Job(
            employee_id=getattr(employee, "id", None),
            campaign_id=campaign.id,
            connector="hermes",
            task_type="Daily Report Internal Test",
            status=JobStatus.completed,
            payload={"internal_test": True, "recipient": recipient, "send_email": False},
            result={"recipient": recipient, "email_sent": False, "message": "Internal test rendered without sending email."},
            logs=[f"Internal test restricted to {recipient}; no email sent during template QA."],
            started_at=now,
            ended_at=now,
        )
    elif action == "generate-sample-draft" and campaign_type == "outreach_drafting":
        job = Job(
            employee_id=getattr(employee, "id", None),
            campaign_id=campaign.id,
            connector="hermes",
            task_type="Generate Sample Draft",
            status=JobStatus.completed,
            payload={"draft_only": True, "send_email": False},
            result={"draft": "QA sample outreach draft generated for review only.", "email_sent": False},
            logs=["Generated sample draft only; no send action exists."],
            started_at=now,
            ended_at=now,
        )
    else:
        raise ValueError(f"Unsupported template action {action} for {campaign_type}")
    db.add(job)
    db.flush()
    log(db, "Campaign Template Sample", "Job", job.id, campaign.company_id, user_id, {"campaign_id": campaign.id, "action": action, "send_email": False})
    return job
