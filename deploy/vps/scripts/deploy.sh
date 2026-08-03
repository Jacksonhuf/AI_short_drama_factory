#!/usr/bin/env bash
set -euo pipefail

# Deploys preloaded Jellyfish images using a persistent, host-local runtime environment.
DEPLOY_PATH="${DEPLOY_PATH:-/opt/jellyfish}"
IMAGE_TAG="${IMAGE_TAG:?IMAGE_TAG is required}"
DOMAIN="${DOMAIN:?DOMAIN is required}"
STACK_PATH="${DEPLOY_PATH}/vps"
ENV_FILE="${DEPLOY_PATH}/.env"

generate_secret() {
  # Generates an opaque runtime secret without placing it in GitHub Actions logs.
  openssl rand -hex 32
}

write_initial_environment() {
  # Creates secrets only on first deployment; subsequent deployments preserve them.
  umask 077
  cat > "${ENV_FILE}" <<EOF
DOMAIN=${DOMAIN}
JELLYFISH_IMAGE_TAG=${IMAGE_TAG}
MYSQL_ROOT_PASSWORD=$(generate_secret)
MYSQL_DATABASE=jellyfish
MYSQL_USER=jellyfish
MYSQL_PASSWORD=$(generate_secret)
REDIS_DB=0
RUSTFS_ACCESS_KEY=jellyfish
RUSTFS_SECRET_KEY=$(generate_secret)
S3_BUCKET_NAME=jellyfish-assets
AUTH_ADMIN_PASSWORD=$(generate_secret)
AUTH_SESSION_SECRET=$(generate_secret)
# Configure an AI provider key on the VPS when real generation is required.
OPENAI_API_KEY=
EOF
}

set_environment_value() {
  # Replaces a non-secret release value while preserving host-local credentials.
  local key="$1"
  local value="$2"
  local temporary_file
  temporary_file="$(mktemp)"
  grep -v "^${key}=" "${ENV_FILE}" > "${temporary_file}" || true
  printf '%s=%s\n' "${key}" "${value}" >> "${temporary_file}"
  mv "${temporary_file}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
}

ensure_environment_value() {
  # Adds a newly introduced secret without replacing credentials from prior deployments.
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" "${ENV_FILE}"; then
    set_environment_value "${key}" "${value}"
  fi
}

if [[ ! -f "${ENV_FILE}" ]]; then
  write_initial_environment
fi

set_environment_value "DOMAIN" "${DOMAIN}"
set_environment_value "JELLYFISH_IMAGE_TAG" "${IMAGE_TAG}"
ensure_environment_value "AUTH_ADMIN_PASSWORD" "$(generate_secret)"
ensure_environment_value "AUTH_SESSION_SECRET" "$(generate_secret)"

docker compose \
  --env-file "${ENV_FILE}" \
  -f "${STACK_PATH}/compose.yml" \
  up -d --remove-orphans

for attempt in $(seq 1 18); do
  if docker compose --env-file "${ENV_FILE}" -f "${STACK_PATH}/compose.yml" \
    exec -T backend curl --fail --silent http://127.0.0.1:8000/health >/dev/null; then
    echo "Jellyfish backend is healthy."
    exit 0
  fi
  sleep 10
done

docker compose --env-file "${ENV_FILE}" -f "${STACK_PATH}/compose.yml" ps
exit 1
