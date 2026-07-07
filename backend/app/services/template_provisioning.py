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
from app.services.hermes_jobs_json_executor import execute_scheduled_jobs_json_task
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT
from app.services.model_policy import default_policy_payload


APPROVED_INTERNAL_RECIPIENT = INTERNAL_REPORT_RECIPIENT
PROVISIONED_STATES = {"Provisioned", "Active", "Paused"}
CAMPAIGN_BLUEPRINTS = {"sales_outreach", "lead_generation", "custom"}
LEGACY_EMPLOYEE_CAMPAIGN_TYPES = {"lead_research", "daily_reporting", "outreach_drafting"}
TEMPLATE_TYPES = CAMPAIGN_BLUEPRINTS | LEGACY_EMPLOYEE_CAMPAIGN_TYPES
GENERIC_LEAD_RESEARCH_SCRIPT = "/opt/data/home/leads/voryx_generic_lead_research.py"
LOCKED_LEAD_FIELDS = [
    "lead_id",
    "created_at",
    "company_id",
    "campaign_id",
    "employee_id",
    "hermes_job_id",
    "source_run_id",
    "business_name",
    "website",
    "email",
    "phone",
    "city",
    "category",
    "lead_status",
    "verified_at",
    "source_url",
    "source_file",
    "notes",
]
DEFAULT_CUSTOM_LEAD_FIELDS = [
    "owner_name",
    "instagram",
    "google_rating",
    "number_of_locations",
    "decision_maker_title",
    "priority",
    "call_notes",
    "sms_status",
]
CAMPAIGN_BLUEPRINT_REGISTRY = {
    "sales_outreach": {
        "label": "Sales / Outreach Campaign",
        "required_fields": ["name", "industry", "geographic_area", "target_audience", "daily_lead_goal", "email_sending_disabled"],
        "description": "Business objective with lead research, internal reporting, and draft-only outreach workers.",
    },
    "lead_generation": {
        "label": "Lead Generation Campaign",
        "required_fields": ["name", "industry", "geographic_area", "target_audience", "daily_lead_goal", "email_sending_disabled"],
        "description": "Lead research objective with optional internal reporting.",
    },
    "custom": {
        "label": "Custom Campaign",
        "required_fields": ["name", "email_sending_disabled"],
        "description": "Database-only campaign. Employees require verified manual Hermes provisioning before activation.",
    },
}
EMPLOYEE_TEMPLATE_REGISTRY = {
    "lead_researcher": {
        "label": "Lead Researcher",
        "employee_types": ["Lead Researcher"],
        "required_fields": ["campaign.industry", "campaign.geographic_area", "campaign.target_audience", "daily_lead_goal", "lead_schema", "email_sending_disabled"],
        "disabled": False,
    },
    "daily_reporter": {
        "label": "Daily Reporter / CRM Manager",
        "employee_types": ["CRM Manager", "Report Manager", "Daily Reporter"],
        "required_fields": ["report_recipient", "timezone", "schedule_time", "internal_only"],
        "disabled": False,
    },
    "outreach_draft_writer": {
        "label": "Outreach Draft Writer",
        "employee_types": ["Email Outreach", "Draft Writer", "Outreach Draft Writer"],
        "required_fields": ["offer", "target_customer", "tone", "no_send_action"],
        "disabled": False,
    },
    "email_sender": {
        "label": "Email Sender",
        "employee_types": ["Email Sender"],
        "required_fields": ["approved_sender", "compliance", "approved_drafts", "limits"],
        "disabled": True,
        "reason": "Disabled until company outreach settings and compliance checks pass. Prospect sending remains locked by default.",
    },
    "reply_monitor": {
        "label": "Reply Monitor",
        "employee_types": ["Reply Monitor"],
        "required_fields": ["gmail_thread_monitoring"],
        "disabled": True,
        "reason": "Disabled until Gmail/thread monitoring is connected.",
    },
    "follow_up_manager": {
        "label": "Follow-up Manager",
        "employee_types": ["Follow-up Manager", "Followup Manager"],
        "required_fields": ["reply_monitor", "campaign_approval", "thread_id"],
        "disabled": True,
        "reason": "Disabled until Reply Monitor exists and campaign follow-up approval is configured.",
    },
    "reply_handler": {
        "label": "Reply Handler",
        "employee_types": ["Reply Handler"],
        "required_fields": [],
        "disabled": True,
        "reason": "Unavailable until Gmail/thread monitoring is implemented.",
    },
    "voice_agent": {
        "label": "Voice Agent",
        "employee_types": ["Voice Agent"],
        "required_fields": [],
        "disabled": True,
        "reason": "Unavailable until calling integration exists.",
    },
    "custom": {
        "label": "Custom",
        "employee_types": ["Custom"],
        "required_fields": ["manual_hermes_job_id"],
        "disabled": False,
    },
}
TEMPLATE_REGISTRY = {
    "lead_research": {"label": "Lead Researcher", "allowed_employee_types": ["Lead Researcher"], "required_fields": EMPLOYEE_TEMPLATE_REGISTRY["lead_researcher"]["required_fields"], "disabled": False},
    "daily_reporting": {"label": "Daily Reporter / CRM Manager", "allowed_employee_types": ["CRM Manager", "Report Manager", "Daily Reporter"], "required_fields": EMPLOYEE_TEMPLATE_REGISTRY["daily_reporter"]["required_fields"], "disabled": False},
    "outreach_drafting": {"label": "Outreach Draft Writer", "allowed_employee_types": ["Email Outreach", "Draft Writer", "Outreach Draft Writer"], "required_fields": EMPLOYEE_TEMPLATE_REGISTRY["outreach_draft_writer"]["required_fields"], "disabled": False},
    "reply_handler": {"label": "Reply Handler", "allowed_employee_types": ["Reply Handler"], "required_fields": [], "disabled": True, "reason": EMPLOYEE_TEMPLATE_REGISTRY["reply_handler"]["reason"]},
    "voice_agent": {"label": "Voice Agent", "allowed_employee_types": ["Voice Agent"], "required_fields": [], "disabled": True, "reason": EMPLOYEE_TEMPLATE_REGISTRY["voice_agent"]["reason"]},
    "custom": {"label": "Custom", "allowed_employee_types": ["Custom"], "required_fields": [], "disabled": False},
}


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


