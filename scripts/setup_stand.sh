#!/usr/bin/env bash

set -euo pipefail

CLUSTER_NAME="kind-thesis-self-healing"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_PATH="${PROJECT_ROOT}/demo stend/microservices-demo-main"
PROMETHEUS_RELEASE="prometheus"
PROMETHEUS_NAMESPACE="monitoring"

require_command() {
  local command_name="$1"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command not found: ${command_name}" >&2
    exit 1
  fi
}

wait_for_rollout() {
  local namespace="$1"
  local resource="$2"

  kubectl rollout status "${resource}" -n "${namespace}" --timeout=300s
}

main() {
  require_command docker
  require_command kind
  require_command kubectl
  require_command helm

  if ! kind get clusters | grep -Fxq "${CLUSTER_NAME}"; then
    kind create cluster --name "${CLUSTER_NAME}"
  else
    echo "kind cluster ${CLUSTER_NAME} already exists."
  fi

  kubectl apply -f "${DEMO_PATH}/release/kubernetes-manifests.yaml"

  kubectl create namespace "${PROMETHEUS_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

  helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
  helm repo update >/dev/null

  helm upgrade --install "${PROMETHEUS_RELEASE}" prometheus-community/prometheus \
    --namespace "${PROMETHEUS_NAMESPACE}"

  wait_for_rollout default deployment/frontend
  wait_for_rollout "${PROMETHEUS_NAMESPACE}" deployment/"${PROMETHEUS_RELEASE}"-server

  echo "Research stand is ready."
  echo "Cluster: ${CLUSTER_NAME}"
  echo "Application: Online Boutique"
  echo "Monitoring: Prometheus in namespace ${PROMETHEUS_NAMESPACE}"
}

main "$@"
