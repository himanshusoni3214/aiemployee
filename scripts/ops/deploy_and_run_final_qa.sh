#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="${VORYX_OPS_ROOT:-/docker/voryx-ops}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"
HERMES_DATA_DIR="${HERMES_DATA_DIR:-/docker/hermes-agent-yepj/data}"
APPROVED_INTERNAL_RECIPIENT="himanshusoni3214@gmail.com"
BREW_COMPANY_ID="company-brew-it-by-sash"
OUTREACH_DRAFT_ID="47caae0a6a59"
LEAD_RESEARCH_ID="0d0c20e25f55"
OUTREACH_FOLLOWUP_ID="b03a2d0f1149"
END_DAY_REPORT_ID="5881b72113ce"
START_ISO="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

if [[ "$(pwd)" != "$ROOT_DIR" ]]; then
  echo "ERROR: run this script from $ROOT_DIR"
  exit 1
fi

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
TS="$(TZ=America/Toronto date +%Y%m%d-%H%M%S)"
AUDIT_REL_DIR="${QA_AUDIT_REL_DIR:-audits/final-production-qa/$TS}"
AUDIT_DIR="$ROOT_DIR/$AUDIT_REL_DIR"
BACKUP_DIR="$AUDIT_DIR/backups"
mkdir -p "$AUDIT_DIR" "$BACKUP_DIR"
exec > >(tee "$AUDIT_DIR/deploy_and_run_final_qa.log") 2>&1

FINAL_STATUS="failed"
FINAL_REASON="script did not complete"

