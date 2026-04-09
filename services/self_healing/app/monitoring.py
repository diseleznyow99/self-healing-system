"""Monitoring module interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


class PrometheusRequestError(RuntimeError):
    """Represent an error raised during a Prometheus API request."""


@dataclass(slots=True, frozen=True)
class PrometheusConfig:
    """Store connection settings for the Prometheus HTTP API."""

    base_url: str
    timeout_seconds: float = 5.0


@dataclass(slots=True, frozen=True)
class MetricQuery:
    """Describe a metric query to an external monitoring system."""

    name: str
    expression: str


@dataclass(slots=True)
class MonitoringSnapshot:
    """Store monitoring data collected during a single polling cycle."""

    metrics: dict[str, Any]
    collected_at: str


def build_query_url(config: PrometheusConfig, expression: str) -> str:
    """Build a Prometheus instant-query URL."""

    query_string = urlencode({"query": expression})
    base_url = config.base_url.rstrip("/")
    return f"{base_url}/api/v1/query?{query_string}"


def parse_prometheus_response(payload: dict[str, Any]) -> Any:
    """Extract metric data from a Prometheus API response."""

    status = payload.get("status")
    if status != "success":
        raise PrometheusRequestError("Prometheus returned a non-success status.")

    data = payload.get("data", {})
    return data.get("result", [])


def execute_prometheus_query(
    config: PrometheusConfig,
    query: MetricQuery,
) -> Any:
    """Execute an instant query against the Prometheus HTTP API."""

    request_url = build_query_url(config, query.expression)

    try:
        with urlopen(request_url, timeout=config.timeout_seconds) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise PrometheusRequestError(
            f"Prometheus HTTP error: {error.code}"
        ) from error
    except URLError as error:
        raise PrometheusRequestError(
            "Prometheus is unavailable or returned an invalid response."
        ) from error
    except json.JSONDecodeError as error:
        raise PrometheusRequestError(
            "Prometheus returned a response that is not valid JSON."
        ) from error

    return parse_prometheus_response(payload)


def collect_monitoring_snapshot(
    config: PrometheusConfig,
    queries: list[MetricQuery],
) -> MonitoringSnapshot:
    """Collect metric data for a list of Prometheus queries."""

    metrics: dict[str, Any] = {}
    for query in queries:
        metrics[query.name] = execute_prometheus_query(config, query)

    return MonitoringSnapshot(
        metrics=metrics,
        collected_at=datetime.now(UTC).isoformat(),
    )
