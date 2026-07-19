import json
import os
import re
import shlex
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.job_evidence import INTERNAL_REPORT_RECIPIENT, validate_report_recipient
from app.core.db import SessionLocal
from app.services.model_policy import guard_hermes_execution

LEAD_RESEARCH_JOB_ID = "0d0c20e25f55"
DAILY_REPORT_JOB_ID = "5881b72113ce"
OUTREACH_DRAFT_JOB_ID = "47caae0a6a59"
OUTREACH_FOLLOWUP_JOB_ID = "b03a2d0f1149"
APPROVED_JOB_IDS = {LEAD_RESEARCH_JOB_ID, DAILY_REPORT_JOB_ID}
BLOCKED_JOB_IDS = {OUTREACH_DRAFT_JOB_ID, OUTREACH_FOLLOWUP_JOB_ID}
GENERIC_LEAD_RESEARCH_SCRIPT = "/opt/data/home/leads/voryx_generic_lead_research.py"
HERMES_NATIVE_BROWSER_SCRIPT = "/opt/data/home/leads/hermes_native_browser_research.py"
BIBS_LEAD_SOURCE_CONFIG = "bibs_real_lead_source_config.json"
BIBS_CAMPAIGN_ID = "campaign-brew-it-by-sash-lead-research"
BIBS_COMPANY_ID = "company-brew-it-by-sash"

DATA_ROOT = Path(os.getenv("HERMES_EXECUTION_DATA_PATH", "/opt/data"))
LEADS_DIR = DATA_ROOT / "home" / "leads"
MAIL_QUEUE_DIR = DATA_ROOT / "home" / "voryx_mail_queue"
CRON_OUTPUT_DIR = DATA_ROOT / "cron" / "output"


