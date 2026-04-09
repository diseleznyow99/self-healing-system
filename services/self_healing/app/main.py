"""Application entry point for the self-healing service."""

import logging
import os
import time

from app.analyzer import analyze_snapshot
from app.executor import build_execution_plan, execute_plan
from app.k8s_client import (
    KubernetesConfig,
    KubernetesRequestError,
    get_deployment_status,
    list_pods,
)
from app.monitoring import (
    MetricQuery,
    PrometheusConfig,
    PrometheusRequestError,
    collect_monitoring_snapshot,
)

LOGGER = logging.getLogger(__name__)
LAST_ACTION_TIMESTAMPS: dict[str, float] = {}


def get_monitoring_config() -> PrometheusConfig:
    """Build monitoring configuration from environment variables."""

    base_url = os.getenv(
        "PROMETHEUS_BASE_URL",
        "http://prometheus-server.monitoring.svc.cluster.local",
    )
    return PrometheusConfig(base_url=base_url)


def get_kubernetes_config() -> KubernetesConfig:
    """Build Kubernetes configuration from environment variables."""

    namespace = os.getenv("KUBERNETES_NAMESPACE", "default")
    token = os.getenv("KUBERNETES_TOKEN")
    return KubernetesConfig(
        base_url="https://kubernetes.default.svc",
        namespace=namespace,
        token=token,
    )


def get_poll_interval_seconds() -> int:
    """Return the monitoring interval in seconds."""

    value = os.getenv("POLL_INTERVAL_SECONDS", "30")
    try:
        interval = int(value)
    except ValueError:
        LOGGER.warning(
            "Invalid POLL_INTERVAL_SECONDS value %r. Falling back to 30 seconds.",
            value,
        )
        return 30

    if interval < 1:
        LOGGER.warning(
            "POLL_INTERVAL_SECONDS must be positive. Falling back to 30 seconds.",
        )
        return 30

    return interval


def get_verification_delay_seconds() -> int:
    """Return the delay before result verification in seconds."""

    value = os.getenv("VERIFICATION_DELAY_SECONDS", "10")
    try:
        delay = int(value)
    except ValueError:
        LOGGER.warning(
            "Invalid VERIFICATION_DELAY_SECONDS value %r. Falling back to 10 seconds.",
            value,
        )
        return 10

    if delay < 1:
        LOGGER.warning(
            "VERIFICATION_DELAY_SECONDS must be positive. Falling back to 10 seconds.",
        )
        return 10

    return delay


def get_verification_attempts() -> int:
    """Return the maximum number of verification attempts."""

    value = os.getenv("VERIFICATION_ATTEMPTS", "3")
    try:
        attempts = int(value)
    except ValueError:
        LOGGER.warning(
            "Invalid VERIFICATION_ATTEMPTS value %r. Falling back to 3 attempts.",
            value,
        )
        return 3

    if attempts < 1:
        LOGGER.warning(
            "VERIFICATION_ATTEMPTS must be positive. Falling back to 3 attempts.",
        )
        return 3

    return attempts


def get_target_service_name() -> str:
    """Return the service name used for monitoring and recovery."""

    return os.getenv("TARGET_SERVICE_NAME", "frontend")


def get_target_container_name() -> str:
    """Return the container name used for monitoring and recovery."""

    return os.getenv("TARGET_CONTAINER_NAME", "server")


def get_action_cooldown_seconds() -> int:
    """Return the cooldown period for repeated corrective actions."""

    value = os.getenv("ACTION_COOLDOWN_SECONDS", "180")
    try:
        cooldown = int(value)
    except ValueError:
        LOGGER.warning(
            "Invalid ACTION_COOLDOWN_SECONDS value %r. Falling back to 180 seconds.",
            value,
        )
        return 180

    if cooldown < 0:
        LOGGER.warning(
            "ACTION_COOLDOWN_SECONDS must not be negative. Falling back to 180 seconds.",
        )
        return 180

    return cooldown


def build_monitoring_queries(
    namespace: str,
    service_name: str,
    container_name: str,
) -> list[MetricQuery]:
    """Build Prometheus queries for the selected namespace."""

    return [
        MetricQuery(
            name="frontend_phase",
            expression=(
                f'kube_pod_status_phase{{namespace="{namespace}",'
                f'pod=~"{service_name}-.*",phase="Running"}}'
            ),
        ),
        MetricQuery(
            name="frontend_ready",
            expression=(
                f'kube_pod_container_status_ready{{namespace="{namespace}",'
                f'pod=~"{service_name}-.*",container="{container_name}"}}'
            ),
        ),
        MetricQuery(
            name="frontend_restarts",
            expression=(
                "sum(increase("
                f'kube_pod_container_status_restarts_total{{namespace="{namespace}",'
                f'pod=~"{service_name}-.*",container="{container_name}"}}[2m]))'
            ),
        ),
    ]


def is_deployment_recovered(
    k8s_config: KubernetesConfig,
    deployment_name: str,
    desired_replicas: int,
) -> bool:
    """Check whether a deployment reached the requested healthy state."""

    status = get_deployment_status(k8s_config, deployment_name)
    ready_replicas = int(status.get("readyReplicas", 0))
    available_replicas = int(status.get("availableReplicas", 0))
    return (
        ready_replicas >= desired_replicas
        and available_replicas >= desired_replicas
    )


