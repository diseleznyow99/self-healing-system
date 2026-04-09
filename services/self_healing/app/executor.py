"""Corrective action execution module interfaces."""

from dataclasses import dataclass

from app.analyzer import IncidentAnalysis
from app.k8s_client import (
    KubernetesConfig,
    delete_pod,
    list_pods,
    send_kubernetes_request,
)


@dataclass(slots=True)
class ExecutionPlan:
    """Describe a corrective action selected for execution."""

    action_name: str
    target_resource: str
    target_namespace: str
    desired_replicas: int | None
    reason: str


@dataclass(slots=True)
class ExecutionResult:
    """Store the result of a corrective action attempt."""

    success: bool
    message: str


def clone_config_with_namespace(
    config: KubernetesConfig,
    namespace: str,
) -> KubernetesConfig:
    """Return a Kubernetes config bound to a selected namespace."""

    return KubernetesConfig(
        base_url=config.base_url,
        namespace=namespace,
        timeout_seconds=config.timeout_seconds,
        token=config.token,
        ca_cert_path=config.ca_cert_path,
    )


def find_target_pod_name(
    config: KubernetesConfig,
    target_resource: str,
) -> str | None:
    """Return a pod name that matches the selected target resource."""

    pods = list_pods(config, label_selector=f"app={target_resource}")
    for pod in pods:
        metadata = pod.get("metadata", {})
        pod_name = metadata.get("name", "")
        if pod_name:
            return pod_name

    return None


def build_execution_plan(analysis: IncidentAnalysis) -> ExecutionPlan:
    """Return a placeholder execution plan for an incident analysis result."""

    if not analysis.incident_detected:
        return ExecutionPlan(
            action_name="",
            target_resource="",
            target_namespace="",
            desired_replicas=None,
            reason=analysis.details,
        )

    if analysis.incident_type == "service_unavailable":
        return ExecutionPlan(
            action_name="scale_deployment",
            target_resource=analysis.target_service,
            target_namespace="",
            desired_replicas=1,
            reason=analysis.details,
        )

    return ExecutionPlan(
        action_name="delete_pod",
        target_resource=analysis.target_service,
        target_namespace="",
        desired_replicas=None,
        reason=analysis.details,
    )


def scale_deployment(
    config: KubernetesConfig,
    deployment_name: str,
    replicas: int,
) -> dict[str, object]:
    """Scale a deployment to the requested replica count."""

    path = f"apis/apps/v1/namespaces/{config.namespace}/deployments/{deployment_name}/scale"
    payload = {
        "apiVersion": "autoscaling/v1",
        "kind": "Scale",
        "metadata": {"name": deployment_name, "namespace": config.namespace},
        "spec": {"replicas": replicas},
    }
    return send_kubernetes_request(config, path, method="PUT", payload=payload)


def execute_plan(
    plan: ExecutionPlan,
    k8s_config: KubernetesConfig | None = None,
) -> ExecutionResult:
    """Execute a corrective action plan."""

    if not plan.action_name:
        return ExecutionResult(success=False, message="No action selected.")

    if k8s_config is None:
        return ExecutionResult(
            success=False,
            message="Kubernetes configuration is required.",
        )

    target_namespace = plan.target_namespace or k8s_config.namespace
    namespace_config = clone_config_with_namespace(k8s_config, target_namespace)

    if plan.action_name == "scale_deployment":
        if plan.desired_replicas is None:
            return ExecutionResult(
                success=False,
                message="Replica count is required for deployment scaling.",
            )

        scale_deployment(
            namespace_config,
            plan.target_resource,
            plan.desired_replicas,
        )

        return ExecutionResult(
            success=True,
            message=(
                f"Deployment scaled to {plan.desired_replicas} replicas."
            ),
        )

    if plan.action_name != "delete_pod":
        return ExecutionResult(success=False, message="Unsupported action.")

    pod_name = find_target_pod_name(namespace_config, plan.target_resource)
    if pod_name is None:
        return ExecutionResult(
            success=False,
            message="No pod matched the selected target resource.",
        )

    delete_pod(namespace_config, pod_name)

    return ExecutionResult(success=True, message="Pod deletion request sent.")