def _stable_job_id(campaign: Campaign, template_key: str | None = None, employee_id: str | None = None) -> str:
    key = template_key or campaign.campaign_type or "custom"
    digest = hashlib.sha1(f"{campaign.id}:{key}:{employee_id or ''}".encode("utf-8")).hexdigest()[:10]
    return f"voryx-template-{_slug(campaign.name)}-{_slug(key)}-{digest}"


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


def _lead_research_config(campaign: Campaign, employee: AIEmployee | None = None, hermes_job_id: str | None = None) -> dict[str, Any]:
    industry = (campaign.industry or "").strip()
    location = (campaign.geographic_area or "").strip()
    target = (campaign.target_audience or "").strip()
    if not industry:
        raise ValueError("Lead Research template requires Industry / niche.")
    if not location:
        raise ValueError("Lead Research template requires City / region.")
    if not target:
        raise ValueError("Lead Research template requires Target customer.")
    if int(campaign.daily_lead_goal or 0) <= 0:
        raise ValueError("Lead Research template requires Lead count greater than 0.")
    if campaign.daily_email_goal or campaign.daily_email_limit or campaign.dry_run_mode is False:
        raise ValueError("Lead Research template requires email sending disabled.")
    limit = max(int(campaign.daily_lead_goal or 0), 1)
    result = campaign.provisioning_result if isinstance(campaign.provisioning_result, dict) else {}
    schema = normalize_lead_schema(result)
    lead_source = result.get("lead_source") if isinstance(result.get("lead_source"), dict) else {}
    return {
        "company_id": campaign.company_id,
        "campaign_id": campaign.id,
        "employee_id": getattr(employee, "id", None),
        "hermes_job_id": hermes_job_id or getattr(employee, "hermes_job_id", None),
        "industry": industry,
        "location": location,
        "target_customer": target,
        "exclude": (campaign.description or "").strip(),
        "limit": limit,
        "notes": (campaign.provisioning_result or {}).get("notes") if isinstance(campaign.provisioning_result, dict) else None,
        "email_sending": False,
        "prospect_outreach": False,
        "lead_schema": schema,
        "lead_source": {
            "type": str(lead_source.get("type") or "").strip(),
            "file": str(lead_source.get("file") or "").strip(),
            "url": str(lead_source.get("url") or "").strip(),
            "query": str(lead_source.get("query") or "").strip(),
        },
    }


