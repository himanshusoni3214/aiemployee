from typing import Any

OUTREACH_FOLLOWUP_HERMES_JOB_ID = "b03a2d0f1149"
SAFETY_LOCKED_HERMES_JOB_IDS = {OUTREACH_FOLLOWUP_HERMES_JOB_ID}
SAFETY_LOCK_MESSAGE = (
    "Safety blocked: Brew It Outreach Followup can send real Gmail prospect outreach "
    "and is locked until an approved sending policy is enabled."
)
SAFETY_BLOCKED_ACTIONS = {"start", "resume", "restart", "run", "dry-run", "test-run"}


def is_safety_locked_hermes_job_id(hermes_job_id: str | None) -> bool:
    return str(hermes_job_id or "") in SAFETY_LOCKED_HERMES_JOB_IDS


def is_safety_blocked_action(hermes_job_id: str | None, action: str) -> bool:
    return is_safety_locked_hermes_job_id(hermes_job_id) and action.lower() in SAFETY_BLOCKED_ACTIONS


def safety_block_result(action: str, hermes_job_id: str | None, hermes_job_name: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "safety_blocked",
        "state": "safety_blocked",
        "action": action,
        "hermes_job_id": str(hermes_job_id or ""),
        "hermes_job_name": str(hermes_job_name or ""),
        "message": SAFETY_LOCK_MESSAGE,
        "safety_locked": True,
        "reason": SAFETY_LOCK_MESSAGE,
    }
