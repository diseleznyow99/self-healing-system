"""Unit tests for corrective action execution."""

import unittest
from unittest.mock import patch

from app.analyzer import IncidentAnalysis
from app.executor import (
    ExecutionPlan,
    build_execution_plan,
    execute_plan,
)
from app.k8s_client import KubernetesConfig


def build_analysis(
    *,
    incident_detected: bool,
    incident_type: str,
    target_service: str,
    details: str,
) -> IncidentAnalysis:
    """Build an incident analysis object for executor tests."""

    return IncidentAnalysis(
        incident_detected=incident_detected,
        incident_type=incident_type,
        target_service=target_service,
        details=details,
    )


def build_k8s_config() -> KubernetesConfig:
    """Build a Kubernetes config for executor tests."""

    return KubernetesConfig(
        base_url="https://kubernetes.default.svc",
        namespace="default",
    )


class BuildExecutionPlanTests(unittest.TestCase):
    """Verify rule-based corrective action selection."""

    def test_returns_empty_action_when_incident_is_not_detected(self) -> None:
        """No incident should produce an empty execution plan."""

        analysis = build_analysis(
            incident_detected=False,
            incident_type="",
            target_service="",
            details="No incident matched the configured rules.",
        )

        plan = build_execution_plan(analysis)

        self.assertEqual("", plan.action_name)
        self.assertIsNone(plan.desired_replicas)

    def test_returns_scale_plan_for_service_unavailable(self) -> None:
        """Service unavailability should produce a deployment scaling plan."""

        analysis = build_analysis(
            incident_detected=True,
            incident_type="service_unavailable",
            target_service="frontend",
            details="Frontend is unavailable.",
        )

        plan = build_execution_plan(analysis)

        self.assertEqual("scale_deployment", plan.action_name)
        self.assertEqual("frontend", plan.target_resource)
        self.assertEqual("", plan.target_namespace)
        self.assertEqual(1, plan.desired_replicas)

    def test_returns_delete_pod_plan_for_other_incidents(self) -> None:
        """Pod-level incidents should keep the delete_pod action."""

        analysis = build_analysis(
            incident_detected=True,
            incident_type="pod_restarting",
            target_service="frontend",
            details="Frontend container restarted.",
        )

        plan = build_execution_plan(analysis)

        self.assertEqual("delete_pod", plan.action_name)
        self.assertEqual("frontend", plan.target_resource)
        self.assertIsNone(plan.desired_replicas)


class ExecutePlanTests(unittest.TestCase):
    """Verify corrective action execution behavior."""

    def test_returns_error_when_no_action_is_selected(self) -> None:
        """An empty plan should not attempt any Kubernetes calls."""

        plan = ExecutionPlan(
            action_name="",
            target_resource="",
            target_namespace="",
            desired_replicas=None,
            reason="No action selected.",
        )

        result = execute_plan(plan, build_k8s_config())

        self.assertFalse(result.success)
        self.assertEqual("No action selected.", result.message)

    def test_returns_error_when_kubernetes_config_is_missing(self) -> None:
        """A real action requires Kubernetes configuration."""

        plan = ExecutionPlan(
            action_name="delete_pod",
            target_resource="frontend",
            target_namespace="default",
            desired_replicas=None,
            reason="Restart the failing pod.",
        )

        result = execute_plan(plan, None)

        self.assertFalse(result.success)
        self.assertEqual("Kubernetes configuration is required.", result.message)

    @patch("app.executor.scale_deployment")
    def test_executes_scale_deployment_action(self, scale_deployment_mock) -> None:
        """Service-unavailable plans should scale the deployment."""

        plan = ExecutionPlan(
            action_name="scale_deployment",
            target_resource="frontend",
            target_namespace="default",
            desired_replicas=1,
            reason="Frontend is unavailable.",
        )

        result = execute_plan(plan, build_k8s_config())

        self.assertTrue(result.success)
        self.assertEqual("Deployment scaled to 1 replicas.", result.message)
        scale_deployment_mock.assert_called_once()

    def test_returns_error_when_scale_plan_has_no_replica_count(self) -> None:
        """Deployment scaling needs the target replica count."""

        plan = ExecutionPlan(
            action_name="scale_deployment",
            target_resource="frontend",
            target_namespace="default",
            desired_replicas=None,
            reason="Frontend is unavailable.",
        )

        result = execute_plan(plan, build_k8s_config())

        self.assertFalse(result.success)
        self.assertEqual(
            "Replica count is required for deployment scaling.",
            result.message,
        )

    @patch("app.executor.delete_pod")
    @patch("app.executor.find_target_pod_name", return_value="frontend-abc123")
    def test_executes_delete_pod_action(
        self,
        find_target_pod_name_mock,
        delete_pod_mock,
    ) -> None:
        """Pod-level corrective actions should delete the matched pod."""

        plan = ExecutionPlan(
            action_name="delete_pod",
            target_resource="frontend",
            target_namespace="default",
            desired_replicas=None,
            reason="Frontend pod is restarting.",
        )

        result = execute_plan(plan, build_k8s_config())

        self.assertTrue(result.success)
        self.assertEqual("Pod deletion request sent.", result.message)
        find_target_pod_name_mock.assert_called_once()
        delete_pod_mock.assert_called_once()

    @patch("app.executor.find_target_pod_name", return_value=None)
    def test_returns_error_when_no_pod_matches_target(
        self,
        find_target_pod_name_mock,
    ) -> None:
        """Delete-pod action should fail cleanly when no pod is found."""

        plan = ExecutionPlan(
            action_name="delete_pod",
            target_resource="frontend",
            target_namespace="default",
            desired_replicas=None,
            reason="Frontend pod is restarting.",
        )

        result = execute_plan(plan, build_k8s_config())

        self.assertFalse(result.success)
        self.assertEqual(
            "No pod matched the selected target resource.",
            result.message,
        )
        find_target_pod_name_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
