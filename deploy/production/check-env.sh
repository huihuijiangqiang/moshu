#!/usr/bin/env bash
set -euo pipefail

env_file="${1:?usage: check-env.sh /path/to/moshu.production.env}"
if [[ ! -f "$env_file" ]]; then
  echo "production env file does not exist" >&2
  exit 1
fi

# Read simple KEY=value lines without sourcing the file, so values cannot execute
# shell code during a deployment check and secrets are never printed.
value_for() {
  local key="$1"
  sed -n -E "s/^${key}=([^#]*)$/\1/p" "$env_file" | head -n 1 | tr -d '\r'
}

required_keys=(
  POSTGRES_PASSWORD DATABASE_URL JWT_SECRET_KEY CREDENTIAL_ENCRYPTION_KEY
  BOOTSTRAP_TOKEN MODEL_GATEWAY_CHEAP_URL MODEL_GATEWAY_CHEAP_KEY
  MODEL_GATEWAY_MAIN_URL MODEL_GATEWAY_MAIN_KEY MODEL_GATEWAY_PREMIUM_URL
  MODEL_GATEWAY_PREMIUM_KEY EMBEDDING_GATEWAY_URL EMBEDDING_GATEWAY_KEY
  S3_ENDPOINT S3_ACCESS_KEY S3_SECRET_KEY
)

for key in "${required_keys[@]}"; do
  value="$(value_for "$key")"
  if [[ -z "$value" || "$value" == *replace-* || "$value" == *example* || "$value" == *your-domain* ]]; then
    echo "missing or placeholder production value: $key" >&2
    exit 1
  fi
done

for key in JWT_SECRET_KEY CREDENTIAL_ENCRYPTION_KEY BOOTSTRAP_TOKEN; do
  value="$(value_for "$key")"
  if (( ${#value} < 32 )); then
    echo "$key must contain at least 32 characters" >&2
    exit 1
  fi
done

echo "production env preflight passed (secrets were not printed)"
