#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uv_env_args=()
if [[ -f "$repo_dir/.env" ]]; then
  uv_env_args=(--env-file "$repo_dir/.env")
fi
cd "$repo_dir/apps/api"
# init_db creates the embedded SQLite file automatically; PostgreSQL is not used.
# APP_DB_PATH in .env may select a different SQLite file for browser development.
uv run "${uv_env_args[@]}" python -c "from trade_helper.db import init_db; init_db()"
uv run "${uv_env_args[@]}" uvicorn trade_helper.api:app --host 127.0.0.1 --port 8000 &
api_pid=$!
uv run "${uv_env_args[@]}" python -m trade_helper.worker &
worker_pid=$!
uv run "${uv_env_args[@]}" python -m trade_helper.shadow_v4 &
shadow_pid=$!
uv run "${uv_env_args[@]}" python -m trade_helper.event_sync &
event_pid=$!
uv run "${uv_env_args[@]}" python -m trade_helper.macro_actual_sync &
macro_pid=$!
uv run "${uv_env_args[@]}" python -m trade_helper.news_sync &
news_pid=$!
uv run "${uv_env_args[@]}" python -m trade_helper.sec_news_sync &
sec_news_pid=$!
uv run "${uv_env_args[@]}" python -m trade_helper.news_classification_worker &
classifier_pid=$!
cd "$repo_dir/apps/web"
pnpm dev --host 127.0.0.1 &
web_pid=$!

cleanup() {
  kill "$api_pid" "$worker_pid" "$shadow_pid" "$event_pid" "$macro_pid" "$news_pid" "$sec_news_pid" "$classifier_pid" "$web_pid" 2>/dev/null || true
  wait "$api_pid" "$worker_pid" "$shadow_pid" "$event_pid" "$macro_pid" "$news_pid" "$sec_news_pid" "$classifier_pid" "$web_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
wait "$api_pid" "$worker_pid" "$shadow_pid" "$event_pid" "$macro_pid" "$news_pid" "$sec_news_pid" "$classifier_pid" "$web_pid"