def _write_lead_research_config(campaign: Campaign, workspace: str, employee: AIEmployee | None = None, hermes_job_id: str | None = None) -> str:
    config = _lead_research_config(campaign, employee, hermes_job_id)
    path = _container_to_data_path(f"{workspace}/lead_research_config.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f"{workspace}/lead_research_config.json"


def _lead_research_command(campaign: Campaign, workspace: str, employee: AIEmployee | None = None, hermes_job_id: str | None = None) -> tuple[str, str, dict[str, Any]]:
    _ensure_generic_lead_script()
    config = _lead_research_config(campaign, employee, hermes_job_id)
    config_path = _write_lead_research_config(campaign, workspace, employee, hermes_job_id)
    output_dir = f"{workspace}/leads"
    _container_to_data_path(output_dir).mkdir(parents=True, exist_ok=True)
    command = " ".join([
        "python3",
        quote(GENERIC_LEAD_RESEARCH_SCRIPT),
        "--company-id",
        quote(config["company_id"]),
        "--campaign-id",
        quote(config["campaign_id"]),
        "--employee-id",
        quote(str(config.get("employee_id") or "")),
        "--hermes-job-id",
        quote(str(config.get("hermes_job_id") or "")),
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
        "--config",
        quote(config_path),
        "--notes",
        quote(str(config.get("notes") or "")),
        "--no-email",
    ])
    config["config_path"] = config_path
    config["output_dir"] = output_dir
    config["script"] = GENERIC_LEAD_RESEARCH_SCRIPT
    return command, output_dir, config


def normalize_lead_schema(source: dict[str, Any] | None = None) -> dict[str, Any]:
    source = source or {}
    raw_schema = source.get("lead_schema") if isinstance(source.get("lead_schema"), dict) else source
    custom_source = raw_schema.get("custom_fields") if isinstance(raw_schema, dict) else None
    custom_fields = []
    if isinstance(custom_source, list):
        for item in custom_source:
            if isinstance(item, dict):
                name = _slug(str(item.get("name") or item.get("key") or "")).replace("-", "_")
                if not name or name in LOCKED_LEAD_FIELDS:
                    continue
                custom_fields.append({
                    "name": name,
                    "label": str(item.get("label") or name.replace("_", " ").title()),
                    "hidden": bool(item.get("hidden", False)),
                    "order": int(item.get("order") or len(custom_fields) + 1),
                })
            elif isinstance(item, str):
                name = _slug(item).replace("-", "_")
                if name and name not in LOCKED_LEAD_FIELDS:
                    custom_fields.append({"name": name, "label": name.replace("_", " ").title(), "hidden": False, "order": len(custom_fields) + 1})
    if not custom_fields:
        custom_fields = [{"name": name, "label": name.replace("_", " ").title(), "hidden": False, "order": index + 1} for index, name in enumerate(DEFAULT_CUSTOM_LEAD_FIELDS)]
    custom_fields = sorted(custom_fields, key=lambda item: item.get("order", 0))
    return {
        "locked_fields": LOCKED_LEAD_FIELDS,
        "custom_fields": custom_fields,
        "columns": LOCKED_LEAD_FIELDS + [field["name"] for field in custom_fields if not field.get("hidden")],
    }


def update_campaign_lead_schema(campaign: Campaign, schema: dict[str, Any]) -> dict[str, Any]:
    next_schema = normalize_lead_schema({"lead_schema": schema})
    result = dict(campaign.provisioning_result or {})
    result["lead_schema"] = next_schema
    campaign.provisioning_result = result
    if _campaign_supports_lead_research(campaign):
        workspace = _campaign_workspace(campaign)
        _write_lead_research_config(campaign, workspace)
    return next_schema


def _campaign_supports_lead_research(campaign: Campaign | None) -> bool:
    if not campaign:
        return False
    return bool((campaign.industry or "").strip() and (campaign.geographic_area or "").strip() and (campaign.target_audience or "").strip() and int(campaign.daily_lead_goal or 0) > 0 and campaign.dry_run_mode is not False and not (campaign.daily_email_goal or campaign.daily_email_limit))


def _campaign_supports_daily_reporter(campaign: Campaign | None) -> bool:
    if not campaign:
        return False
    return bool((campaign.report_recipient or campaign.internal_test_recipient or "").strip() and (campaign.timezone or "").strip())


def _campaign_supports_outreach_draft(campaign: Campaign | None) -> bool:
    if not campaign:
        return False
    return bool((campaign.target_audience or "").strip() and (campaign.description or "").strip() and campaign.dry_run_mode is not False and not (campaign.daily_email_goal or campaign.daily_email_limit))


def _employee_template_key(employee_type: str | None) -> str:
    value = (employee_type or "Custom").strip().lower()
    if value in {"lead researcher", "lead_researcher"}:
        return "lead_researcher"
    if value in {"crm manager", "report manager", "daily reporter", "daily_reporter"}:
        return "daily_reporter"
    if value in {"email outreach", "draft writer", "outreach draft writer", "outreach_draft_writer"}:
        return "outreach_draft_writer"
    if value in {"email sender", "email_sender"}:
        return "email_sender"
    if value in {"reply monitor", "reply_monitor"}:
        return "reply_monitor"
    if value in {"follow-up manager", "followup manager", "follow_up_manager"}:
        return "follow_up_manager"
    if value in {"reply handler", "reply_handler"}:
        return "reply_handler"
    if value in {"voice agent", "voice_agent"}:
        return "voice_agent"
    return "custom"


def template_registry_payload() -> dict[str, Any]:
    return {
        "campaign_blueprints": CAMPAIGN_BLUEPRINT_REGISTRY,
        "employee_templates": {
            key: {
                **value,
                "locked_lead_fields": LOCKED_LEAD_FIELDS if key == "lead_researcher" else [],
                "default_custom_lead_fields": DEFAULT_CUSTOM_LEAD_FIELDS if key == "lead_researcher" else [],
            }
            for key, value in EMPLOYEE_TEMPLATE_REGISTRY.items()
        },
        "legacy_campaign_templates": {
            key: {
                **value,
                "locked_lead_fields": LOCKED_LEAD_FIELDS if key == "lead_research" else [],
                "default_custom_lead_fields": DEFAULT_CUSTOM_LEAD_FIELDS if key == "lead_research" else [],
            }
            for key, value in TEMPLATE_REGISTRY.items()
        },
    }


def allowed_employee_types_for_campaign(campaign: Campaign | None) -> list[str]:
    if not campaign:
        return ["Custom"]
    campaign_type = (getattr(campaign, "campaign_type", None) or "custom").strip().lower()
    if campaign_type in LEGACY_EMPLOYEE_CAMPAIGN_TYPES:
        entry = TEMPLATE_REGISTRY.get(campaign_type) or TEMPLATE_REGISTRY["custom"]
        return list(entry["allowed_employee_types"])
    allowed: list[str] = []
    if _campaign_supports_lead_research(campaign):
        allowed.extend(EMPLOYEE_TEMPLATE_REGISTRY["lead_researcher"]["employee_types"])
    if _campaign_supports_daily_reporter(campaign):
        allowed.extend(EMPLOYEE_TEMPLATE_REGISTRY["daily_reporter"]["employee_types"])
    if _campaign_supports_outreach_draft(campaign):
        allowed.extend(EMPLOYEE_TEMPLATE_REGISTRY["outreach_draft_writer"]["employee_types"])
    if campaign_type == "sales_outreach":
        allowed.extend(EMPLOYEE_TEMPLATE_REGISTRY["email_sender"]["employee_types"])
        allowed.extend(EMPLOYEE_TEMPLATE_REGISTRY["reply_monitor"]["employee_types"])
        allowed.extend(EMPLOYEE_TEMPLATE_REGISTRY["follow_up_manager"]["employee_types"])
    allowed.append("Custom")
    return list(dict.fromkeys(allowed))


def validate_campaign_blueprint(campaign: Campaign) -> None:
    campaign.campaign_type = (campaign.campaign_type or "custom").strip().lower()
    if campaign.campaign_type in LEGACY_EMPLOYEE_CAMPAIGN_TYPES:
        return
    if campaign.campaign_type not in CAMPAIGN_BLUEPRINTS:
        raise ValueError(f"Unsupported campaign blueprint: {campaign.campaign_type}")
    if not (campaign.name or "").strip():
        raise ValueError("Campaign name is required.")
    if campaign.dry_run_mode is False or campaign.daily_email_goal or campaign.daily_email_limit:
        raise ValueError("Campaign email sending must remain disabled.")
    if campaign.campaign_type in {"sales_outreach", "lead_generation"}:
        if not (campaign.industry or "").strip():
            raise ValueError("Campaign requires Industry / niche.")
        if not (campaign.geographic_area or "").strip():
            raise ValueError("Campaign requires City / region.")
        if not (campaign.target_audience or "").strip():
            raise ValueError("Campaign requires Target customer.")
        if int(campaign.daily_lead_goal or 0) <= 0:
            raise ValueError("Campaign requires Lead goal greater than 0.")
    result = dict(campaign.provisioning_result or {})
    result.setdefault("campaign_blueprint", campaign.campaign_type)
    result.setdefault("lead_schema", normalize_lead_schema(result))
    result.setdefault("message", "Campaign blueprint saved. Add employee templates to provision Hermes jobs.")
    campaign.provisioning_result = result
    if campaign.provisioning_state in {None, "Provisioned", "Provisioning"}:
        campaign.provisioning_state = "Draft"

def verify_hermes_job_exists(hermes_job_id: str | None) -> bool:
    if not hermes_job_id:
        return False
    try:
        raw = HermesControlService()._read_jobs()
        return HermesControlService()._find_job(raw, hermes_job_id) is not None
    except Exception:
        return False


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
        "model_policy": default_policy_payload(),
    }


