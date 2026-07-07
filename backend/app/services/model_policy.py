import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import (
    AIEmployee,
    Campaign,
    Company,
    CompanyModelPolicy,
    EmployeeModelPolicy,
    GlobalModelPolicy,
    ModelUsageAudit,
)
from app.services.hermes_control import HermesControlError, HermesControlService

DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
DEFAULT_NORMALIZED_MODEL = f"{DEFAULT_PROVIDER}/{DEFAULT_MODEL}"
DEFAULT_APPROVED_MODELS = [DEFAULT_NORMALIZED_MODEL, DEFAULT_MODEL]
DEFAULT_BLOCKED_MODELS = [
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "openai/gpt",
    "gpt",
    "google/gemini",
    "gemini",
    "google/gemini-pro",
    "google/gemini-1.5-pro",
    "google/gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]
UNAPPROVED_MARKERS = ("gpt", "openai/", "gemini", "google/gemini")
BIBS_JOB_IDS = {"0d0c20e25f55", "5881b72113ce", "47caae0a6a59", "b03a2d0f1149"}


def normalize_provider(value: str | None) -> str:
    return (value or DEFAULT_PROVIDER).strip().lower() or DEFAULT_PROVIDER


def normalize_model(provider: str | None, model: str | None) -> str:
    provider_value = normalize_provider(provider)
    model_value = (model or DEFAULT_MODEL).strip().lower()
    if not model_value:
        model_value = DEFAULT_MODEL
    if "/" in model_value and model_value.split("/", 1)[0] in {"openrouter", "openai", "google", "anthropic"}:
        return model_value
    if provider_value and not model_value.startswith(f"{provider_value}/"):
        return f"{provider_value}/{model_value}"
    return model_value


def default_policy_payload() -> dict[str, Any]:
    return {
        "provider": DEFAULT_PROVIDER,
        "model": DEFAULT_MODEL,
        "normalized_model": DEFAULT_NORMALIZED_MODEL,
        "approved_models": list(DEFAULT_APPROVED_MODELS),
        "blocked_models": list(DEFAULT_BLOCKED_MODELS),
        "fallback_enabled": False,
        "fail_closed": True,
        "daily_budget_usd": 0,
        "monthly_budget_usd": 0,
        "max_cost_per_run_usd": 0,
        "budget_guard": "unknown_provider_cost_is_audited; explicit positive limits are enforced",
    }


def ensure_global_policy(db: Session) -> GlobalModelPolicy:
    policy = db.scalar(select(GlobalModelPolicy).where(GlobalModelPolicy.name == "default"))
    if policy:
        changed = False
        if not policy.approved_models:
            policy.approved_models = list(DEFAULT_APPROVED_MODELS); changed = True
        blocked = set(policy.blocked_models or []) | set(DEFAULT_BLOCKED_MODELS)
        if set(policy.blocked_models or []) != blocked:
            policy.blocked_models = sorted(blocked); changed = True
        if policy.fallback_enabled:
            policy.fallback_enabled = False; changed = True
        if changed:
            policy.updated_at = datetime.utcnow()
        return policy
    policy = GlobalModelPolicy(
        name="default",
        provider=DEFAULT_PROVIDER,
        model=DEFAULT_MODEL,
        approved_models=list(DEFAULT_APPROVED_MODELS),
        blocked_models=list(DEFAULT_BLOCKED_MODELS),
        fallback_enabled=False,
        fail_closed=True,
        notes="Default Voryx policy: OpenRouter NVIDIA/Nemotron only; GPT/Gemini fallback disabled.",
    )
    db.add(policy)
    db.flush()
    return policy


def _merge(base: dict[str, Any], override: Any) -> dict[str, Any]:
    if not override:
        return base
    for key in ("provider", "model", "fallback_enabled", "fail_closed", "daily_budget_usd", "monthly_budget_usd", "max_cost_per_run_usd", "notes"):
        value = getattr(override, key, None) if not isinstance(override, dict) else override.get(key)
        if value is not None and value != "":
            base[key] = value
    for key in ("approved_models", "blocked_models"):
        value = getattr(override, key, None) if not isinstance(override, dict) else override.get(key)
        if value:
            base[key] = list(dict.fromkeys([*base.get(key, []), *value]))
    return base


def _job_policy_from_jobs_json(hermes_job_id: str | None) -> dict[str, Any] | None:
    if not hermes_job_id:
        return None
    try:
        raw = HermesControlService()._read_jobs()
    except Exception:
        return None
    jobs = raw if isinstance(raw, list) else raw.get("jobs", []) if isinstance(raw, dict) else []
    for job in jobs:
        if isinstance(job, dict) and str(job.get("id")) == hermes_job_id:
            policy = job.get("model_policy")
            return policy if isinstance(policy, dict) else None
    return None


def effective_policy(db: Session, *, company_id: str | None = None, campaign_id: str | None = None, employee_id: str | None = None, hermes_job_id: str | None = None, jobs_json_policy: dict[str, Any] | None = None) -> dict[str, Any]:
    global_policy = ensure_global_policy(db)
    policy = default_policy_payload()
    policy = _merge(policy, global_policy)
    employee = db.get(AIEmployee, employee_id) if employee_id else None
    if employee:
        company_id = company_id or employee.company_id
        campaign_id = campaign_id or employee.campaign_id
        hermes_job_id = hermes_job_id or employee.hermes_job_id
    campaign = db.get(Campaign, campaign_id) if campaign_id else None
    if campaign:
        company_id = company_id or campaign.company_id
    if company_id:
        company_policy = db.scalar(select(CompanyModelPolicy).where(CompanyModelPolicy.company_id == company_id))
        policy = _merge(policy, company_policy)
    if employee_id:
        employee_policy = db.scalar(select(EmployeeModelPolicy).where(EmployeeModelPolicy.employee_id == employee_id))
        policy = _merge(policy, employee_policy)
    policy = _merge(policy, jobs_json_policy or _job_policy_from_jobs_json(hermes_job_id))
    policy["provider"] = normalize_provider(policy.get("provider"))
    policy["model"] = (policy.get("model") or DEFAULT_MODEL).strip()
    policy["normalized_model"] = normalize_model(policy["provider"], policy["model"])
    policy["approved_models"] = list(dict.fromkeys([normalize_model(policy["provider"], value) for value in policy.get("approved_models") or DEFAULT_APPROVED_MODELS]))
    policy["blocked_models"] = list(dict.fromkeys([normalize_model(policy["provider"], value) for value in [*(policy.get("blocked_models") or []), *DEFAULT_BLOCKED_MODELS]]))
    policy["fallback_enabled"] = bool(policy.get("fallback_enabled"))
    policy["fail_closed"] = policy.get("fail_closed") is not False
    policy["company_id"] = company_id
    policy["campaign_id"] = campaign_id
    policy["employee_id"] = employee_id
    policy["hermes_job_id"] = hermes_job_id
    return policy


def _is_explicitly_blocked(normalized: str, blocked: list[str]) -> bool:
    lowered = normalized.lower()
    if lowered in {item.lower() for item in blocked}:
        return True
    return any(marker in lowered for marker in UNAPPROVED_MARKERS)


def validate_policy(policy: dict[str, Any], *, requested_provider: str | None = None, requested_model: str | None = None, estimated_cost_usd: int | None = None) -> dict[str, Any]:
    provider = normalize_provider(requested_provider or policy.get("provider"))
    model = requested_model or policy.get("model") or DEFAULT_MODEL
    normalized = normalize_model(provider, model)
    approved = [item.lower() for item in policy.get("approved_models") or []]
    blocked = policy.get("blocked_models") or []
    if policy.get("fallback_enabled"):
        return {"allowed": False, "status": "model_blocked", "reason": "Silent model fallback is disabled; fallback_enabled must remain false.", "normalized_model": normalized, "provider": provider, "model": model}
    if _is_explicitly_blocked(normalized, blocked):
        return {"allowed": False, "status": "model_blocked", "reason": f"Model {normalized} is blocked by Voryx model policy.", "normalized_model": normalized, "provider": provider, "model": model}
    if normalized.lower() not in approved:
        return {"allowed": False, "status": "model_unavailable", "reason": f"Model {normalized} is not in the approved allowlist.", "normalized_model": normalized, "provider": provider, "model": model}
    cost = estimated_cost_usd
    max_cost = int(policy.get("max_cost_per_run_usd") or 0)
    if cost is not None and max_cost > 0 and cost > max_cost:
        return {"allowed": False, "status": "budget_blocked", "reason": f"Estimated model cost {cost} exceeds per-run budget {max_cost}.", "normalized_model": normalized, "provider": provider, "model": model}
    return {"allowed": True, "status": "allowed", "reason": "Model policy allowed execution.", "normalized_model": normalized, "provider": provider, "model": model}


def record_model_audit(db: Session, policy: dict[str, Any], decision: dict[str, Any], *, task_type: str | None = None, metadata: dict[str, Any] | None = None, estimated_cost_usd: int | None = None) -> ModelUsageAudit:
    audit = ModelUsageAudit(
        company_id=policy.get("company_id"),
        campaign_id=policy.get("campaign_id"),
        employee_id=policy.get("employee_id"),
        hermes_job_id=policy.get("hermes_job_id"),
        provider=decision.get("provider") or policy.get("provider") or DEFAULT_PROVIDER,
        model=decision.get("model") or policy.get("model") or DEFAULT_MODEL,
        normalized_model=decision.get("normalized_model") or policy.get("normalized_model") or DEFAULT_NORMALIZED_MODEL,
        task_type=task_type,
        status=decision.get("status") or "allowed",
        reason=decision.get("reason"),
        estimated_cost_usd=estimated_cost_usd,
        metadata_json=metadata or {},
    )
    db.add(audit)
    db.flush()
    return audit


def guard_hermes_execution(db: Session, *, task_type: str, payload: dict[str, Any] | None, jobs_json_policy: dict[str, Any] | None = None, estimated_cost_usd: int | None = None) -> dict[str, Any]:
    payload = payload or {}
    hermes_job_id = str(payload.get("hermes_job_id") or payload.get("job_id") or payload.get("id") or "").strip() or None
    employee = db.scalar(select(AIEmployee).where(AIEmployee.hermes_job_id == hermes_job_id)) if hermes_job_id else None
    policy = effective_policy(db, employee_id=getattr(employee, "id", None), hermes_job_id=hermes_job_id, jobs_json_policy=jobs_json_policy)
    requested = payload.get("model_policy") if isinstance(payload.get("model_policy"), dict) else {}
    decision = validate_policy(policy, requested_provider=requested.get("provider"), requested_model=requested.get("model"), estimated_cost_usd=estimated_cost_usd)
    record_model_audit(db, policy, decision, task_type=task_type, metadata={"source": "runtime_guard", "payload_keys": sorted(payload.keys())}, estimated_cost_usd=estimated_cost_usd)
    return {"allowed": decision["allowed"], "decision": decision, "policy": policy}


def _policy_for_jobs_json(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": policy.get("provider") or DEFAULT_PROVIDER,
        "model": policy.get("model") or DEFAULT_MODEL,
        "normalized_model": policy.get("normalized_model") or DEFAULT_NORMALIZED_MODEL,
        "approved_models": policy.get("approved_models") or list(DEFAULT_APPROVED_MODELS),
        "blocked_models": policy.get("blocked_models") or list(DEFAULT_BLOCKED_MODELS),
        "fallback_enabled": False,
        "fail_closed": policy.get("fail_closed") is not False,
        "source": "voryx_model_policy",
        "updated_at": datetime.utcnow().isoformat(),
    }


def sync_model_policy_to_jobs_json(db: Session, *, hermes_job_id: str, employee_id: str | None = None, company_id: str | None = None, campaign_id: str | None = None) -> dict[str, Any]:
    policy = effective_policy(db, company_id=company_id, campaign_id=campaign_id, employee_id=employee_id, hermes_job_id=hermes_job_id)
    rendered = _policy_for_jobs_json(policy)
    service = HermesControlService()
    raw = service._read_jobs()
    job = service._find_job(raw, hermes_job_id)
    if not job:
        raise HermesControlError(f"Hermes job not found for model policy sync: {hermes_job_id}")
    before = job.get("model_policy")
    changed = before != rendered
    if changed:
        job["model_policy"] = rendered
        service._write_jobs(raw)
    verified = service._find_job(service._read_jobs(), hermes_job_id)
    ok = isinstance(verified, dict) and verified.get("model_policy") == rendered
    if not ok:
        raise HermesControlError(f"Hermes model policy verification failed for job {hermes_job_id}")
    return {"ok": True, "hermes_job_id": hermes_job_id, "changed": changed, "model_policy": rendered}


def sync_all_model_policies_to_jobs_json(db: Session) -> dict[str, Any]:
    results = []
    employees = db.scalars(select(AIEmployee).where(AIEmployee.hermes_job_id.is_not(None))).all()
    seen = set()
    for employee in employees:
        if not employee.hermes_job_id or employee.hermes_job_id in seen:
            continue
        seen.add(employee.hermes_job_id)
        try:
            results.append(sync_model_policy_to_jobs_json(db, hermes_job_id=employee.hermes_job_id, employee_id=employee.id, company_id=employee.company_id, campaign_id=employee.campaign_id))
        except Exception as exc:
            message = str(exc)
            skipped = "Hermes job not found" in message
            results.append({"ok": skipped, "skipped": skipped, "hermes_job_id": employee.hermes_job_id, "error": message})
    for job_id in BIBS_JOB_IDS - seen:
        try:
            results.append(sync_model_policy_to_jobs_json(db, hermes_job_id=job_id))
        except Exception as exc:
            message = str(exc)
            skipped = "Hermes job not found" in message
            results.append({"ok": skipped, "skipped": skipped, "hermes_job_id": job_id, "error": message})
    hard_failures = [item for item in results if not item.get("ok") and not item.get("skipped")]
    return {"ok": not hard_failures, "count": len(results), "skipped_count": sum(1 for item in results if item.get("skipped")), "results": results}


def write_company_workspace_policy(company_id: str, payload: dict[str, Any]) -> str | None:
    if not settings.hermes_data_path:
        return None
    root = Path(settings.hermes_data_path) / "home" / "voryx_workspaces" / company_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / "model_policy.json"
    safe_payload = {key: value for key, value in payload.items() if key not in {"api_key", "secret", "token"}}
    path.write_text(json.dumps(safe_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return f"/opt/data/home/voryx_workspaces/{company_id}/model_policy.json"


def policy_payload(policy: Any, effective: dict[str, Any] | None = None) -> dict[str, Any]:
    if policy is None:
        base = default_policy_payload()
    elif isinstance(policy, dict):
        base = dict(policy)
    else:
        base = {key: getattr(policy, key, None) for key in ["id", "name", "company_id", "employee_id", "campaign_id", "hermes_job_id", "provider", "model", "approved_models", "blocked_models", "fallback_enabled", "fail_closed", "daily_budget_usd", "monthly_budget_usd", "max_cost_per_run_usd", "notes", "updated_at", "created_at"]}
    if effective:
        base["effective"] = effective
    return base
