"""Incident analysis module interfaces."""

from dataclasses import dataclass
import os

from app.monitoring import MonitoringSnapshot


@dataclass(slots=True)
class IncidentAnalysis:
    """Represent the result of incident analysis."""

    incident_detected: bool
    incident_type: str
    target_service: str
    details: str


def get_target_service_name() -> str:
    """Return the service name used for incident analysis."""

    return os.getenv("TARGET_SERVICE_NAME", "frontend")


def find_metric_value(snapshot: MonitoringSnapshot, metric_name: str) -> float | None:
    """Extract a numeric metric value from a monitoring snapshot."""

    metric_result = snapshot.metrics.get(metric_name)
    if not metric_result:
        return None

    first_item = metric_result[0]
    value_block = first_item.get("value")
    if not isinstance(value_block, list) or len(value_block) < 2:
        return None

    try:
        return float(value_block[1])
    except (TypeError, ValueError):
        return None


def has_metric_samples(snapshot: MonitoringSnapshot, metric_name: str) -> bool:
    """Check whether Prometheus returned at least one sample for a metric."""

    metric_result = snapshot.metrics.get(metric_name)
    return bool(metric_result)


def detect_availability_incident(snapshot: MonitoringSnapshot) -> IncidentAnalysis | None:
    """Detect a service availability incident from Kubernetes phase and readiness."""

    target_service_name = get_target_service_name()
    has_phase_samples = has_metric_samples(snapshot, "frontend_phase")
    has_ready_samples = has_metric_samples(snapshot, "frontend_ready")
    phase_value = find_metric_value(snapshot, "frontend_phase")
    ready_value = find_metric_value(snapshot, "frontend_ready")
    if not has_phase_samples and not has_ready_samples:
        return IncidentAnalysis(
            incident_detected=True,
            incident_type="service_unavailable",
            target_service=target_service_name,
            details=(
                "Prometheus returned no target pod samples, "
                "which indicates service unavailability."
            ),
        )

    if phase_value == 1.0 and ready_value == 1.0:
        return None

    return IncidentAnalysis(
        incident_detected=True,
        incident_type="service_unavailable",
        target_service=target_service_name,
        details=(
            "Prometheus reported that the target pod is not fully running or ready."
        ),
    )


def detect_restart_incident(snapshot: MonitoringSnapshot) -> IncidentAnalysis | None:
    """Detect a service incident from the restart counter."""

    target_service_name = get_target_service_name()
    restart_value = find_metric_value(snapshot, "frontend_restarts")
    if restart_value is None:
        return None

    if restart_value < 1.0:
        return None

    return IncidentAnalysis(
        incident_detected=True,
        incident_type="pod_restarting",
        target_service=target_service_name,
        details="Prometheus reported one or more target container restarts.",
    )


def analyze_snapshot(snapshot: MonitoringSnapshot) -> IncidentAnalysis:
    """Analyze a monitoring snapshot and classify a known incident."""

    availability_incident = detect_availability_incident(snapshot)
    if availability_incident is not None:
        return availability_incident

    restart_incident = detect_restart_incident(snapshot)
    if restart_incident is not None:
        return restart_incident

    return IncidentAnalysis(
        incident_detected=False,
        incident_type="",
        target_service="",
        details="No incident matched the configured rules.",
    )
