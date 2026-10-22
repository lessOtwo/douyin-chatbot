#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:-}"
umask 077
touch .env
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"
  [[ "$line" =~ ^([A-Z0-9_]+)=(.*)$ ]] || continue
  key="${BASH_REMATCH[1]}"; value="${BASH_REMATCH[2]}"
  grep -qE "^${key}=" .env && continue
  if [[ "$value" == GENERATE ]]; then
    command -v openssl >/dev/null || { echo 'Install openssl to generate new secrets.' >&2; exit 1; }
    size=24
    [[ "$key" == ENCRYPTION_SECRET ]] && size=16
    value="$(openssl rand -hex "$size")"
  fi
  printf '\n%s=%s\n' "$key" "$value" >> .env
done < .env.example
[[ -f config/accounts.json ]] || printf '[]\n' > config/accounts.json
compose=(docker compose -f compose.yaml)
case "$mode" in
  --mock) echo 'Legacy Typebot mock is retired. Use the full stack with SEND_MODE=mock.' >&2; exit 1 ;;
  --production)
    for key in BUSINESS_HOST FASTGPT_HOST; do
      grep -Eq "^${key}=.+" .env || { echo "Set $key in .env" >&2; exit 1; }
    done
    for key in BUSINESS_URL FASTGPT_PUBLIC_URL; do
      grep -Eq "^${key}=https://.+" .env || { echo "Set $key to its public HTTPS URL" >&2; exit 1; }
    done
    compose+=(-f compose.production.yaml)
    ;;
  ''|--config-only) ;;
  *) echo 'Usage: bash start.sh [--mock|--production|--config-only]' >&2; exit 1 ;;
esac
"${compose[@]}" config --quiet
[[ "$mode" == --config-only ]] && exit 0
docker info --format '{{.ServerVersion}}'
up_args=(up -d --build --wait --wait-timeout 600)
"${compose[@]}" "${up_args[@]}"
"${compose[@]}" ps
echo 'Startup finished; see docs/PLAN.md for URLs and implementation status.'