def provision_campaign_template(db: Session, campaign: Campaign, user_id: str | None = None) -> dict[str, Any]:
    campaign.campaign_type = (campaign.campaign_type or "custom").strip().lower()
    if campaign.campaign_type not in TEMPLATE_TYPES:
        raise ValueError(f"Unsupported campaign template: {campaign.campaign_type}")
    entry = TEMPLATE_REGISTRY.get(campaign.campaign_type)
    if entry and entry.get("disabled"):
        raise ValueError(f"{entry['label']} template is unavailable: {entry.get('reason')}")
    if campaign.campaign_type == "custom":
        campaign.provisioning_state = campaign.provisioning_state or "Draft"
        campaign.provisioning_result = {"provisioned": False, "message": "Custom campaign requires manual Hermes provisioning."}
        return campaign.provisioning_result
    if campaign.campaign_type == "lead_research":
        _lead_research_config(campaign)
    if campaign.campaign_type == "daily_reporting":
        recipient = campaign.report_recipient or campaign.internal_test_recipient
        if not recipient:
            raise ValueError("Daily Reporting template requires report_recipient.")
        if not campaign.timezone:
            raise ValueError("Daily Reporting template requires timezone.")
        if recipient != APPROVED_INTERNAL_RECIPIENT:
            raise ValueError(f"Daily Reporting template recipient must be {APPROVED_INTERNAL_RECIPIENT}")
    if campaign.campaign_type == "outreach_drafting":
        if not (campaign.description or "").strip():
            raise ValueError("Outreach Drafting template requires offer/tone in Exclusions / notes.")
        if not (campaign.target_audience or "").strip():
            raise ValueError("Outreach Drafting template requires target_customer.")
        if campaign.daily_email_goal or campaign.daily_email_limit or campaign.dry_run_mode is False:
            raise ValueError("Outreach Drafting template requires email sending disabled.")
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
        **(campaign.provisioning_result or {}),
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



