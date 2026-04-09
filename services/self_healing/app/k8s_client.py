"""Kubernetes API integration interfaces."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class KubernetesRequestError(RuntimeError):
    """Represent an error raised during a Kubernetes API request."""


@dataclass(slots=True, frozen=True)
class KubernetesConfig:
    """Store connection settings for the Kubernetes API."""

    base_url: str
    namespace: str
    timeout_seconds: float = 5.0
    token: str | None = None
    ca_cert_path: str | None = None


SERVICE_ACCOUNT_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
SERVICE_ACCOUNT_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def resolve_token(config: KubernetesConfig) -> str | None:
    """Resolve the API token from configuration or service account files."""

    if config.token:
        return config.token

    if SERVICE_ACCOUNT_TOKEN_PATH.exists():
        return SERVICE_ACCOUNT_TOKEN_PATH.read_text(encoding="utf-8").strip()

    return None


def build_ssl_context(config: KubernetesConfig) -> ssl.SSLContext | None:
    """Build an SSL context for secure Kubernetes API requests."""

    ca_cert_path = config.ca_cert_path
    if ca_cert_path is None and SERVICE_ACCOUNT_CA_PATH.exists():
        ca_cert_path = str(SERVICE_ACCOUNT_CA_PATH)

    if ca_cert_path is None:
        return None

    return ssl.create_default_context(cafile=ca_cert_path)


def build_headers(config: KubernetesConfig) -> dict[str, str]:
    """Build HTTP headers for Kubernetes API requests."""

    headers = {"Accept": "application/json"}
    token = resolve_token(config)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def send_kubernetes_request(
    config: KubernetesConfig,
    path: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send an HTTP request to the Kubernetes API."""

    url = f"{config.base_url.rstrip('/')}/{path.lstrip('/')}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = build_headers(config)

    if body is not None:
        headers["Content-Type"] = "application/json"

    request = Request(url=url, data=body, headers=headers, method=method)
    ssl_context = build_ssl_context(config)

    try:
        with urlopen(
            request,
            timeout=config.timeout_seconds,
            context=ssl_context,
        ) as response:
            return json.load(response)
    except HTTPError as error:
        raise KubernetesRequestError(
            f"Kubernetes API HTTP error: {error.code}"
        ) from error
    except URLError as error:
        raise KubernetesRequestError(
            "Kubernetes API is unavailable or returned an invalid response."
        ) from error
    except json.JSONDecodeError as error:
        raise KubernetesRequestError(
            "Kubernetes API returned a response that is not valid JSON."
        ) from error


def list_pods(
    config: KubernetesConfig,
    label_selector: str | None = None,
) -> list[dict[str, Any]]:
    """Return pod objects from the configured namespace."""

    path = f"api/v1/namespaces/{config.namespace}/pods"
    if label_selector:
        path = f"{path}?{urlencode({'labelSelector': label_selector})}"
    payload = send_kubernetes_request(config, path)
    return payload.get("items", [])


def get_deployment_status(
    config: KubernetesConfig,
    deployment_name: str,
) -> dict[str, Any]:
    """Return the status block for a selected deployment."""

    path = f"apis/apps/v1/namespaces/{config.namespace}/deployments/{deployment_name}"
    payload = send_kubernetes_request(config, path)
    return payload.get("status", {})


def get_pod_status(
    config: KubernetesConfig,
    pod_name: str,
) -> dict[str, Any]:
    """Return the status block for a selected pod."""

    path = f"api/v1/namespaces/{config.namespace}/pods/{pod_name}"
    payload = send_kubernetes_request(config, path)
    return payload.get("status", {})


def delete_pod(
    config: KubernetesConfig,
    pod_name: str,
) -> dict[str, Any]:
    """Delete a pod to trigger its recreation by the controller."""

    path = f"api/v1/namespaces/{config.namespace}/pods/{pod_name}"
    return send_kubernetes_request(config, path, method="DELETE")