def is_pod_recovery_complete(
    k8s_config: KubernetesConfig,
    service_name: str,
    container_name: str,
) -> bool:
    """Check whether a restarted pod was replaced by a healthy instance."""

    pods = list_pods(k8s_config, label_selector=f"app={service_name}")
    if not pods:
        return False

    for pod in pods:
        status = pod.get("status", {})
        phase = status.get("phase")
        if phase != "Running":
            continue

        container_statuses = status.get("containerStatuses", [])
        for container_status in container_statuses:
            if container_status.get("name") != container_name:
                continue

            if not container_status.get("ready", False):
                continue

            restart_count = int(container_status.get("restartCount", 0))
            if restart_count == 0:
                return True

    return False


def build_action_key(action_name: str, target_resource: str) -> str:
    """Build a stable key for corrective-action cooldown tracking."""

    return f"{action_name}:{target_resource}"


def is_action_in_cooldown(
    action_name: str,
    target_resource: str,
    cooldown_seconds: int,
) -> bool:
    """Check whether the same corrective action is still in its cooldown period."""

    if cooldown_seconds == 0:
        return False

    action_key = build_action_key(action_name, target_resource)
    last_timestamp = LAST_ACTION_TIMESTAMPS.get(action_key)
    if last_timestamp is None:
        return False

    return (time.time() - last_timestamp) < cooldown_seconds


def remember_action_execution(action_name: str, target_resource: str) -> None:
    """Store the timestamp of a completed corrective action."""

    action_key = build_action_key(action_name, target_resource)
    LAST_ACTION_TIMESTAMPS[action_key] = time.time()


def run_once() -> None:
    """Execute a single placeholder self-healing cycle."""

    monitoring_config = get_monitoring_config()
    k8s_config = get_kubernetes_config()
    verification_delay_seconds = get_verification_delay_seconds()
    verification_attempts = get_verification_attempts()
    action_cooldown_seconds = get_action_cooldown_seconds()
    target_service_name = get_target_service_name()
    target_container_name = get_target_container_name()
    queries = build_monitoring_queries(
        k8s_config.namespace,
        target_service_name,
        target_container_name,
    )

    LOGGER.info("Starting self-healing cycle.")
    try:
        snapshot = collect_monitoring_snapshot(monitoring_config, queries)
        analysis = analyze_snapshot(snapshot)
        LOGGER.info(
            "Analysis result: detected=%s type=%s target=%s",
            analysis.incident_detected,
            analysis.incident_type,
            analysis.target_service,
        )
        plan = build_execution_plan(analysis)
        if plan.action_name and is_action_in_cooldown(
            plan.action_name,
            plan.target_resource,
            action_cooldown_seconds,
        ):
            LOGGER.info(
                "Execution skipped: action=%s target=%s is still in cooldown.",
                plan.action_name,
                plan.target_resource,
            )
            return
        result = execute_plan(plan, k8s_config)
        LOGGER.info(
            "Execution result: success=%s message=%s",
            result.success,
            result.message,
        )
    except (PrometheusRequestError, KubernetesRequestError) as error:
        LOGGER.error("Self-healing cycle failed: %s", error)
        return

    if not plan.action_name or not result.success:
        return

    remember_action_execution(plan.action_name, plan.target_resource)

    for attempt in range(1, verification_attempts + 1):
        try:
            time.sleep(verification_delay_seconds)
            if plan.action_name == "scale_deployment" and plan.desired_replicas is not None:
                recovered = is_deployment_recovered(
                    k8s_config,
                    plan.target_resource,
                    plan.desired_replicas,
                )
                LOGGER.info(
                    "Verification result: attempt=%s recovered=%s action=%s target=%s",
                    attempt,
                    recovered,
                    plan.action_name,
                    plan.target_resource,
                )
            elif plan.action_name == "delete_pod":
                recovered = is_pod_recovery_complete(
                    k8s_config,
                    target_service_name,
                    target_container_name,
                )
                LOGGER.info(
                    "Verification result: attempt=%s recovered=%s action=%s target=%s",
                    attempt,
                    recovered,
                    plan.action_name,
                    plan.target_resource,
                )
            else:
                verification_snapshot = collect_monitoring_snapshot(monitoring_config, queries)
                verification_analysis = analyze_snapshot(verification_snapshot)
                recovered = not verification_analysis.incident_detected
                LOGGER.info(
                    "Verification result: attempt=%s recovered=%s type=%s target=%s",
                    attempt,
                    recovered,
                    verification_analysis.incident_type,
                    verification_analysis.target_service,
                )
        except (PrometheusRequestError, KubernetesRequestError) as error:
            LOGGER.error(
                "Verification failed: attempt=%s error=%s",
                attempt,
                error,
            )
            break
        if recovered:
            break


def main() -> None:
    """Start the self-healing service."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    poll_interval_seconds = get_poll_interval_seconds()
    while True:
        run_once()
        time.sleep(poll_interval_seconds)


if __name__ == "__main__":
    main()