def _employee_template_spec(campaign: Campaign, employee: AIEmployee, job_id: str) -> TemplateSpec | None:
    workspace = _campaign_workspace(campaign)
    key = _employee_template_key(employee.employee_type)
    if key == "lead_researcher":
        command, output_dir, config = _lead_research_command(campaign, workspace, employee, job_id)
        return TemplateSpec(
            employee_type="Lead Researcher",
            task_type="Generate Leads",
            schedule_name=f"{campaign.name} Lead Research",
            cron="0 13 * * *",
            command=command,
            working_directory=workspace,
            description="Voryx Lead Researcher job. Generates lead CSV files only and never sends email.",
            safety={"prospect_outreach": False, "email_sending": False, "max_sample_leads": 5, "script": GENERIC_LEAD_RESEARCH_SCRIPT, "config": config},
            prompt=f"Generate lead research only for {config['industry']} in {config['location']}. Email sending disabled.",
        )
    if key == "daily_reporter":
        recipient = campaign.report_recipient or campaign.internal_test_recipient or APPROVED_INTERNAL_RECIPIENT
        return TemplateSpec(
            employee_type="CRM Manager" if employee.employee_type not in {"Report Manager", "Daily Reporter"} else employee.employee_type,
            task_type="Daily Report",
            schedule_name=f"{campaign.name} Daily Reporting",
            cron="0 23 * * *",
            command=f"python3 /opt/data/home/leads/generate_daily_report.py --recipient {quote(recipient)} --send-email false",
            working_directory=workspace,
            description="Voryx Daily Reporter job. Internal reports only.",
            safety={"prospect_outreach": False, "approved_recipient": APPROVED_INTERNAL_RECIPIENT, "send_email_default": False, "internal_only": True},
            prompt="Prepare internal daily reporting only for the approved recipient.",
        )
    if key == "outreach_draft_writer":
        return TemplateSpec(
            employee_type="Email Outreach" if employee.employee_type not in {"Draft Writer", "Outreach Draft Writer"} else employee.employee_type,
            task_type="Draft Outreach",
            schedule_name=f"{campaign.name} Outreach Drafting",
            cron="0 15 * * *",
            command=f"python3 /opt/data/home/leads/draft_outreach.py --output-dir {quote(workspace + '/drafts')} --no-send",
            working_directory=workspace,
            description="Voryx Outreach Draft Writer job. Draft-only automation with no send action.",
            safety={"prospect_outreach": False, "email_sending": False, "draft_only": True},
            prompt="Generate outreach draft copy only. Never send email.",
        )
    return None