env_value() {
  local name="$1"
  [[ -f "$ENV_FILE" ]] || return 1
  grep -E "^${name}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

write_final_report() {
  local exit_code="${1:-1}"
  {
    echo "# Final Production QA Report"
    echo
    echo "Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "Started: $START_ISO"
    echo "Audit directory: $AUDIT_DIR"
    echo "Git SHA: $(git rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "Exit code: $exit_code"
    echo "Status: $FINAL_STATUS"
    echo "Reason: $FINAL_REASON"
    echo
    echo "## Required Safety States"
    echo
    echo "- Outreach Draft $OUTREACH_DRAFT_ID: paused"
    echo "- Lead Research $LEAD_RESEARCH_ID: scheduled"
    echo "- Outreach Followup $OUTREACH_FOLLOWUP_ID: paused and safety locked"
    echo "- End Day Report $END_DAY_REPORT_ID: scheduled"
    echo
    echo "## Artifacts"
    echo
    echo "- HERMES_STATE_EVIDENCE.json"
    echo "- REPORT_DELIVERY_EVIDENCE.json"
    echo "- NO_PROSPECT_SEND_EVIDENCE.json"
    echo "- CLEANUP_EVIDENCE.json"
    echo "- CRUD_MATRIX.md"
    echo "- selector/playwright-report/index.html"
    echo "- crud/playwright-report/index.html"
  } > "$AUDIT_DIR/FINAL_QA_REPORT.md"
  python3 - "$AUDIT_DIR/FINAL_QA_REPORT.md" "$AUDIT_DIR/FINAL_QA_REPORT.html" <<'PY'
import html
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
body = "\n".join(f"<p>{html.escape(line)}</p>" if line else "" for line in source.splitlines())
pathlib.Path(sys.argv[2]).write_text(f"<!doctype html><html><body>{body}</body></html>\n", encoding="utf-8")
PY
}

finish() {
  local exit_code=$?
  set +e
  "${COMPOSE[@]}" ps > "$AUDIT_DIR/compose_ps_final.txt" 2>&1
  "${COMPOSE[@]}" logs --tail=500 backend frontend worker scheduler > "$AUDIT_DIR/container_logs_tail.txt" 2>&1
  docker ps > "$AUDIT_DIR/docker_ps_final.txt" 2>&1
  write_final_report "$exit_code"
  echo "Final QA artifacts: $AUDIT_DIR"
  exit "$exit_code"
}
trap finish EXIT

require_env() {
  local name="$1"
  local value="${!name:-$(env_value "$name" || true)}"
  if [[ -z "$value" ]]; then
    echo "ERROR: missing required environment value $name"
    exit 1
  fi
  printf '%s' "$value"
}

APP_HOST_VALUE="${APP_HOST:-$(env_value APP_HOST || true)}"
QA_BASE_URL_VALUE="${QA_BASE_URL:-$(env_value QA_BASE_URL || true)}"
BASE_URL="${QA_BASE_URL_VALUE:-https://${APP_HOST_VALUE:-ops.themealz.com}}"
BASE_URL="${BASE_URL%/}"
QA_EMAIL="$(require_env VORYX_QA_ADMIN_EMAIL)"
QA_PASSWORD="$(require_env VORYX_QA_ADMIN_PASSWORD)"

json_get() {
  local file="$1"
  local expr="$2"
  python3 - "$file" "$expr" <<'PY'
import json
import sys

data = json.loads(open(sys.argv[1], encoding="utf-8").read())
value = data
for part in sys.argv[2].split("."):
    if not part:
        continue
    value = value.get(part) if isinstance(value, dict) else None
print("" if value is None else value)
PY
}

verify_hermes_states() {
  local phase="$1"
  local out_file="$AUDIT_DIR/HERMES_STATE_EVIDENCE.${phase}.json"
  python3 - "$HERMES_DATA_DIR/cron/jobs.json" "$out_file" "$phase" <<'PY'
import json
import pathlib
import sys
from datetime import datetime, timezone

jobs_path = pathlib.Path(sys.argv[1])
out_path = pathlib.Path(sys.argv[2])
phase = sys.argv[3]
raw = json.loads(jobs_path.read_text(encoding="utf-8"))
jobs = raw if isinstance(raw, list) else raw.get("jobs", raw.get("items", []))
by_id = {str(job.get("id")): job for job in jobs if isinstance(job, dict)}
expected = {
    "47caae0a6a59": {"enabled": False, "state": "paused", "next_run_at": None},
    "0d0c20e25f55": {"enabled": True, "state": "scheduled"},
    "b03a2d0f1149": {"enabled": False, "state": "paused", "next_run_at": None},
    "5881b72113ce": {"enabled": True, "state": "scheduled"},
}
evidence = {"phase": phase, "checked_at": datetime.now(timezone.utc).isoformat(), "jobs_path": str(jobs_path), "states": {}, "ok": True, "errors": []}
for job_id, required in expected.items():
    job = by_id.get(job_id)
    if not job:
        evidence["ok"] = False
        evidence["errors"].append(f"missing job {job_id}")
        continue
    state = {key: job.get(key) for key in ("name", "enabled", "state", "next_run_at", "last_status", "last_error", "paused_at", "paused_reason")}
    evidence["states"][job_id] = state
    for key, expected_value in required.items():
        if job.get(key) != expected_value:
            evidence["ok"] = False
            evidence["errors"].append(f"{job_id} expected {key}={expected_value!r}, got {job.get(key)!r}")
followup = by_id.get("b03a2d0f1149") or {}
if followup.get("enabled") is not False:
    evidence["ok"] = False
    evidence["errors"].append("Outreach Followup is enabled; aborting to protect prospect outreach")
out_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
if not evidence["ok"]:
    raise SystemExit("; ".join(evidence["errors"]))
PY
  cp "$out_file" "$AUDIT_DIR/HERMES_STATE_EVIDENCE.json"
}

wait_for_backend() {
  local health_url="$BASE_URL/health"
  echo "Waiting for backend at $health_url"
  for attempt in $(seq 1 90); do
    if curl -fsS --max-time 5 "$health_url" > "$AUDIT_DIR/backend_health_attempt_${attempt}.json" 2>"$AUDIT_DIR/backend_health_attempt_${attempt}.err"; then
      echo "Backend ready on attempt $attempt"
      return 0
    fi
    sleep 2
  done
  echo "ERROR: backend did not become ready"
  return 1
}

api_login() {
  local response_file="$AUDIT_DIR/api_login.json"
  curl -fsS -X POST "$BASE_URL/api/auth/login" \
    -H 'Content-Type: application/json' \
    --data "$(python3 - <<PY
import json
print(json.dumps({"email": "$QA_EMAIL", "password": "$QA_PASSWORD"}))
PY
)" > "$response_file"
  json_get "$response_file" "access_token"
}

api_call() {
  local token="$1"
  local method="$2"
  local path="$3"
  local body="${4:-}"
  local output="$5"
  if [[ -n "$body" ]]; then
    curl -fsS -X "$method" "$BASE_URL$path" -H "Authorization: Bearer $token" -H 'Content-Type: application/json' --data "$body" > "$output"
  else
    curl -fsS -X "$method" "$BASE_URL$path" -H "Authorization: Bearer $token" > "$output"
  fi
}

backup_environment() {
  git rev-parse HEAD > "$AUDIT_DIR/git_sha_before.txt"
  git status --short > "$AUDIT_DIR/git_status_before.txt"
  "${COMPOSE[@]}" ps > "$AUDIT_DIR/compose_ps_before.txt"
  docker ps > "$AUDIT_DIR/docker_ps_before.txt"
  "${COMPOSE[@]}" exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$BACKUP_DIR/postgres.sql"
  cp -a "$HERMES_DATA_DIR/cron/jobs.json" "$BACKUP_DIR/jobs.json"
  tar -czf "$BACKUP_DIR/hermes_scripts.tgz" -C "$HERMES_DATA_DIR" home cron/jobs.json 2>"$BACKUP_DIR/hermes_scripts_tar.err" || true
}

