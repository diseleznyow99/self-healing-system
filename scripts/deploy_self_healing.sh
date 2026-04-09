#!/usr/bin/env bash

set -euo pipefail

CLUSTER_NAME="kind-thesis-self-healing"
IMAGE_NAME="self-healing:v15"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_PATH="${PROJECT_ROOT}/services/self_healing"
MANIFEST_PATH="${PROJECT_ROOT}/infra/k8s/self-healing.yaml"

require_command() {
  local command_name="$1"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command not found: ${command_name}" >&2
    exit 1
  fi
}

main() {
  require_command docker
  require_command kind
  require_command kubectl

  docker build -t "${IMAGE_NAME}" "${SERVICE_PATH}"
  kind load docker-image "${IMAGE_NAME}" --name "${CLUSTER_NAME}"

  kubectl apply -f "${MANIFEST_PATH}"
  kubectl rollout status deployment/self-healing -n default --timeout=300s

  echo "self-healing deployment is ready."
  echo "Image: ${IMAGE_NAME}"
  echo "Manifest: ${MANIFEST_PATH}"
}

main "$@"