def _validate_employee_template_requirements(campaign: Campaign, employee: AIEmployee) -> None:
    key = _employee_template_key(employee.employee_type)
    if key == "email_sender":
        employee.status = EmployeeStatus.paused
        employee.dry_run_mode = True
        employee.daily_email_limit = 0
        employee.paused_reason = "Email Sender disabled until outreach settings, approved drafts and compliance pass."
        return
    if key == "reply_monitor":
        employee.status = EmployeeStatus.paused
        employee.paused_reason = "Reply Monitor disabled until Gmail/thread monitoring is connected."
        return
    if key == "follow_up_manager":
        employee.status = EmployeeStatus.paused
        employee.paused_reason = "Follow-up Manager disabled until Reply Monitor exists and follow-up approval is configured."
        return
    if key == "reply_handler":
        raise ValueError("Reply Handler is disabled until Gmail/thread monitoring is implemented.")
    if key == "voice_agent":
        raise ValueError("Voice Agent is disabled until calling integration exists.")
    if key == "custom":
        if employee.status in {EmployeeStatus.running, EmployeeStatus.scheduled} and not employee.hermes_job_id:
            raise ValueError("Custom employees cannot become active without a verified Hermes job ID.")
        return
    if employee.employee_type not in allowed_employee_types_for_campaign(campaign):
        raise ValueError(f"{employee.employee_type} is not valid for this campaign until required fields are complete. Allowed now: {', '.join(allowed_employee_types_for_campaign(campaign))}")
    if key == "lead_researcher":
        _lead_research_config(campaign, employee, employee.hermes_job_id)
    if key == "daily_reporter":
        recipient = campaign.report_recipient or campaign.internal_test_recipient
        if not recipient:
            raise ValueError("Daily Reporter requires report_recipient on the campaign.")
        if recipient != APPROVED_INTERNAL_RECIPIENT:
            raise ValueError(f"Daily Reporter recipient must be {APPROVED_INTERNAL_RECIPIENT}")
        if not campaign.timezone:
            raise ValueError("Daily Reporter requires campaign timezone.")
    if key == "outreach_draft_writer":
        if not (campaign.description or "").strip():
            raise ValueError("Outreach Draft Writer requires offer/product and tone in campaign notes.")
        if campaign.daily_email_goal or campaign.daily_email_limit or campaign.dry_run_mode is False:
            raise ValueError("Outreach Draft Writer requires no send action and email sending disabled.")


