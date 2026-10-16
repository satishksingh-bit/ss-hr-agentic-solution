"""HR Agentic Solution (MVP 1) — Core Package.

All modules expose and implement the production interfaces specified in `01-sdd/SDD.md` and `02-architecture/architecture.md`.
Uses PEP 562 lazy attribute loading (`__getattr__`) so invoking `python -m hr_agent.<module>` never triggers eager circular-import `RuntimeWarning`s.
"""

from __future__ import annotations

import importlib
from typing import Any

from hr_agent.models import (
    ALLOWED_PROFILE_COUNTRIES,
    BLOCKED_COUNTRIES,
    FALLBACK_COORDINATOR_MODEL,
    OUT_OF_SCOPE_DOMAINS,
    PRIMARY_COORDINATOR_MODEL,
    SUPPORTED_COUNTRIES,
    SUPPORTED_LANGUAGES,
    ActionPlanState,
    ActorType,
    AuditEvent,
    ConfirmationTokenClaims,
    EmployeePersona,
    ErrorCode,
    FirestoreDatabaseId,
    IdempotencyRecord,
    IdempotencyStatus,
    LeaveType,
    LocalSqliteStore,
    RoleName,
    StepDigest,
    TicketPriority,
    TicketState,
    UserContextTokenClaims,
)

__all__ = [
    "ALLOWED_PROFILE_COUNTRIES",
    "BLOCKED_COUNTRIES",
    "FALLBACK_COORDINATOR_MODEL",
    "OUT_OF_SCOPE_DOMAINS",
    "PRIMARY_COORDINATOR_MODEL",
    "SUPPORTED_COUNTRIES",
    "SUPPORTED_LANGUAGES",
    "ActionPlanState",
    "ActorType",
    "AgentCoordinator",
    "AuditEvent",
    "BffService",
    "ConfirmationTokenClaims",
    "DeploymentTopologyValidator",
    "DsarToolService",
    "EmployeePersona",
    "ErrorCode",
    "FinOpsCostTracker",
    "FirestoreDatabaseId",
    "FirestoreIamMatrixVerifier",
    "HrAdminService",
    "IdempotencyRecord",
    "IdempotencyStateMachine",
    "IdempotencyStatus",
    "InputSafetyGate",
    "KmsTokenSigner",
    "LeaveType",
    "LocalSqliteStore",
    "OutputSafetyGate",
    "PlanReconcilerWorker",
    "PolicyRagService",
    "RbacAuthorizer",
    "RegionAccessGuard",
    "RoleName",
    "ServiceDeskMcpServer",
    "StepDigest",
    "TelemetrySanitizer",
    "TicketPriority",
    "TicketState",
    "ToolManifestAllowlist",
    "UpstreamMockSaasClient",
    "UserContextTokenClaims",
    "WorkWeekMcpServer",
]

_SYMBOL_TO_MODULE: dict[str, str] = {
    "BffService": "hr_agent.bff",
    "DeploymentTopologyValidator": "hr_agent.ops",
    "DsarToolService": "hr_agent.ops",
    "FinOpsCostTracker": "hr_agent.ops",
    "FirestoreIamMatrixVerifier": "hr_agent.ops",
    "HrAdminService": "hr_agent.ops",
    "AgentCoordinator": "hr_agent.orchestrator",
    "IdempotencyStateMachine": "hr_agent.orchestrator",
    "PlanReconcilerWorker": "hr_agent.orchestrator",
    "PolicyRagService": "hr_agent.policy_rag",
    "InputSafetyGate": "hr_agent.security",
    "KmsTokenSigner": "hr_agent.security",
    "OutputSafetyGate": "hr_agent.security",
    "RbacAuthorizer": "hr_agent.security",
    "RegionAccessGuard": "hr_agent.security",
    "TelemetrySanitizer": "hr_agent.security",
    "ToolManifestAllowlist": "hr_agent.security",
    "ServiceDeskMcpServer": "hr_agent.servicedesk_mcp",
    "UpstreamMockSaasClient": "hr_agent.upstream_client",
    "WorkWeekMcpServer": "hr_agent.workweek_mcp",
}


def __getattr__(name: str) -> Any:
    if name in _SYMBOL_TO_MODULE:
        return getattr(importlib.import_module(_SYMBOL_TO_MODULE[name]), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
