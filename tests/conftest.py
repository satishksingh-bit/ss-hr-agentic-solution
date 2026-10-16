"""Deterministic pytest fixtures, synthetic personas, and mock SaaS constants for HR Agentic Solution (MVP 1).

No real secrets, production tokens, or personal employee data are ever used in tests.
"""

from __future__ import annotations

import pytest

from hr_agent.bff import BffService
from hr_agent.models import EmployeePersona
from hr_agent.ops import (
    DeploymentTopologyValidator,
    DsarToolService,
    FinOpsCostTracker,
    FirestoreIamMatrixVerifier,
    HrAdminService,
)
from hr_agent.orchestrator import (
    AgentCoordinator,
    IdempotencyStateMachine,
    PlanReconcilerWorker,
)
from hr_agent.policy_rag import PolicyRagService
from hr_agent.security import (
    InputSafetyGate,
    KmsTokenSigner,
    OutputSafetyGate,
    RbacAuthorizer,
    RegionAccessGuard,
    TelemetrySanitizer,
    ToolManifestAllowlist,
)
from hr_agent.servicedesk_mcp import ServiceDeskMcpServer
from hr_agent.upstream_client import UpstreamMockSaasClient
from hr_agent.workweek_mcp import WorkWeekMcpServer

FIXED_NOW_EPOCH = 1780000000  # Deterministic epoch timestamp (2026-05-28T20:26:40Z)
CANARY_SPII_TOKEN = "CANARY-SPII-99412"


@pytest.fixture
def fixed_now() -> int:
    """Deterministic timestamp fixture for all token TTL, lease, and cooldown tests."""
    return FIXED_NOW_EPOCH


@pytest.fixture
def canary_spii() -> str:
    """Synthetic canary SPII token for zero-content-capture telemetry verification (FR-014, NFR-007)."""
    return CANARY_SPII_TOKEN


@pytest.fixture
def persona_nl() -> EmployeePersona:
    """Synthetic Netherlands (NL) employee persona (English-only per CON-005)."""
    return EmployeePersona(
        employee_id="EMP-NL-001",
        email="eva.bakker.synthetic@example.invalid",
        full_name="Eva Bakker",
        country="NL",
        legal_entity="NL-BV-01",
        department="Engineering",
        role="Senior Software Engineer",
        manager_employee_id="EMP-NL-MGR-01",
        manager_name="Pieter Jansen",
        manager_email="pieter.jansen.synthetic@example.invalid",
        hire_date="2022-03-01",
        fte=1.0,
        preferred_language="en",
        timezone="Europe/Amsterdam",
        work_location_type="REMOTE",
        personal_phone="+31612345678",
        home_address={
            "street": "Keizersgracht 100",
            "city": "Amsterdam",
            "postal_code": "1015 AA",
            "country": "NL",
        },
        roles=("employee",),
    )


@pytest.fixture
def persona_de() -> EmployeePersona:
    """Synthetic Germany (DE) employee persona (English-only per CON-005)."""
    return EmployeePersona(
        employee_id="EMP-DE-002",
        email="lukas.weber.synthetic@example.invalid",
        full_name="Lukas Weber",
        country="DE",
        legal_entity="DE-GMBH-01",
        department="Product Management",
        role="Product Manager",
        manager_employee_id="EMP-DE-MGR-01",
        manager_name="Hannah Schmidt",
        manager_email="hannah.schmidt.synthetic@example.invalid",
        hire_date="2023-01-15",
        fte=0.8,
        preferred_language="en",
        timezone="Europe/Berlin",
        work_location_type="HYBRID",
        personal_phone="+4915123456789",
        home_address={
            "street": "Friedrichstrasse 50",
            "city": "Berlin",
            "postal_code": "10117",
            "country": "DE",
        },
        roles=("employee",),
    )