def provision_employee_template(db: Session, employee: AIEmployee, user_id: str | None = None) -> dict[str, Any] | None:
    if not employee.campaign_id:
        return None
    campaign = db.get(Campaign, employee.campaign_id)
    if not campaign:
        raise ValueError("Employee template requires an existing campaign.")
    key = _employee_template_key(employee.employee_type)
    if key == "custom":
        return None
    if key in {"email_sender", "reply_monitor", "follow_up_manager"}:
        _validate_employee_template_requirements(campaign, employee)
        return {"provisioned": False, "employee_template": key, "message": employee.paused_reason, "manual_control_only": True}
    _validate_employee_template_requirements(campaign, employee)
    job_id = employee.hermes_job_id or _stable_job_id(campaign, key, employee.id)
    employee.hermes_job_id = job_id
    spec = _employee_template_spec(campaign, employee, job_id)
    if not spec:
        raise ValueError(f"No provisioning spec for employee template: {employee.employee_type}")
    employee.employee_type = spec.employee_type
    employee.approved_script = spec.command
    employee.working_directory = spec.working_directory
    employee.prompt = employee.prompt or spec.prompt
    employee.daily_limits = {**(employee.daily_limits or {}), "employee_template": key, "campaign_blueprint": campaign.campaign_type, "hermes_job_id": job_id, "safety": spec.safety}
    employee.status = EmployeeStatus.paused
    employee.dry_run_mode = True
    employee.daily_email_limit = 0 if key != "daily_reporter" else 1
    employee.paused_reason = "Employee template provisioned paused; resume schedule only after review."

    schedule = db.scalar(select(Schedule).where(Schedule.employee_id == employee.id, Schedule.payload["hermes_job_id"].as_string() == job_id))
    if not schedule:
        schedule = Schedule(
            employee_id=employee.id,
            name=spec.schedule_name,
            cron=spec.cron,
            timezone=campaign.timezone or "America/Toronto",
            task_type=spec.task_type,
            payload={"source": "voryx_employee_template", "hermes_job_id": job_id, "employee_template": key, "campaign_blueprint": campaign.campaign_type, "safety": spec.safety},
            is_paused=True,
        )
        db.add(schedule)
        db.flush()
    else:
        schedule.name = spec.schedule_name
        schedule.cron = spec.cron
        schedule.timezone = campaign.timezone or "America/Toronto"
        schedule.task_type = spec.task_type
        schedule.payload = {**(schedule.payload or {}), "source": "voryx_employee_template", "hermes_job_id": job_id, "employee_template": key, "campaign_blueprint": campaign.campaign_type, "safety": spec.safety}
        schedule.is_paused = True

    desired_job = _desired_job(campaign, employee, schedule, spec, job_id)
    desired_job["source"] = "voryx_employee_template"
    desired_job["name"] = f"voryx-{_slug(campaign.name)}-{_slug(key)}"
    desired_job["employee_template"] = key
    control = HermesControlService().upsert_provisioned_job(desired_job)
    result = dict(campaign.provisioning_result or {})
    employees = result.get("employees") if isinstance(result.get("employees"), list) else []
    employees = [item for item in employees if not (isinstance(item, dict) and item.get("employee_id") == employee.id)]
    employees.append({"employee_id": employee.id, "employee_template": key, "hermes_job_id": job_id, "schedule_id": schedule.id, "task_type": spec.task_type})
    result.update({
        "campaign_blueprint": campaign.campaign_type,
        "provisioned": True,
        "employees": employees,
        "lead_schema": normalize_lead_schema(result),
        "message": "Employee template provisioned disabled/paused Hermes job and paused Voryx schedule.",
    })
    if key == "lead_researcher":
        result["latest_lead_research_job_id"] = job_id
        result["working_directory"] = spec.working_directory
    campaign.provisioning_result = result
    if campaign.provisioning_state in {"Draft", "Provisioning", "Provisioning Failed", None}:
        campaign.provisioning_state = "Provisioned"
    log(db, "Employee Template Provisioned", "AIEmployee", employee.id, employee.company_id, user_id, {"employee_template": key, "hermes_job_id": job_id, "hermes_control": control})
    return {"provisioned": True, "employee_template": key, "hermes_job_id": job_id, "schedule_id": schedule.id, "hermes_control": control}

def mark_provisioning_failed(campaign: Campaign, exc: Exception) -> None:
    campaign.provisioning_state = "Provisioning Failed"
    campaign.provisioning_result = {"provisioned": False, "error": str(exc)}


def validate_employee_operational_state(db: Session, employee: AIEmployee, next_status: EmployeeStatus | str | None = None) -> None:
    status = next_status if isinstance(next_status, EmployeeStatus) else None
    if status is None and next_status is not None:
        status = next((member for member in EmployeeStatus if str(next_status) in {member.value, member.name}), None)
    status = status or employee.status
    campaign = db.get(Campaign, employee.campaign_id) if employee.campaign_id else None
    if campaign:
        allowed_types = allowed_employee_types_for_campaign(campaign)
        if employee.employee_type not in allowed_types:
            if status in {EmployeeStatus.running, EmployeeStatus.scheduled}:
                raise ValueError(f"{employee.employee_type} is not allowed for {campaign.campaign_type}. Allowed: {', '.join(allowed_types)}")
    hermes_job_id = (employee.hermes_job_id or "").strip()
    if status in {EmployeeStatus.running, EmployeeStatus.scheduled} and not hermes_job_id:
        raise ValueError("Employee cannot become Running or Scheduled without a Hermes job ID.")
    if status in {EmployeeStatus.running, EmployeeStatus.scheduled} and not verify_hermes_job_exists(hermes_job_id):
        raise ValueError("Employee cannot become Running or Scheduled until the Hermes job exists in jobs.json.")
    if status == EmployeeStatus.scheduled:
        schedule = db.scalar(select(Schedule).where(Schedule.employee_id == employee.id).limit(1))
        if not schedule:
            raise ValueError("Employee cannot become Scheduled without a schedule.")


