"""Operational deployment/readiness primitives for OS AI Core F8."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

_ALLOWED_EXTERNAL = frozenset({"telegram", "google_drive", "client_api", "llm"})


class DeploymentError(ValueError):
    """Deployment configuration is invalid."""


@dataclass(frozen=True)
class DependencyStatus:
    name: str
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    network_mode: str
    dependencies: tuple[DependencyStatus, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "network_mode": self.network_mode,
            "dependencies": [
                {"name": item.name, "status": item.status, "reason": item.reason}
                for item in self.dependencies
            ],
        }


def normalize_dependencies(values: Iterable[str]) -> tuple[str, ...]:
    clean = tuple(sorted({value.strip() for value in values if value.strip()}))
    unknown = set(clean) - _ALLOWED_EXTERNAL
    if unknown:
        raise DeploymentError(f"unknown external dependency: {sorted(unknown)}")
    return clean


def evaluate_readiness(
    *,
    network_mode: str,
    required_external: Iterable[str] = (),
    configured_external: Iterable[str] = (),
    degraded_external: Iterable[str] = (),
) -> ReadinessReport:
    """Fail closed when an external capability cannot actually be reached.

    This does not replace provider-specific health checks.  It provides the
    deployment-level invariant that an explicitly offline runtime cannot claim
    Telegram/Drive/API/LLM readiness.
    """

    if network_mode not in {"online", "offline"}:
        raise DeploymentError("network_mode must be online or offline")
    required = normalize_dependencies(required_external)
    configured = set(normalize_dependencies(configured_external))
    degraded = set(normalize_dependencies(degraded_external))
    statuses: list[DependencyStatus] = []
    for name in required:
        if name not in configured:
            statuses.append(DependencyStatus(name, "UNAVAILABLE", "not_configured"))
        elif network_mode == "offline":
            statuses.append(DependencyStatus(name, "UNAVAILABLE", "offline"))
        elif name in degraded:
            statuses.append(DependencyStatus(name, "UNAVAILABLE", "provider_unhealthy"))
        else:
            statuses.append(DependencyStatus(name, "AVAILABLE"))
    return ReadinessReport(
        ready=all(item.status == "AVAILABLE" for item in statuses),
        network_mode=network_mode,
        dependencies=tuple(statuses),
    )
