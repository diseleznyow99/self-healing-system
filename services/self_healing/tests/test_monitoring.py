"""Unit tests for the monitoring module."""

import unittest
from unittest.mock import patch

from app.monitoring import (
    MetricQuery,
    MonitoringSnapshot,
    PrometheusConfig,
    PrometheusRequestError,
    build_query_url,
    collect_monitoring_snapshot,
    parse_prometheus_response,
)


def build_config() -> PrometheusConfig:
    """Build a Prometheus config for monitoring tests."""

    return PrometheusConfig(base_url="http://prometheus.example")


class BuildQueryUrlTests(unittest.TestCase):
    """Verify Prometheus query URL construction."""

    def test_builds_instant_query_url(self) -> None:
        """URL should include the encoded instant-query expression."""

        config = build_config()

        query_url = build_query_url(
            config,
            'kube_pod_status_phase{namespace="default",phase="Running"}',
        )

        self.assertTrue(query_url.startswith("http://prometheus.example/api/v1/query?"))
        self.assertIn("query=", query_url)
        self.assertIn("kube_pod_status_phase", query_url)


class ParsePrometheusResponseTests(unittest.TestCase):
    """Verify Prometheus API response parsing."""

    def test_returns_result_for_successful_response(self) -> None:
        """A successful response should return the result array."""

        payload = {
            "status": "success",
            "data": {
                "result": [{"metric": {}, "value": [1775572036.0, "1"]}],
            },
        }

        result = parse_prometheus_response(payload)

        self.assertEqual(payload["data"]["result"], result)

    def test_returns_empty_list_when_result_is_missing(self) -> None:
        """Missing result field should be normalized to an empty list."""

        payload = {
            "status": "success",
            "data": {},
        }

        result = parse_prometheus_response(payload)

        self.assertEqual([], result)

    def test_raises_error_for_non_success_status(self) -> None:
        """Non-success Prometheus status should raise a request error."""

        payload = {
            "status": "error",
            "data": {},
        }

        with self.assertRaises(PrometheusRequestError):
            parse_prometheus_response(payload)


class CollectMonitoringSnapshotTests(unittest.TestCase):
    """Verify monitoring snapshot collection."""

    @patch("app.monitoring.execute_prometheus_query")
    def test_collects_metrics_for_all_queries(self, execute_query_mock) -> None:
        """Snapshot collection should store results under query names."""

        execute_query_mock.side_effect = [
            [{"metric": {}, "value": [1775572036.0, "1"]}],
            [{"metric": {}, "value": [1775572036.0, "0"]}],
        ]
        config = build_config()
        queries = [
            MetricQuery(name="frontend_phase", expression="phase_query"),
            MetricQuery(name="frontend_ready", expression="ready_query"),
        ]

        snapshot = collect_monitoring_snapshot(config, queries)

        self.assertIsInstance(snapshot, MonitoringSnapshot)
        self.assertEqual(2, len(snapshot.metrics))
        self.assertIn("frontend_phase", snapshot.metrics)
        self.assertIn("frontend_ready", snapshot.metrics)
        self.assertTrue(snapshot.collected_at)


if __name__ == "__main__":
    unittest.main()