def create_template_sample_job(db: Session, campaign: Campaign, action: str, user_id: str | None = None) -> Job:
    campaign_type = (campaign.campaign_type or "custom").strip().lower()
    employees = db.scalars(select(AIEmployee).where(AIEmployee.campaign_id == campaign.id).order_by(AIEmployee.name)).all()
    lead_employee = next((item for item in employees if _employee_template_key(item.employee_type) == "lead_researcher"), None)
    report_employee = next((item for item in employees if _employee_template_key(item.employee_type) == "daily_reporter"), None)
    draft_employee = next((item for item in employees if _employee_template_key(item.employee_type) == "outreach_draft_writer"), None)
    if campaign_type == "lead_research" and not lead_employee:
        lead_employee = db.scalar(select(AIEmployee).where(AIEmployee.campaign_id == campaign.id).order_by(AIEmployee.name).limit(1))
    if campaign_type == "daily_reporting" and not report_employee:
        report_employee = db.scalar(select(AIEmployee).where(AIEmployee.campaign_id == campaign.id).order_by(AIEmployee.name).limit(1))
    if campaign_type == "outreach_drafting" and not draft_employee:
        draft_employee = db.scalar(select(AIEmployee).where(AIEmployee.campaign_id == campaign.id).order_by(AIEmployee.name).limit(1))
    now = datetime.utcnow()
    if action == "generate-sample":
        employee = lead_employee
        if not employee or not employee.hermes_job_id:
            raise ValueError("Lead Research sample requires a provisioned Lead Researcher Hermes job.")
        _lead_research_config(campaign, employee, employee.hermes_job_id)
        requested_limit = min(max(int(campaign.daily_lead_goal or 5), 1), 5)
        result = execute_scheduled_jobs_json_task(
            "Generate Leads",
            {
                "source": "dashboard_generate_sample",
                "hermes_job_id": employee.hermes_job_id,
                "campaign_type": "lead_research",
                "employee_template": "lead_researcher",
                "sample": True,
                "limit": requested_limit,
                "send_email": False,
            },
        )
        status = JobStatus.completed if result.get("status") == "ok" else JobStatus.failed
        job = Job(
            employee_id=getattr(employee, "id", None),
            campaign_id=campaign.id,
            connector="hermes",
            task_type="Generate Lead Sample",
            status=status,
            payload={"sample": True, "send_email": False, "limit": requested_limit, "hermes_job_id": employee.hermes_job_id},
            result=result.get("results", result),
            logs=result.get("logs", []),
            error_message=result.get("error") if status == JobStatus.failed else None,
            started_at=now,
            ended_at=datetime.utcnow(),
        )
    elif action == "send-internal-test":
        employee = report_employee
        if not employee or not employee.hermes_job_id:
            raise ValueError("Internal report test requires a provisioned Daily Reporter Hermes job.")
        recipient = campaign.report_recipient or campaign.internal_test_recipient or APPROVED_INTERNAL_RECIPIENT
        if recipient != APPROVED_INTERNAL_RECIPIENT:
            raise ValueError(f"Internal test recipient must be {APPROVED_INTERNAL_RECIPIENT}")
        job = Job(
            employee_id=getattr(employee, "id", None),
            campaign_id=campaign.id,
            connector="hermes",
            task_type="Daily Report Internal Test",
            status=JobStatus.completed,
            payload={"internal_test": True, "recipient": recipient, "send_email": False, "hermes_job_id": employee.hermes_job_id},
            result={"recipient": recipient, "email_sent": False, "message": "Internal test rendered without sending email."},
            logs=[f"Internal test restricted to {recipient}; no email sent during template QA."],
            started_at=now,
            ended_at=now,
        )
    elif action == "generate-sample-draft":
        employee = draft_employee
        if not employee or not employee.hermes_job_id:
            raise ValueError("Sample draft requires a provisioned Outreach Draft Writer Hermes job.")
        job = Job(
            employee_id=getattr(employee, "id", None),
            campaign_id=campaign.id,
            connector="hermes",
            task_type="Generate Sample Draft",
            status=JobStatus.completed,
            payload={"draft_only": True, "send_email": False, "hermes_job_id": employee.hermes_job_id},
            result={"draft": "QA sample outreach draft generated for review only.", "email_sent": False},
            logs=["Generated sample draft only; no send action exists."],
            started_at=now,
            ended_at=now,
        )
    else:
        raise ValueError(f"Unsupported template action {action} for campaign {campaign.id}")
    db.add(job)
    db.flush()
    log(db, "Campaign Template Sample", "Job", job.id, campaign.company_id, user_id, {"campaign_id": campaign.id, "action": action, "send_email": False})
    return job