install_internal_processor() {
  "${COMPOSE[@]}" exec -T backend python - <<'PY' > "$AUDIT_DIR/internal_processor_install.json"
import json
from app.services.hermes_control import HermesControlService
from app.services.internal_mail_queue import install_processor_script

processor_path = install_processor_script()
control = HermesControlService().ensure_internal_mail_processor_job()
print(json.dumps({"processor_path": str(processor_path), "control": control}, indent=2, default=str))
PY
}

run_internal_report_acceptance() {
  local token="$1"
  local request_file="$AUDIT_DIR/report_delivery_request.json"
  local response_file="$AUDIT_DIR/report_delivery_response.json"
  python3 - <<PY > "$request_file"
import json
print(json.dumps({
    "company_id": "$BREW_COMPANY_ID",
    "recipient": "$APPROVED_INTERNAL_RECIPIENT",
    "send_email": True,
    "report_only_acceptance": True,
}))
PY
  api_call "$token" POST "/api/reports/daily" "$(cat "$request_file")" "$response_file"
  local job_id
  job_id="$(json_get "$response_file" "delivery.job_id")"
  if [[ -z "$job_id" ]]; then
    echo "ERROR: daily report response did not include delivery.job_id"
    return 1
  fi
  echo "$job_id" > "$AUDIT_DIR/report_delivery_job_id.txt"

  for attempt in $(seq 1 40); do
    api_call "$token" GET "/api/system/health?company_id=$BREW_COMPANY_ID" "" "$AUDIT_DIR/report_delivery_health_${attempt}.json" || true
    api_call "$token" GET "/api/jobs/$job_id" "" "$AUDIT_DIR/report_delivery_job_${attempt}.json" || true
    if python3 - "$AUDIT_DIR/report_delivery_job_${attempt}.json" "$AUDIT_DIR/REPORT_DELIVERY_EVIDENCE.json" "$APPROVED_INTERNAL_RECIPIENT" <<'PY'
import json
import sys
from datetime import datetime, timezone

job = json.loads(open(sys.argv[1], encoding="utf-8").read())
recipient = sys.argv[3]
evidence = {
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "job_id": job.get("id"),
    "status": job.get("status"),
    "delivery_status": job.get("delivery_status"),
    "recipient_email": job.get("recipient_email"),
    "provider_message_id": job.get("provider_message_id"),
    "sent_at": job.get("sent_at"),
    "evidence_type": job.get("evidence_type"),
    "verification_reason": job.get("verification_reason"),
    "ok": False,
}
ok = (
    job.get("status") == "Completed"
    and job.get("delivery_status") == "sent"
    and str(job.get("recipient_email") or "").lower() == recipient.lower()
    and bool(str(job.get("provider_message_id") or "").strip())
    and bool(str(job.get("sent_at") or "").strip())
)
evidence["ok"] = ok
open(sys.argv[2], "w", encoding="utf-8").write(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
raise SystemExit(0 if ok else 1)
PY
    then
      echo "Internal report delivery completed with receipt evidence"
      return 0
    fi
    if grep -q '"status"[[:space:]]*:[[:space:]]*"Failed"' "$AUDIT_DIR/report_delivery_job_${attempt}.json"; then
      echo "ERROR: internal report delivery job failed"
      return 1
    fi
    sleep 15
  done
  echo "ERROR: timed out waiting for internal report receipt"
  return 1
}

verify_himalaya_sent() {
  local container
  container="${HERMES_CONTAINER:-$(docker ps --format '{{.Names}}' | grep -E 'hermes|Hermes|agent' | head -n 1 || true)}"
  if [[ -z "$container" ]]; then
    echo "ERROR: could not find running Hermes container for Himalaya Sent-folder verification"
    return 1
  fi
  echo "$container" > "$AUDIT_DIR/hermes_container.txt"
  docker exec "$container" sh -lc 'himalaya envelope list --folder "[Gmail]/Sent Mail" 2>&1 | head -n 120' > "$AUDIT_DIR/himalaya_sent_folder.txt"
  if ! grep -Eiq 'Brew It by Sash Outreach Report|himanshusoni3214@gmail.com' "$AUDIT_DIR/himalaya_sent_folder.txt"; then
    echo "ERROR: Himalaya Sent-folder output did not show the internal report evidence"
    return 1
  fi
}

verify_no_prospect_send() {
  "${COMPOSE[@]}" exec -T backend env "START_ISO=$START_ISO" "APPROVED_INTERNAL_RECIPIENT=$APPROVED_INTERNAL_RECIPIENT" python - <<'PY' > "$AUDIT_DIR/NO_PROSPECT_SEND_EVIDENCE.json"
import json
import os
from datetime import datetime
from sqlalchemy import func, or_, select
from app.core.db import SessionLocal
from app.models.entities import Job, OutreachEvent

start = datetime.fromisoformat(os.environ["START_ISO"].replace("Z", "+00:00")).replace(tzinfo=None)
approved = os.environ["APPROVED_INTERNAL_RECIPIENT"].lower()
sent_statuses = {"sent", "success", "successful", "delivered", "ok", "completed"}
db = SessionLocal()
try:
    prospect_jobs = db.scalar(
        select(func.count(Job.id)).where(
            Job.created_at >= start,
            Job.recipient_email.is_not(None),
            func.lower(Job.recipient_email) != approved,
            or_(Job.delivery_status == "sent", Job.sent_at.is_not(None), Job.provider_message_id.is_not(None)),
        )
    ) or 0
    prospect_events = db.scalar(
        select(func.count(OutreachEvent.event_id)).where(
            OutreachEvent.created_at >= start,
            OutreachEvent.recipient.is_not(None),
            func.lower(OutreachEvent.recipient) != approved,
            OutreachEvent.dry_run == False,  # noqa: E712
            func.lower(OutreachEvent.status).in_(sent_statuses),
        )
    ) or 0
    evidence = {
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "started_at": os.environ["START_ISO"],
        "approved_internal_recipient": approved,
        "prospect_delivery_jobs_since_start": prospect_jobs,
        "prospect_outreach_events_since_start": prospect_events,
        "ok": prospect_jobs == 0 and prospect_events == 0,
    }
    print(json.dumps(evidence, indent=2, sort_keys=True))
    raise SystemExit(0 if evidence["ok"] else 1)
finally:
    db.close()
PY
}

verify_brew_counts() {
  "${COMPOSE[@]}" exec -T backend python - <<'PY' > "$AUDIT_DIR/brew_counts_final.json"
import json
from sqlalchemy import func, select
from app.core.db import SessionLocal
from app.models.entities import AIEmployee, Campaign, EmployeeStatus, Schedule, Status

company_id = "company-brew-it-by-sash"
db = SessionLocal()
try:
    campaigns = db.scalar(select(func.count(Campaign.id)).where(Campaign.company_id == company_id, Campaign.status != Status.archived)) or 0
    employees = db.scalar(select(func.count(AIEmployee.id)).where(AIEmployee.company_id == company_id, AIEmployee.status != EmployeeStatus.archived)) or 0
    schedules = db.scalar(
        select(func.count(Schedule.id))
        .join(AIEmployee, AIEmployee.id == Schedule.employee_id)
        .where(AIEmployee.company_id == company_id, AIEmployee.status != EmployeeStatus.archived)
    ) or 0
    evidence = {"campaigns": campaigns, "employees": employees, "schedules": schedules, "ok": campaigns == 3 and employees == 4 and schedules == 4}
    print(json.dumps(evidence, indent=2, sort_keys=True))
    raise SystemExit(0 if evidence["ok"] else 1)
finally:
    db.close()
PY
}

echo "Final QA audit directory: $AUDIT_DIR"
backup_environment
verify_hermes_states "pre_deploy"

"${COMPOSE[@]}" build backend worker scheduler frontend
"${COMPOSE[@]}" run --rm backend alembic upgrade head
"${COMPOSE[@]}" up -d backend worker scheduler frontend
wait_for_backend
install_internal_processor
verify_hermes_states "post_processor_install"

TOKEN="$(api_login)"
run_internal_report_acceptance "$TOKEN"
verify_himalaya_sent

QA_AUDIT_REL_DIR="$AUDIT_REL_DIR/selector" scripts/ops/run_production_qa.sh
QA_AUDIT_REL_DIR="$AUDIT_REL_DIR/crud" CRUD_QA_PREFIX="QA-E2E-$TS" scripts/ops/run_production_crud_qa.sh

cp -f "$AUDIT_DIR/crud/CRUD_MATRIX.md" "$AUDIT_DIR/CRUD_MATRIX.md"
cp -f "$AUDIT_DIR/crud/CLEANUP_EVIDENCE.json" "$AUDIT_DIR/CLEANUP_EVIDENCE.json"
verify_brew_counts
verify_no_prospect_send
verify_hermes_states "final"

FINAL_STATUS="passed"
FINAL_REASON="all deployment, safety, report delivery, selector QA, CRUD QA, cleanup, and final state checks passed"
echo "Final production QA PASSED"
