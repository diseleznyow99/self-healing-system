"""Unit tests for the incident analyzer."""

import unittest

from app.analyzer import analyze_snapshot
from app.monitoring import MonitoringSnapshot


def build_snapshot(metrics: dict[str, object]) -> MonitoringSnapshot:
    """Build a monitoring snapshot for analyzer tests."""

    return MonitoringSnapshot(
        metrics=metrics,
        collected_at="2026-04-07T00:00:00+00:00",
    )


def build_metric_result(value: str) -> list[dict[str, object]]:
    """Build a Prometheus-like instant query result for a metric."""

    return [{"metric": {}, "value": [1775572036.0, value]}]


class AnalyzeSnapshotTests(unittest.TestCase):
    """Verify rule-based incident classification."""

    def test_returns_no_incident_for_healthy_frontend(self) -> None:
        """Healthy phase and readiness should not trigger an incident."""

        snapshot = build_snapshot(
            {
                "frontend_phase": build_metric_result("1"),
                "frontend_ready": build_metric_result("1"),
                "frontend_restarts": build_metric_result("0"),
            }
        )

        analysis = analyze_snapshot(snapshot)

        self.assertFalse(analysis.incident_detected)
        self.assertEqual("", analysis.incident_type)
        self.assertEqual("", analysis.target_service)

    def test_detects_service_unavailable_when_frontend_metrics_are_missing(self) -> None:
        """Missing frontend samples should be treated as service unavailability."""

        snapshot = build_snapshot(
            {
                "frontend_phase": [],
                "frontend_ready": [],
                "frontend_restarts": [],
            }
        )

        analysis = analyze_snapshot(snapshot)

        self.assertTrue(analysis.incident_detected)
        self.assertEqual("service_unavailable", analysis.incident_type)
        self.assertEqual("frontend", analysis.target_service)

    def test_detects_service_unavailable_when_frontend_is_not_ready(self) -> None:
        """Non-ready frontend metrics should trigger availability handling."""

        snapshot = build_snapshot(
            {
                "frontend_phase": build_metric_result("1"),
                "frontend_ready": build_metric_result("0"),
                "frontend_restarts": build_metric_result("0"),
            }
        )

        analysis = analyze_snapshot(snapshot)

        self.assertTrue(analysis.incident_detected)
        self.assertEqual("service_unavailable", analysis.incident_type)
        self.assertEqual("frontend", analysis.target_service)

    def test_detects_restart_incident_when_restart_counter_is_positive(self) -> None:
        """A positive restart counter should trigger the restart incident rule."""

        snapshot = build_snapshot(
            {
                "frontend_phase": build_metric_result("1"),
                "frontend_ready": build_metric_result("1"),
                "frontend_restarts": build_metric_result("2"),
            }
        )

        analysis = analyze_snapshot(snapshot)

        self.assertTrue(analysis.incident_detected)
        self.assertEqual("pod_restarting", analysis.incident_type)
        self.assertEqual("frontend", analysis.target_service)


if __name__ == "__main__":
    unittest.main()