def execute_scheduled_jobs_json_task(task_type: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    hermes_job_id = _hermes_job_id(payload)
    if not hermes_job_id:
        return _unsupported("jobs_json scheduled execution requires payload.hermes_job_id")
    if hermes_job_id in BLOCKED_JOB_IDS:
        return _unsupported(f"Hermes job {hermes_job_id} is not executable from Voryx scheduled execution")
    if hermes_job_id not in APPROVED_JOB_IDS and not _is_generic_lead_research_job(hermes_job_id):
        return _unsupported(f"Hermes job {hermes_job_id} is not approved for jobs_json scheduled execution")

    if _payload_mentions_send_outreach(payload):
        return _failed("Blocked unsafe scheduled execution: send_outreach.py is not allowed")

    guard_result = _model_policy_guard(task_type, payload, _jobs_json_entry(hermes_job_id))
    if not guard_result.get("allowed"):
        result = _model_policy_blocked(hermes_job_id, task_type, payload, guard_result)
        _record_jobs_json_execution(hermes_job_id, result)
        return result

    if hermes_job_id == LEAD_RESEARCH_JOB_ID:
        result = _execute_lead_research(task_type, payload)
        _record_jobs_json_execution(hermes_job_id, result)
        return result
    if _is_generic_lead_research_job(hermes_job_id):
        result = _execute_generic_lead_research(hermes_job_id, task_type, payload)
        _record_jobs_json_execution(hermes_job_id, result)
        return result
    if hermes_job_id == DAILY_REPORT_JOB_ID:
        result = _execute_daily_report(task_type, payload)
        _record_jobs_json_execution(hermes_job_id, result)
        return result
    return _unsupported(f"No jobs_json executor is registered for Hermes job {hermes_job_id}")



def _model_policy_guard(task_type: str, payload: dict[str, Any], job: dict[str, Any] | None) -> dict[str, Any]:
    db = SessionLocal()
    try:
        policy = job.get("model_policy") if isinstance(job, dict) and isinstance(job.get("model_policy"), dict) else None
        result = guard_hermes_execution(db, task_type=task_type, payload=payload, jobs_json_policy=policy)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        return {"allowed": False, "decision": {"status": "model_policy_error", "reason": f"Model policy guard failed closed: {exc}", "normalized_model": "unknown"}, "policy": {}}
    finally:
        db.close()


def _model_policy_blocked(hermes_job_id: str, task_type: str, payload: dict[str, Any], guard_result: dict[str, Any]) -> dict[str, Any]:
    decision = guard_result.get("decision") if isinstance(guard_result.get("decision"), dict) else {}
    policy = guard_result.get("policy") if isinstance(guard_result.get("policy"), dict) else {}
    reason = str(decision.get("reason") or "Model policy blocked execution")
    status = str(decision.get("status") or "model_blocked")
    output_record = _write_cron_output(
        hermes_job_id,
        "Hermes execution blocked by Voryx model policy",
        task_type,
        payload,
        [reason],
        {"hermes_job_id": hermes_job_id, "status": status, "reason": reason, "provider": decision.get("provider"), "model": decision.get("model"), "normalized_model": decision.get("normalized_model")},
    )
    return {
        "status": "blocked",
        "error": reason,
        "logs": [f"MODEL_POLICY_BLOCKED status={status} model={decision.get('normalized_model') or policy.get('normalized_model')}: {reason}", f"HERMES_OUTPUT_WRITTEN path={output_record}"],
        "results": {
            "hermes_job_id": hermes_job_id,
            "status": status,
            "reason": reason,
            "provider": decision.get("provider") or policy.get("provider"),
            "model": decision.get("model") or policy.get("model"),
            "normalized_model": decision.get("normalized_model") or policy.get("normalized_model"),
            "hermes_output_path": str(output_record),
            "prospect_emails_sent": 0,
        },
    }

def _hermes_job_id(payload: dict[str, Any]) -> str:
    for key in ("hermes_job_id", "job_id", "id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    nested = payload.get("hermes")
    if isinstance(nested, dict):
        return _hermes_job_id(nested)
    return ""


def _jobs_json_path() -> Path:
    if DATA_ROOT != Path("/opt/data"):
        return DATA_ROOT / "cron" / "jobs.json"
    if settings.hermes_data_path and str(settings.hermes_data_path) != "/hermes-data":
        return Path(settings.hermes_data_path) / "cron" / "jobs.json"
    data_root_jobs = DATA_ROOT / "cron" / "jobs.json"
    if data_root_jobs.exists():
        return data_root_jobs
    configured = Path(settings.hermes_data_path) / "cron" / "jobs.json" if settings.hermes_data_path else None
    return configured or data_root_jobs


def _jobs_json_entry(hermes_job_id: str) -> dict[str, Any] | None:
    path = _jobs_json_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    if not isinstance(jobs, list):
        return None
    return next((job for job in jobs if isinstance(job, dict) and str(job.get("id")) == hermes_job_id), None)


def _is_generic_lead_research_job(hermes_job_id: str) -> bool:
    job = _jobs_json_entry(hermes_job_id)
    if not job:
        return False
    command = str(job.get("command") or "")
    safety = job.get("safety") if isinstance(job.get("safety"), dict) else {}
    return (
        job.get("source") in {"voryx_template", "voryx_employee_template"}
        and str(job.get("task_type") or "").lower() == "generate leads"
        and GENERIC_LEAD_RESEARCH_SCRIPT in command
        and "--no-email" in command
        and safety.get("email_sending") is False
        and safety.get("prospect_outreach") is False
    )


def _container_path(path: str) -> Path:
    value = str(path or "")
    if DATA_ROOT != Path("/opt/data"):
        root = DATA_ROOT
    else:
        root = Path(settings.hermes_data_path) if settings.hermes_data_path and str(settings.hermes_data_path) != "/hermes-data" else DATA_ROOT
    if not root.exists() and settings.hermes_data_path:
        root = Path(settings.hermes_data_path)
    if value == "/opt/data":
        return root
    if value.startswith("/opt/data/"):
        return root / value.removeprefix("/opt/data/")
    return Path(value)



def _normalized_business(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\b(inc|incorporated|ltd|limited|llc|corp|corporation|company|co|cafe|coffee|restaurant|bar)\b\.?", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalized_domain(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.removeprefix("https://").removeprefix("http://").removeprefix("www.")
    return text.split("/", 1)[0].split(":", 1)[0]


def _normalized_phone(value: str | None) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _business_keys(row: dict[str, str]) -> set[str]:
    keys: set[str] = set()
    email = str(row.get("Public Email") or row.get("email") or row.get("public_email") or "").strip().lower()
    business = _normalized_business(row.get("Business Name") or row.get("business_name") or row.get("business") or row.get("company") or "")
    website = _normalized_domain(row.get("Website") or row.get("website") or row.get("domain") or "")
    phone = _normalized_phone(row.get("Phone") or row.get("phone") or "")
    address = _normalized_business(row.get("Address") or row.get("address") or row.get("location") or "")
    source_id = str(row.get("Source Platform ID") or row.get("source_platform_id") or row.get("osm_id") or row.get("place_id") or "").strip()
    if source_id:
        keys.add(f"source:{source_id}")
    if email and "@" in email:
        keys.add(f"email:{email}")
    if website:
        keys.add(f"domain:{website}")
    if phone:
        keys.add(f"phone:{phone}")
    if business and address:
        keys.add(f"business-address:{business}:{address}")
    elif business and website:
        keys.add(f"business-domain:{business}:{website}")
    elif business and phone:
        keys.add(f"business-phone:{business}:{phone}")
    elif business:
        keys.add(f"business:{business}")
    return keys


def _business_key(row: dict[str, str]) -> str:
    keys = sorted(_business_keys(row))
    return keys[0] if keys else ""


def _existing_bibs_business_keys(exclude: Path | None = None) -> set[str]:
    keys: set[str] = set()
    for path in LEADS_DIR.glob("leads_brew_it*.csv"):
        if exclude is not None and path.resolve() == exclude.resolve():
            continue
        try:
            import csv
            with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                for row in csv.DictReader(handle):
                    row_keys = _business_keys(row)
                    if row_keys:
                        keys.update(row_keys)
        except Exception:
            continue
    return keys


def _csv_business_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    try:
        import csv
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                row_keys = _business_keys(row)
                if row_keys:
                    keys.update(row_keys)
    except Exception:
        return set()
    return keys


def _payload_mentions_send_outreach(payload: Any) -> bool:
    try:
        return "send_outreach.py" in json.dumps(payload, sort_keys=True)
    except TypeError:
        return "send_outreach.py" in str(payload)


def _execute_lead_research(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    source_config = LEADS_DIR / BIBS_LEAD_SOURCE_CONFIG
    if not source_config.exists():
        return _failed(
            "internet_research_provider_not_configured: AI Internet Research is selected, but no web/search provider is connected. Connect a search provider or upload a lead CSV.",
            logs=[
                "INTERNET_RESEARCH_PROVIDER_NOT_CONFIGURED",
                "Blocked legacy generate_leads.py because it repeats the same 28 BIBS leads.",
                "No prospect email sent.",
            ],
            results={"error_code": "internet_research_provider_not_configured", "next_action": "Connect a search provider or upload a lead CSV.", "prospect_emails_sent": 0},
        )
    try:
        config_payload = json.loads(source_config.read_text(encoding="utf-8"))
    except Exception:
        config_payload = {}
    if str(config_payload.get("source_type") or "").strip() in {"ai_internet_research", "real_directory"}:
        return _execute_bibs_native_browser_research(task_type, payload, source_config, config_payload)
    script = _container_path(GENERIC_LEAD_RESEARCH_SCRIPT)
    if not script.exists():
        return _failed(f"Generic Lead Research script not found: {GENERIC_LEAD_RESEARCH_SCRIPT}")
    before_keys = _existing_bibs_business_keys()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args = [
        "python3",
        str(script),
        "--company-id", BIBS_COMPANY_ID,
        "--campaign-id", BIBS_CAMPAIGN_ID,
        "--employee-id", "",
        "--hermes-job-id", LEAD_RESEARCH_JOB_ID,
        "--industry", "Cold Brew Coffee B2B Outreach",
        "--location", "Toronto",
        "--target-customer", "independent cafes, restaurants, boutique grocers and hospitality buyers",
        "--exclude", "franchises, chains, already contacted businesses, duplicate prior BIBS leads",
        "--limit", str(int(payload.get("limit") or 25)),
        "--output-dir", str(LEADS_DIR),
        "--config", str(source_config),
        "--notes", "BIBS real source-backed lead research",
        "--no-email",
    ]
    result = _run(args, cwd=LEADS_DIR)
    logs = _logs_from_completed_process(result)
    if result.returncode != 0:
        return _failed(_first_error_line(logs) or "BIBS source-backed lead research failed", logs=logs, results={"returncode": result.returncode, "prospect_emails_sent": 0})
    output_path = _generic_output_from_stdout(result.stdout) or _latest_lead_output()
    physical_output_path = _container_path(str(output_path)) if output_path else None
    if physical_output_path is None or not physical_output_path.exists():
        return _failed("BIBS source-backed lead research completed without an output CSV", logs=logs)
    new_keys = _csv_business_keys(physical_output_path) - before_keys
    if len(new_keys) < 10:
        return _failed(
            f"no_new_unique_leads: BIBS lead run produced only {len(new_keys)} new unique businesses; refusing to mark repeated leads as success.",
            logs=logs + [f"NEW_UNIQUE_BUSINESSES={len(new_keys)}", "No prospect email sent."],
            results={"error_code": "no_new_unique_leads", "output_path": str(output_path), "new_unique_businesses": len(new_keys), "prospect_emails_sent": 0},
        )
    output_record = _write_cron_output(
        LEAD_RESEARCH_JOB_ID,
        "BIBS Lead Research scheduled execution",
        task_type,
        payload,
        logs,
        {"output_path": str(output_path), "new_unique_businesses": len(new_keys), "sent_count": 0, "prospect_emails_sent": 0},
    )
    return {
        "status": "ok",
        "logs": logs + [f"NEW_UNIQUE_BUSINESSES={len(new_keys)}", f"HERMES_OUTPUT_WRITTEN path={output_record}"],
        "results": {
            "hermes_job_id": LEAD_RESEARCH_JOB_ID,
            "output_path": str(output_path),
            "hermes_output_path": str(output_record),
            "new_unique_businesses": len(new_keys),
            "sent_count": 0,
            "prospect_emails_sent": 0,
        },
    }


def _execute_bibs_native_browser_research(task_type: str, payload: dict[str, Any], source_config: Path, config_payload: dict[str, Any]) -> dict[str, Any]:
    script = _container_path(HERMES_NATIVE_BROWSER_SCRIPT)
    if not script.exists():
        return _failed(f"Hermes Native Browser provider script not found: {HERMES_NATIVE_BROWSER_SCRIPT}")
    args = [
        "python3",
        str(script),
        "--company-id", BIBS_COMPANY_ID,
        "--campaign-id", BIBS_CAMPAIGN_ID,
        "--industry", str(config_payload.get("product_service") or "Ethiopian coffee concentrate / cold brew concentrate"),
        "--product-service", str(config_payload.get("product_service") or "Ethiopian coffee concentrate / cold brew concentrate"),
        "--target-customer", str(config_payload.get("target_customer") or "independent cafés, specialty coffee shops, restaurants, boutique grocers"),
        "--geography", str(config_payload.get("target_geography") or "Toronto/GTA"),
        "--exclusions", str(config_payload.get("exclusions") or "franchises, chains, already contacted businesses"),
        "--limit", str(int(payload.get("limit") or config_payload.get("lead_limit") or 25)),
        "--min-success", "1",
        "--target-type", "email_ready",
        "--max-runtime-seconds", str(int(config_payload.get("max_runtime_seconds") or 900)),
        "--max-pages", str(int(config_payload.get("max_pages") or 150)),
        "--max-consecutive-no-new-queries", str(int(config_payload.get("max_consecutive_no_new_queries") or 3)),
        "--output-dir", str(LEADS_DIR),
        "--config", str(source_config),
        "--no-email",
    ]
    result = _run(args, cwd=LEADS_DIR)
    logs = _logs_from_completed_process(result)
    output_path = _native_browser_output_from_stdout(result.stdout)
    metadata = _native_browser_metadata_from_output(output_path)
    if result.returncode != 0:
        error_text = _first_error_line(logs) or "Hermes Native Browser research failed"
        results = {"returncode": result.returncode, "prospect_emails_sent": 0, **metadata}
        if "no_new_unique_leads" in error_text:
            results["error_code"] = "no_new_unique_leads"
        elif "hermes_native_browser_unavailable" in error_text:
            results["error_code"] = "hermes_native_browser_unavailable"
        else:
            results["error_code"] = "browser_research_failed"
        return _failed(error_text, logs=logs, results=results)
    physical_output_path = _container_path(str(output_path)) if output_path else None
    if physical_output_path is None or not physical_output_path.exists():
        return _failed("Hermes Native Browser research completed without an output CSV", logs=logs)
    new_keys = _csv_business_keys(physical_output_path)
    new_unique_businesses = int(metadata.get("new_unique_businesses") or _csv_row_count(physical_output_path) or len(new_keys))
    if new_unique_businesses < 1 and not metadata.get("target_achieved"):
        return _failed(
            "no_new_unique_leads: Hermes Native Browser did not find any new unique businesses.",
            logs=logs + [f"NEW_UNIQUE_BUSINESSES={new_unique_businesses}", "No prospect email sent."],
            results={"error_code": "no_new_unique_leads", "output_path": str(output_path), "new_unique_businesses": new_unique_businesses, "prospect_emails_sent": 0, **metadata},
        )
    warning_logs = []
    if not metadata.get("target_achieved"):
        warning_logs.append(f"EMAIL_READY_TARGET_PARTIAL={metadata.get('email_ready', 0)}/{metadata.get('target', '?')}; stop_reason={metadata.get('stop_reason', 'unknown')}")
    output_results = {
        "provider": "hermes_native_browser",
        "output_path": str(output_path),
        "new_unique_businesses": new_unique_businesses,
        "sent_count": 0,
        "prospect_emails_sent": 0,
        "warning": warning_logs[0] if warning_logs else None,
        **metadata,
    }
    output_record = _write_cron_output(
        LEAD_RESEARCH_JOB_ID,
        "BIBS Hermes Native Browser Lead Research",
        task_type,
        payload,
        logs + warning_logs,
        output_results,
    )
    return {
        "status": "ok",
        "logs": logs + warning_logs + [f"NEW_UNIQUE_BUSINESSES={new_unique_businesses}", f"EMAIL_READY={output_results.get('email_ready', 0)}", f"HERMES_OUTPUT_WRITTEN path={output_record}"],
        "results": {
            "provider": "hermes_native_browser",
            "hermes_job_id": LEAD_RESEARCH_JOB_ID,
            "output_path": str(output_path),
            "hermes_output_path": str(output_record),
            "new_unique_businesses": new_unique_businesses,
            "warning": warning_logs[0] if warning_logs else None,
            "sent_count": 0,
            "prospect_emails_sent": 0,
            "email_sending": False,
            **metadata,
        },
    }

def _execute_generic_lead_research(hermes_job_id: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    job = _jobs_json_entry(hermes_job_id)
    if not job:
        return _failed(f"Hermes job not found in jobs.json: {hermes_job_id}")
    command = str(job.get("command") or "")
    if "send_outreach.py" in command or "--no-email" not in command or GENERIC_LEAD_RESEARCH_SCRIPT not in command:
        return _failed("Blocked unsafe generic lead research command")
    try:
        args = shlex.split(command)
    except ValueError as exc:
        return _failed(f"Could not parse generic lead research command: {exc}")
    if payload.get("sample"):
        args = _replace_arg(args, "--limit", str(min(max(int(payload.get("limit") or 5), 1), 5)))
    if len(args) < 2 or args[0] != "python3" or args[1] != GENERIC_LEAD_RESEARCH_SCRIPT:
        return _failed("Generic lead research command must call the approved script with python3")
    script = _container_path(GENERIC_LEAD_RESEARCH_SCRIPT)
    if not script.exists():
        return _failed(f"Generic Lead Research script not found: {GENERIC_LEAD_RESEARCH_SCRIPT}")
    output_dir = _arg_value(args, "--output-dir")
    limit = int(_arg_value(args, "--limit") or "0")
    if not output_dir or not output_dir.startswith("/opt/data/home/voryx_workspaces/"):
        return _failed("Generic lead research output directory must be under /opt/data/home/voryx_workspaces")
    before = _latest_generic_lead_output(_container_path(output_dir))
    run_args = _physicalize_args(args)
    result = _run(run_args, cwd=_container_path(str(job.get("working_directory") or output_dir)))
    logs = _logs_from_completed_process(result)
    if result.returncode != 0:
        error_text = _first_error_line(logs) or "Generic Lead Research execution failed"
        return _failed(
            error_text,
            logs=logs,
            results={"returncode": result.returncode, "error": error_text},
        )
    output_path = _generic_output_from_stdout(result.stdout) or _latest_generic_lead_output(_container_path(output_dir))
    physical_output_path = _container_path(str(output_path)) if output_path else None
    if physical_output_path is None or (before is not None and physical_output_path == before and not _file_touched_now(physical_output_path)):
        return _failed("Generic Lead Research completed without a current output file", logs=logs)
    lead_count = _csv_row_count(physical_output_path)
    if limit and lead_count > limit:
        return _failed(f"Generic Lead Research exceeded limit: {lead_count}>{limit}", logs=logs, results={"output_path": str(output_path)})
    output_record = _write_cron_output(
        hermes_job_id,
        "Generic Lead Research scheduled execution",
        task_type,
        payload,
        logs,
        {
            "output_path": str(output_path),
            "lead_count": lead_count,
            "sent_count": 0,
            "command": command,
        },
    )
    return {
        "status": "ok",
        "logs": logs + [f"HERMES_OUTPUT_WRITTEN path={output_record}"],
        "results": {
            "hermes_job_id": hermes_job_id,
            "output_path": str(output_path),
            "hermes_output_path": str(output_record),
            "lead_count": lead_count,
            "sent_count": 0,
            "prospect_emails_sent": 0,
            "email_sending": False,
        },
    }


def _execute_daily_report(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        recipient = validate_report_recipient(payload.get("recipient") or INTERNAL_REPORT_RECIPIENT, report_only_acceptance=True)
    except ValueError as exc:
        return _failed(str(exc))
    script = LEADS_DIR / "generate_daily_report.py"
    if not script.exists():
        return _failed(f"Daily Report script not found: {script}")

    report_date = _report_date(payload)
    report_path = LEADS_DIR / "brew_daily_report.txt"
    result = _run(["python3", str(script), "--date", report_date, "--output", str(report_path)], cwd=LEADS_DIR)
    if result.returncode != 0:
        return _failed(
            "Daily Report generation failed",
            logs=_logs_from_completed_process(result),
            results={"returncode": result.returncode, "recipient": recipient},
        )
    if not report_path.exists():
        return _failed("Daily Report generation did not create the expected report file", logs=_logs_from_completed_process(result))

    subject = payload.get("subject") or f"Brew It By Sash Daily Outreach Report - {report_date}"
    request_id = f"voryx-scheduled-report-{report_date}-{uuid.uuid4().hex[:10]}"
    request_path = _write_mail_request(request_id, recipient, str(subject), report_path, report_date)
    mail_result = _process_one_internal_mail()
    logs = _logs_from_completed_process(result) + [f"MAIL_REQUEST_WRITTEN path={request_path}"] + _logs_from_completed_process(mail_result)
    if mail_result.returncode != 0:
        return _failed(
            "Daily Report email delivery failed",
            logs=logs,
            results={"request_path": str(request_path), "recipient": recipient, "returncode": mail_result.returncode},
        )

    receipt = _read_receipt(request_id)
    provider_message_id = str(receipt.get("provider_message_id") or receipt.get("message_id") or "").strip()
    sent_at = str(receipt.get("sent_at") or "").strip()
    receipt_recipient = str(receipt.get("recipient") or "").strip().lower()
    if not provider_message_id or receipt_recipient != recipient:
        return _failed(
            "Daily Report delivery receipt is missing provider message evidence",
            logs=logs,
            results={"request_path": str(request_path), "receipt": _safe_receipt(receipt), "recipient": recipient},
        )

    output_record = _write_cron_output(
        DAILY_REPORT_JOB_ID,
        "BIBS Daily Report scheduled execution",
        task_type,
        payload,
        logs,
        {
            "artifact_path": str(report_path),
            "recipient": recipient,
            "provider_message_id": provider_message_id,
            "sent_at": sent_at,
            "receipt_path": str(_receipt_path(request_id)),
        },
    )
    return {
        "status": "ok",
        "logs": logs + [f"HERMES_OUTPUT_WRITTEN path={output_record}"],
        "results": {
            "hermes_job_id": DAILY_REPORT_JOB_ID,
            "artifact_path": str(report_path),
            "report_path": str(report_path),
            "hermes_output_path": str(output_record),
            "recipient": recipient,
            "provider_message_id": provider_message_id,
            "sent_at": sent_at,
            "status": "sent",
            "delivery_status": "sent",
            "prospect_emails_sent": 0,
        },
    }


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    execution_root = _container_path("/opt/data")
    env["HOME"] = str(execution_root / "home")
    env["PATH"] = f"{execution_root / 'home' / '.cargo' / 'bin'}:{env.get('PATH', '')}"
    env.setdefault("HIMALAYA_BIN", str(execution_root / "home" / ".cargo" / "bin" / "himalaya"))
    env["HERMES_DATA_ROOT"] = str(execution_root)
    env["HERMES_CONTAINER_DATA_ROOT"] = str(execution_root)
    return subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )


def _process_one_internal_mail() -> subprocess.CompletedProcess:
    processor = MAIL_QUEUE_DIR / "process_internal_mail_queue.py"
    if not processor.exists():
        return subprocess.CompletedProcess(
            args=["python3", str(processor)],
            returncode=127,
            stdout="",
            stderr=f"Internal mail processor not found: {processor}",
        )
    env = os.environ.copy()
    env["HOME"] = str(DATA_ROOT / "home")
    env["PATH"] = f"{DATA_ROOT / 'home' / '.cargo' / 'bin'}:{env.get('PATH', '')}"
    env["HIMALAYA_BIN"] = str(DATA_ROOT / "home" / ".cargo" / "bin" / "himalaya")
    env["HERMES_DATA_ROOT"] = str(DATA_ROOT)
    env["HERMES_CONTAINER_DATA_ROOT"] = str(DATA_ROOT)
    env["VORYX_PROCESS_ONE_MAIL"] = "1"
    return subprocess.run(
        ["python3", str(processor)],
        cwd=str(MAIL_QUEUE_DIR),
        env=env,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )


def _write_mail_request(request_id: str, recipient: str, subject: str, artifact_path: Path, report_date: str) -> Path:
    pending = MAIL_QUEUE_DIR / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    request_path = pending / f"{request_id}.json"
    tmp_path = pending / f".{request_id}.tmp"
    request = {
        "request_id": request_id,
        "kind": "daily_report",
        "recipient": recipient,
        "subject": subject,
        "artifact_path": str(artifact_path),
        "report_date": report_date,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "report_only_acceptance": True,
        "source": "voryx_jobs_json_scheduler",
    }
    tmp_path.write_text(json.dumps(request, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(request_path)
    return request_path


def _read_receipt(request_id: str) -> dict[str, Any]:
    path = _receipt_path(request_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _receipt_path(request_id: str) -> Path:
    return MAIL_QUEUE_DIR / "receipts" / f"{request_id}.json"


def _safe_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in receipt.items()
        if key in {"request_id", "kind", "recipient", "sent_at", "provider_message_id", "message_id", "status", "error"}
    }


def _report_date(payload: dict[str, Any]) -> str:
    value = str(payload.get("report_date") or payload.get("date") or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return datetime.now().astimezone().date().isoformat()


def _lead_output_from_stdout(stdout: str) -> Path | None:
    match = re.search(r"Generated\s+\d+\s+unique leads in\s+(.+)", stdout or "")
    if not match:
        return None
    return Path(match.group(1).strip())


def _generic_output_from_stdout(stdout: str) -> Path | None:
    match = re.search(r"GENERIC_LEAD_RESEARCH_OUTPUT\s+path=(.+)", stdout or "")
    if not match:
        return None
    return Path(match.group(1).strip())


def _native_browser_output_from_stdout(stdout: str) -> Path | None:
    match = re.search(r"HERMES_NATIVE_BROWSER_OUTPUT\s+path=(.+)", stdout or "")
    if not match:
        return None
    return Path(match.group(1).strip())


def _native_browser_metadata_from_output(output_path: Path | None) -> dict[str, Any]:
    if not output_path:
        return {}
    physical_output_path = _container_path(str(output_path))
    metadata_path = physical_output_path.with_suffix(".metadata.json")
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _arg_value(args: list[str], name: str) -> str:
    try:
        index = args.index(name)
    except ValueError:
        return ""
    if index + 1 >= len(args):
        return ""
    return args[index + 1]


def _replace_arg(args: list[str], name: str, value: str) -> list[str]:
    next_args = list(args)
    try:
        index = next_args.index(name)
    except ValueError:
        return next_args + [name, value]
    if index + 1 < len(next_args):
        next_args[index + 1] = value
    else:
        next_args.append(value)
    return next_args


def _physicalize_args(args: list[str]) -> list[str]:
    next_args = list(args)
    for index, value in enumerate(next_args):
        if value.startswith("/opt/data/"):
            next_args[index] = str(_container_path(value))
    return next_args


def _latest_generic_lead_output(output_dir: Path) -> Path | None:
    if not output_dir.exists():
        return None
    candidates = sorted(output_dir.glob("leads_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _csv_row_count(path: Path) -> int:
    import csv

    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def _latest_lead_output() -> Path | None:
    candidates = sorted(LEADS_DIR.glob("leads_brew_it_combined_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def _file_touched_now(path: Path) -> bool:
    return datetime.now().timestamp() - path.stat().st_mtime < 300


def _write_cron_output(
    hermes_job_id: str,
    title: str,
    task_type: str,
    payload: dict[str, Any],
    logs: list[str],
    results: dict[str, Any],
) -> Path:
    directory = CRON_OUTPUT_DIR / hermes_job_id
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{timestamp}.md"
    body = [
        f"# {title}",
        "",
        f"- executed_at: {datetime.now(timezone.utc).isoformat()}",
        f"- task_type: {task_type}",
        f"- hermes_job_id: {hermes_job_id}",
        "",
        "## Results",
        "```json",
        json.dumps(results, indent=2, sort_keys=True),
        "```",
        "",
        "## Logs",
        "```text",
        "\n".join(logs)[-12000:],
        "```",
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _record_jobs_json_execution(hermes_job_id: str, result: dict[str, Any]) -> None:
    path = _jobs_json_path()
    if not path.exists():
        return
    lock_path = path.with_suffix(".lock")
    try:
        import fcntl

        with lock_path.open("w", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            raw = json.loads(path.read_text(encoding="utf-8"))
            jobs = raw.get("jobs") if isinstance(raw, dict) else raw
            if not isinstance(jobs, list):
                return
            job = next((item for item in jobs if isinstance(item, dict) and str(item.get("id")) == hermes_job_id), None)
            if not job:
                return
            now = datetime.now(timezone.utc).isoformat()
            status = str(result.get("status") or "").lower()
            results = result.get("results") if isinstance(result.get("results"), dict) else {}
            job["last_run_at"] = now
            job["last_status"] = "ok" if status == "ok" else ("blocked" if status == "blocked" else "failed")
            job["last_error"] = None if status == "ok" else str(result.get("error") or "scheduled execution failed")
            if status == "ok":
                job["last_output_path"] = results.get("hermes_output_path") or results.get("output_path") or results.get("report_path")
            if job.get("enabled"):
                job["state"] = "scheduled"
            if isinstance(raw, dict):
                raw["jobs"] = jobs
                raw["updated_at"] = now
                output = raw
            else:
                output = jobs
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
            tmp_path.replace(path)
    except Exception:
        return


def _first_error_line(logs: list[str]) -> str:
    for line in logs:
        value = str(line or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if "error" in lowered or "failed" in lowered or "not configured" in lowered:
            return value[-1000:]
    return ""


def _logs_from_completed_process(result: subprocess.CompletedProcess) -> list[str]:
    logs: list[str] = []
    if result.stdout:
        logs.extend(line for line in result.stdout.splitlines() if line.strip())
    if result.stderr:
        logs.extend(f"stderr: {line}" for line in result.stderr.splitlines() if line.strip())
    logs.append(f"returncode={result.returncode}")
    return logs


def _unsupported(message: str) -> dict[str, Any]:
    return {"status": "unsupported", "mode": "jobs_json", "logs": [message], "results": {}, "error": message}


def _failed(message: str, *, logs: list[str] | None = None, results: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "failed",
        "mode": "jobs_json",
        "logs": (logs or []) + [message],
        "results": results or {},
        "error": message,
    }
