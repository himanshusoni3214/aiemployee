#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="${VORYX_OPS_ROOT:-/docker/voryx-ops}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

cd "$ROOT_DIR" || exit 1

TS="$(TZ=America/Toronto date +%Y%m%d-%H%M%S)"
QA_AUDIT_REL_DIR="${QA_AUDIT_REL_DIR:-audits/final-production-qa/$TS}"
QA_AUDIT_DIR="/work/$QA_AUDIT_REL_DIR"
HOST_AUDIT_DIR="$ROOT_DIR/$QA_AUDIT_REL_DIR"
CRUD_QA_PREFIX="${CRUD_QA_PREFIX:-QA-E2E-$TS}"
mkdir -p "$HOST_AUDIT_DIR"

{
  echo "timestamp=$TS"
  echo "root=$ROOT_DIR"
  echo "compose_file=$COMPOSE_FILE"
  echo "env_file=$ENV_FILE"
  echo "crud_qa_prefix=$CRUD_QA_PREFIX"
  echo "git_head=$(git rev-parse HEAD 2>/dev/null || true)"
  echo "git_status<<EOF"
  git status --short 2>/dev/null || true
  echo "EOF"
} > "$HOST_AUDIT_DIR/run_context.txt"

"${COMPOSE[@]}" ps > "$HOST_AUDIT_DIR/compose_ps_before.txt" 2>&1 || true
docker ps > "$HOST_AUDIT_DIR/docker_ps_before.txt" 2>&1 || true

env_value() {
  local name="$1"
  [[ -f "$ENV_FILE" ]] || return 1
  grep -E "^${name}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

APP_HOST_VALUE="${APP_HOST:-$(env_value APP_HOST || true)}"
QA_BASE_URL_VALUE="${QA_BASE_URL:-$(env_value QA_BASE_URL || true)}"
BASE_URL="${QA_BASE_URL_VALUE:-https://${APP_HOST_VALUE:-ops.themealz.com}}"
HEALTH_URL="${BASE_URL%/}/health"
echo "Waiting for backend readiness at $HEALTH_URL" | tee "$HOST_AUDIT_DIR/backend_readiness.txt"
READY=0
for attempt in $(seq 1 60); do
  if curl -fsS --max-time 3 "$HEALTH_URL" >> "$HOST_AUDIT_DIR/backend_readiness.txt" 2>&1; then
    READY=1
    echo "Backend ready on attempt $attempt" | tee -a "$HOST_AUDIT_DIR/backend_readiness.txt"
    break
  fi
  echo "Attempt $attempt failed; retrying in 2s" >> "$HOST_AUDIT_DIR/backend_readiness.txt"
  sleep 2
done

if [[ "$READY" -ne 1 ]]; then
  echo "Production CRUD QA FAILED: backend did not become ready at $HEALTH_URL"
  echo "Audit artifacts: $HOST_AUDIT_DIR"
  exit 1
fi

set +e
"${COMPOSE[@]}" run --rm \
  -e "QA_AUDIT_DIR=$QA_AUDIT_DIR" \
  -e "CRUD_QA_PREFIX=$CRUD_QA_PREFIX" \
  qa npx playwright test --config=playwright.config.ts specs/production-crud-flow.spec.ts
STATUS=$?
set -e

"${COMPOSE[@]}" ps > "$HOST_AUDIT_DIR/compose_ps_after.txt" 2>&1 || true
"${COMPOSE[@]}" logs --tail=300 backend frontend worker scheduler > "$HOST_AUDIT_DIR/docker_logs_tail.txt" 2>&1 || true

if [[ "$STATUS" -eq 0 ]]; then
  echo "Production CRUD QA PASSED"
else
  echo "Production CRUD QA FAILED"
fi
echo "Audit artifacts: $HOST_AUDIT_DIR"
echo "CRUD matrix: $HOST_AUDIT_DIR/CRUD_MATRIX.md"
echo "Playwright report: $HOST_AUDIT_DIR/playwright-report/index.html"

exit "$STATUS"