@pytest.fixture
def persona_cn() -> EmployeePersona:
    """Synthetic Mainland China (CN) employee persona (allowed per FR-028, English-only per CON-005)."""
    return EmployeePersona(
        employee_id="EMP-CN-003",
        email="wei.chen.synthetic@example.invalid",
        full_name="Wei Chen",
        country="CN",
        legal_entity="CN-SH-01",
        department="Cloud Operations",
        role="Site Reliability Engineer",
        manager_employee_id="EMP-CN-MGR-01",
        manager_name="Lin Zhang",
        manager_email="lin.zhang.synthetic@example.invalid",
        hire_date="2021-09-01",
        fte=1.0,
        preferred_language="en",
        timezone="Asia/Shanghai",
        work_location_type="REMOTE",
        personal_phone="+8613800138000",
        home_address={
            "street": "Century Avenue 88",
            "city": "Shanghai",
            "postal_code": "200120",
            "country": "CN",
        },
        roles=("employee",),
    )


@pytest.fixture
def persona_tw_blocked() -> EmployeePersona:
    """Synthetic Taiwan (TW) persona used exclusively for negative regional block testing (FR-027)."""
    return EmployeePersona(
        employee_id="EMP-TW-999",
        email="blocked.tw.synthetic@example.invalid",
        full_name="Blocked Taiwan Test Persona",
        country="TW",
        legal_entity="TW-BLOCKED",
        department="Test",
        role="Test",
        manager_employee_id="EMP-MGR-99",
        manager_name="Test Manager",
        manager_email="mgr.synthetic@example.invalid",
        hire_date="2024-01-01",
        fte=1.0,
        preferred_language="en",
        timezone="Asia/Taipei",
        work_location_type="OFFICE",
        personal_phone="+886912345678",
        home_address={
            "street": "Section 5 Xinyi Road",
            "city": "Taipei",
            "postal_code": "110",
            "country": "TW",
        },
        roles=("employee",),
    )


@pytest.fixture
def kms_signer() -> KmsTokenSigner:
    return KmsTokenSigner()


@pytest.fixture
def rbac_authorizer() -> RbacAuthorizer:
    return RbacAuthorizer()


@pytest.fixture
def region_guard() -> RegionAccessGuard:
    return RegionAccessGuard()


@pytest.fixture
def input_safety_gate() -> InputSafetyGate:
    return InputSafetyGate()


@pytest.fixture
def output_safety_gate() -> OutputSafetyGate:
    return OutputSafetyGate()


@pytest.fixture
def tool_allowlist() -> ToolManifestAllowlist:
    return ToolManifestAllowlist()


@pytest.fixture
def telemetry_sanitizer() -> TelemetrySanitizer:
    return TelemetrySanitizer()


@pytest.fixture
def workweek_mcp() -> WorkWeekMcpServer:
    return WorkWeekMcpServer()


@pytest.fixture
def servicedesk_mcp() -> ServiceDeskMcpServer:
    return ServiceDeskMcpServer()


@pytest.fixture
def policy_rag() -> PolicyRagService:
    return PolicyRagService()


@pytest.fixture
def coordinator() -> AgentCoordinator:
    return AgentCoordinator()


@pytest.fixture
def idempotency_sm() -> IdempotencyStateMachine:
    return IdempotencyStateMachine()


@pytest.fixture
def reconciler() -> PlanReconcilerWorker:
    return PlanReconcilerWorker()


@pytest.fixture
def bff_service() -> BffService:
    return BffService()


@pytest.fixture
def hr_admin() -> HrAdminService:
    return HrAdminService()


@pytest.fixture
def dsar_tool() -> DsarToolService:
    return DsarToolService()


@pytest.fixture
def iam_verifier() -> FirestoreIamMatrixVerifier:
    return FirestoreIamMatrixVerifier()


@pytest.fixture
def finops_tracker() -> FinOpsCostTracker:
    return FinOpsCostTracker()


@pytest.fixture
def topology_validator() -> DeploymentTopologyValidator:
    return DeploymentTopologyValidator()


@pytest.fixture
def upstream_client() -> UpstreamMockSaasClient:
    return UpstreamMockSaasClient()
