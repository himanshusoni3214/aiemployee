#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="${VORYX_OPS_ROOT:-/docker/voryx-ops}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

cd "$ROOT_DIR" || exit 1

TS="$(TZ=America/Toronto date +%Y%m%d-%H%M%S)"
QA_AUDIT_REL_DIR="${QA_AUDIT_REL_DIR:-audits/production-qa/$TS}"
QA_AUDIT_DIR="/work/$QA_AUDIT_REL_DIR"
HOST_AUDIT_DIR="$ROOT_DIR/$QA_AUDIT_REL_DIR"
mkdir -p "$HOST_AUDIT_DIR"

{
  echo "timestamp=$TS"
  echo "root=$ROOT_DIR"
  echo "compose_file=$COMPOSE_FILE"
  echo "env_file=$ENV_FILE"
  echo "git_head=$(git rev-parse HEAD 2>/dev/null || true)"
  echo "git_status<<EOF"
  git status --short 2>/dev/null || true
  echo "EOF"
} > "$HOST_AUDIT_DIR/run_context.txt"

"${COMPOSE[@]}" ps > "$HOST_AUDIT_DIR/compose_ps_before.txt" 2>&1 || true
docker ps > "$HOST_AUDIT_DIR/docker_ps_before.txt" 2>&1 || true

set +e
"${COMPOSE[@]}" run --rm \
  -e "QA_AUDIT_DIR=$QA_AUDIT_DIR" \
  qa
STATUS=$?
set -e

"${COMPOSE[@]}" ps > "$HOST_AUDIT_DIR/compose_ps_after.txt" 2>&1 || true
"${COMPOSE[@]}" logs --tail=300 backend frontend worker scheduler > "$HOST_AUDIT_DIR/docker_logs_tail.txt" 2>&1 || true

if [[ "$STATUS" -eq 0 ]]; then
  echo "Production QA PASSED"
else
  echo "Production QA FAILED"
fi
echo "Audit artifacts: $HOST_AUDIT_DIR"
echo "Markdown report: $HOST_AUDIT_DIR/QA_REPORT.md"
echo "HTML report: $HOST_AUDIT_DIR/QA_REPORT.html"
echo "Playwright report: $HOST_AUDIT_DIR/playwright-report/index.html"

exit "$STATUS"
