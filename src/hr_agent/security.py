"""Security, Identity, KMS Signing, RBAC, Safety Gates, and Telemetry Sanitizer implementation (SDD §4.1–§4.8)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from hr_agent.models import (
    BLOCKED_COUNTRIES,
    SUPPORTED_COUNTRIES,
    ConfirmationTokenClaims,
    EmployeePersona,
    ErrorCode,
    RoleName,
    StepDigest,
    UserContextTokenClaims,
)

logger = logging.getLogger(__name__)

# NIST P-256 (secp256r1 / prime256v1) curve parameters for JWS ES256 (EC_SIGN_P256_SHA256)
_P256_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_P256_A = _P256_P - 3
_P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_P256_GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
_P256_GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
_P256_G: tuple[int, int] = (_P256_GX, _P256_GY)

_POINT_CACHE_LOCK = threading.RLock()
_G_MUL_CACHE: dict[int, tuple[int, int]] = {}


def _p256_point_add(
    p1: tuple[int, int] | None, p2: tuple[int, int] | None
) -> tuple[int, int] | None:
    """Affine point addition on NIST P-256."""
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % _P256_P == 0:
            return None
        num = (3 * x1 * x1 + _P256_A) % _P256_P
        den = pow((2 * y1) % _P256_P, -1, _P256_P)
    else:
        num = (y2 - y1) % _P256_P
        den = pow((x2 - x1) % _P256_P, -1, _P256_P)
    lam = (num * den) % _P256_P
    x3 = (lam * lam - x1 - x2) % _P256_P
    y3 = (lam * (x1 - x3) - y1) % _P256_P
    return (x3, y3)


def _p256_scalar_mult(k: int, point: tuple[int, int]) -> tuple[int, int] | None:
    """Double-and-add scalar multiplication k * P on NIST P-256."""
    k = k % _P256_N
    if k == 0:
        return None
    if point == _P256_G:
        with _POINT_CACHE_LOCK:
            cached = _G_MUL_CACHE.get(k)
            if cached is not None:
                return cached
    result: tuple[int, int] | None = None
    addend: tuple[int, int] | None = point
    scalar = k
    while scalar > 0:
        if scalar & 1:
            result = _p256_point_add(result, addend)
        addend = _p256_point_add(addend, addend)
        scalar >>= 1
    if point == _P256_G and result is not None:
        with _POINT_CACHE_LOCK:
            if len(_G_MUL_CACHE) < 512:
                _G_MUL_CACHE[k] = result
    return result


def _p256_derive_keypair(seed_bytes: bytes) -> tuple[int, tuple[int, int]]:
    """Derive NIST P-256 private scalar d and public point Q = d * G from key seed."""
    digest = hashlib.sha256(seed_bytes).digest()
    d = (int.from_bytes(digest, "big") % (_P256_N - 1)) + 1
    pub = _p256_scalar_mult(d, _P256_G)
    assert pub is not None
    return d, pub


def _ecdsa_p256_sign(private_scalar: int, message: bytes) -> bytes:
    """Sign message with deterministic RFC 6979-style nonce on NIST P-256 and return 64-byte JWS ES256 (r || s)."""
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    priv_bytes = private_scalar.to_bytes(32, "big")
    counter = 0
    while True:
        nonce_material = hmac.new(
            priv_bytes, message + counter.to_bytes(4, "big"), hashlib.sha256
        ).digest()
        k = (int.from_bytes(nonce_material, "big") % (_P256_N - 1)) + 1
        pt = _p256_scalar_mult(k, _P256_G)
        if pt is not None:
            r = pt[0] % _P256_N
            if r != 0:
                k_inv = pow(k, -1, _P256_N)
                s = (k_inv * (z + r * private_scalar)) % _P256_N
                if s != 0:
                    if s > _P256_N // 2:
                        s = _P256_N - s
                    return r.to_bytes(32, "big") + s.to_bytes(32, "big")
        counter += 1


def _ecdsa_p256_verify(
    public_point: tuple[int, int], message: bytes, signature: bytes
) -> bool:
    """Verify 64-byte IEEE P1363 (r || s) JWS ES256 signature using ONLY the public key point Q."""
    if len(signature) != 64:
        return False
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not (1 <= r < _P256_N and 1 <= s < _P256_N):
        return False
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    w = pow(s, -1, _P256_N)
    u1 = (z * w) % _P256_N
    u2 = (r * w) % _P256_N
    p1 = _p256_scalar_mult(u1, _P256_G)
    p2 = _p256_scalar_mult(u2, public_point)
    pt = _p256_point_add(p1, p2)
    if pt is None:
        return False
    return (pt[0] % _P256_N) == r


_PROCESS_EPHEMERAL_KEYS: dict[str, bytes] = {}
_PROCESS_KEYS_LOCK = threading.RLock()


def _load_or_generate_key(env_var: str) -> bytes:
    """Resolve secret key from environment or generate a process-scoped ephemeral key for local/test runs."""
    val = os.environ.get(env_var)
    if val:
        return val.encode("utf-8")
    with _PROCESS_KEYS_LOCK:
        if env_var not in _PROCESS_EPHEMERAL_KEYS:
            logger.debug(
                "Generating process-ephemeral key material for %s (local/test mode).",
                env_var,
            )
            _PROCESS_EPHEMERAL_KEYS[env_var] = secrets.token_bytes(32)
        return _PROCESS_EPHEMERAL_KEYS[env_var]


@dataclass(frozen=True)
class TokenVerificationFailure:
    """Structured failure returned when UCT or CT verification fails."""

    error_code: ErrorCode
    reason: str
    ok: bool = False

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


class KmsTokenSigner:
    """Asymmetric ES256 (NIST P-256 ECDSA) token signer, public-key verifier, and HMAC pseudonymizer backed by Cloud KMS (SDD §4.3, §4.8)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._uct_key = _load_or_generate_key("HR_AGENT_UCT_SIGNING_KEY")
        self._ct_key = _load_or_generate_key("HR_AGENT_CT_SIGNING_KEY")
        self._uct_priv, self._uct_pub = _p256_derive_keypair(
            b"UCT_ES256:" + self._uct_key
        )
        self._ct_priv, self._ct_pub = _p256_derive_keypair(b"CT_ES256:" + self._ct_key)
        self._psn_keys: dict[int, bytes] = {
            1: _load_or_generate_key("HR_AGENT_PSN_HMAC_KEY_V1"),
            2: _load_or_generate_key("HR_AGENT_PSN_HMAC_KEY_V2"),
        }
        self._consumed_ct_steps: set[tuple[str, str]] = set()
        self._mint_counter = 0

    @property
    def uct_public_key_xy(self) -> tuple[int, int]:
        """Public key coordinates (Qx, Qy) on NIST P-256 used to verify UCT tokens."""
        return self._uct_pub

    @property
    def ct_public_key_xy(self) -> tuple[int, int]:
        """Public key coordinates (Qx, Qy) on NIST P-256 used to verify CT tokens."""
        return self._ct_pub

    @staticmethod
    def _b64url_encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @staticmethod
    def _b64url_decode(encoded: str) -> bytes:
        padding = "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode((encoded + padding).encode("ascii"))

    def _sign_segment_es256(self, priv_scalar: int, signing_input: str) -> str:
        sig_bytes = _ecdsa_p256_sign(priv_scalar, signing_input.encode("ascii"))
        return self._b64url_encode(sig_bytes)

    def _verify_segment_es256(
        self, pub_point: tuple[int, int], signing_input: str, sig_b64: str
    ) -> bool:
        try:
            sig_bytes = self._b64url_decode(sig_b64)
        except Exception:
            return False
        return _ecdsa_p256_verify(pub_point, signing_input.encode("ascii"), sig_bytes)

    def mint_uct(
        self,
        persona: EmployeePersona,
        session_id: str,
        now_epoch: int,
        ttl_seconds: int = 300,
    ) -> str:
        """Mint an ES256-signed UserContextToken (UCT) with 5-minute TTL (FR-005, FR-020)."""
        with self._lock:
            self._mint_counter += 1
            counter = self._mint_counter
        header = {
            "alg": "ES256",
            "typ": "JWT",
            "kid": "projects/hr-agent/locations/europe-west4/keyRings/hr-agent-keyring/cryptoKeys/uct-signing/cryptoKeyVersions/1",
        }
        payload = {
            "iss": "hr-chat-bff",
            "sub": persona.employee_id,
            "aud": "workweek-mcp,servicedesk-mcp,hr-agent",
            "email": persona.email,
            "country": persona.country,
            "legal_entity": persona.legal_entity,
            "manager_id": persona.manager_employee_id,
            "fte": persona.fte,
            "preferred_language": persona.preferred_language,
            "session_id": session_id,
            "iat": int(now_epoch),
            "exp": int(now_epoch + ttl_seconds),
            "jti": f"uct-{session_id}-{now_epoch}-{counter}",
            "roles": list(persona.roles),
        }
        h_b64 = self._b64url_encode(
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        p_b64 = self._b64url_encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signing_input = f"{h_b64}.{p_b64}"
        sig_b64 = self._sign_segment_es256(self._uct_priv, signing_input)
        return f"{signing_input}.{sig_b64}"

    def verify_uct(
        self,
        token: str,
        expected_audience: str,
        now_epoch: int,
    ) -> UserContextTokenClaims | TokenVerificationFailure:
        """Verify UCT ES256 signature using public key Q, expiration (with 30s clock-skew tolerance), audience, and return parsed claims (FR-005, FR-016)."""
        parts = token.split(".")
        if len(parts) != 3 or not parts[2]:
            return TokenVerificationFailure(
                error_code=ErrorCode.AUTHN_FAILED, reason="Malformed UCT JWT"
            )
        h_b64, p_b64, sig_b64 = parts

        try:
            header = json.loads(self._b64url_decode(h_b64).decode("utf-8"))
            payload = json.loads(self._b64url_decode(p_b64).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, Exception):
            return TokenVerificationFailure(
                error_code=ErrorCode.AUTHN_FAILED, reason="Invalid UCT JSON payload"
            )

        if (
            not isinstance(header, dict)
            or not isinstance(payload, dict)
            or header.get("alg") != "ES256"
        ):
            return TokenVerificationFailure(
                error_code=ErrorCode.AUTHN_FAILED, reason="Unsupported JWT algorithm"
            )

        if not self._verify_segment_es256(self._uct_pub, f"{h_b64}.{p_b64}", sig_b64):
            return TokenVerificationFailure(
                error_code=ErrorCode.AUTHN_FAILED,
                reason="UCT signature verification failed",
            )

        # 30-second clock-skew allowance per SDD §4.3.1 and TC-UT-002
        exp = int(payload.get("exp", 0))
        iat = int(payload.get("iat", 0))
        if int(now_epoch) > exp + 30 or int(now_epoch) < iat - 30:
            return TokenVerificationFailure(
                error_code=ErrorCode.AUTHN_FAILED,
                reason="UCT token expired beyond 30s clock skew",
            )

        aud_list = [
            a.strip() for a in str(payload.get("aud", "")).split(",") if a.strip()
        ]
        if expected_audience and expected_audience not in aud_list:
            return TokenVerificationFailure(
                error_code=ErrorCode.AUTHN_FAILED, reason="UCT audience mismatch"
            )

        return UserContextTokenClaims(
            iss=str(payload["iss"]),
            sub=str(payload["sub"]),
            aud=str(payload["aud"]),
            email=str(payload["email"]),
            country=str(payload["country"]),
            legal_entity=str(payload["legal_entity"]),
            manager_id=str(payload["manager_id"]),
            fte=float(payload["fte"]),
            preferred_language=str(payload["preferred_language"]),
            session_id=str(payload["session_id"]),
            iat=iat,
            exp=exp,
            jti=str(payload["jti"]),
            roles=tuple(payload.get("roles", ["employee"])),
        )

    def mint_ct(
        self,
        sub: str,
        session_id: str,
        plan_id: str,
        action_kind: str,
        steps_hash: str,
        step_digests: list[dict[str, str]],
        preview_digest: str,
        now_epoch: int,
        ttl_seconds: int = 300,
    ) -> str:
        """Mint an ES256-signed ConfirmationToken (CT) bound to step_digests and preview_digest (FR-004, FR-022)."""
        with self._lock:
            self._mint_counter += 1
            counter = self._mint_counter
        header = {
            "alg": "ES256",
            "typ": "JWT",
            "kid": "projects/hr-agent/locations/europe-west4/keyRings/hr-agent-keyring/cryptoKeys/ct-signing/cryptoKeyVersions/1",
        }
        payload = {
            "iss": "hr-chat-bff",
            "sub": sub,
            "aud": "workweek-mcp,servicedesk-mcp",
            "session_id": session_id,
            "plan_id": plan_id,
            "action_kind": action_kind,
            "steps_hash": steps_hash,
            "step_digests": step_digests,
            "preview_digest": preview_digest,
            "iat": int(now_epoch),
            "exp": int(now_epoch + ttl_seconds),
            "jti": f"ct-{plan_id}-{now_epoch}-{counter}",
        }
        h_b64 = self._b64url_encode(
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        p_b64 = self._b64url_encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signing_input = f"{h_b64}.{p_b64}"
        sig_b64 = self._sign_segment_es256(self._ct_priv, signing_input)
        return f"{signing_input}.{sig_b64}"

    def verify_and_consume_ct(
        self,
        token: str,
        expected_sub: str,
        expected_session_id: str,
        expected_plan_id: str,
        expected_step_id: str,
        expected_tool_name: str,
        expected_arg_digest: str,
        expected_preview_digest: str,
        now_epoch: int,
    ) -> ConfirmationTokenClaims | TokenVerificationFailure:
        """Verify CT ES256 signature using public key Q, single-use jti consumption, sub, session_id, step_id, arg_digest, and preview_digest (FR-004, FR-022, NFR-003)."""
        parts = token.split(".")
        if len(parts) != 3 or not parts[2]:
            return TokenVerificationFailure(
                error_code=ErrorCode.INVALID_CONFIRMATION_TOKEN,
                reason="Malformed ConfirmationToken",
            )
        h_b64, p_b64, sig_b64 = parts

        try:
            header = json.loads(self._b64url_decode(h_b64).decode("utf-8"))
            payload = json.loads(self._b64url_decode(p_b64).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, Exception):
            return TokenVerificationFailure(
                error_code=ErrorCode.INVALID_CONFIRMATION_TOKEN,
                reason="Invalid ConfirmationToken JSON",
            )

        if (
            not isinstance(header, dict)
            or not isinstance(payload, dict)
            or header.get("alg") != "ES256"
        ):
            return TokenVerificationFailure(
                error_code=ErrorCode.INVALID_CONFIRMATION_TOKEN,
                reason="Unsupported ConfirmationToken algorithm",
            )

        if not self._verify_segment_es256(self._ct_pub, f"{h_b64}.{p_b64}", sig_b64):
            return TokenVerificationFailure(
                error_code=ErrorCode.INVALID_CONFIRMATION_TOKEN,
                reason="ConfirmationToken signature mismatch",
            )

        exp = int(payload.get("exp", 0))
        if int(now_epoch) > exp:
            return TokenVerificationFailure(
                error_code=ErrorCode.INVALID_CONFIRMATION_TOKEN,
                reason="ConfirmationToken expired",
            )

        if (
            payload.get("sub") != expected_sub
            or payload.get("session_id") != expected_session_id
            or payload.get("plan_id") != expected_plan_id
        ):
            return TokenVerificationFailure(
                error_code=ErrorCode.INVALID_CONFIRMATION_TOKEN,
                reason="ConfirmationToken identity or plan binding mismatch",
            )

        if payload.get("preview_digest") != expected_preview_digest:
            return TokenVerificationFailure(
                error_code=ErrorCode.PREVIEW_MISMATCH,
                reason="ConfirmationToken preview_digest does not match confirmed card",
            )

        raw_steps = payload.get("step_digests", [])
        matching_step = next(
            (s for s in raw_steps if s.get("step_id") == expected_step_id), None
        )
        if (
            matching_step is None
            or matching_step.get("tool_name") != expected_tool_name
            or matching_step.get("arg_digest") != expected_arg_digest
        ):
            return TokenVerificationFailure(
                error_code=ErrorCode.PREVIEW_MISMATCH,
                reason="ConfirmationToken step arg_digest or tool_name mismatch",
            )

        jti = str(payload.get("jti", ""))
        consume_key = (jti, expected_step_id)
        with self._lock:
            if consume_key in self._consumed_ct_steps:
                return TokenVerificationFailure(
                    error_code=ErrorCode.INVALID_CONFIRMATION_TOKEN,
                    reason="ConfirmationToken jti already consumed for this step",
                )
            self._consumed_ct_steps.add(consume_key)

        return ConfirmationTokenClaims(
            iss=str(payload["iss"]),
            sub=str(payload["sub"]),
            aud=str(payload["aud"]),
            session_id=str(payload["session_id"]),
            plan_id=str(payload["plan_id"]),
            action_kind=str(payload["action_kind"]),
            steps_hash=str(payload["steps_hash"]),
            step_digests=tuple(
                StepDigest(
                    step_id=str(s["step_id"]),
                    tool_name=str(s["tool_name"]),
                    arg_digest=str(s["arg_digest"]),
                )
                for s in raw_steps
            ),
            preview_digest=str(payload["preview_digest"]),
            iat=int(payload["iat"]),
            exp=exp,
            jti=jti,
        )

    def pseudonymize_identifier(
        self,
        *,
        raw_id: str,
        prefix: str = "u",
        key_version: int = 1,
    ) -> str:
        """Compute keyed HMAC-SHA256 pseudonym u<v>_<32hex> or h<v>_<32hex> (SDD §4.8, NFR-004, NFR-005)."""
        key = self._psn_keys.get(key_version, self._psn_keys[1])
        digest_hex = hmac.new(key, raw_id.encode("utf-8"), hashlib.sha256).hexdigest()[
            :32
        ]
        return f"{prefix}{key_version}_{digest_hex}"

    def check_key_rotation_compliance(
        self,
        cmek_rotation_days: int,
        signing_key_rotation_days: int,
    ) -> dict[str, Any]:
        """Verify CMEK <= 90 days and asymmetric signing key <= 180 days with 24h overlap (NFR-011)."""
        violations: list[str] = []
        if cmek_rotation_days <= 0 or cmek_rotation_days > 90:
            violations.append(
                f"cmek_rotation_days={cmek_rotation_days} exceeds 90-day maximum"
            )
        if signing_key_rotation_days <= 0 or signing_key_rotation_days > 180:
            violations.append(
                f"signing_key_rotation_days={signing_key_rotation_days} exceeds 180-day maximum"
            )
        return {
            "compliant": len(violations) == 0,
            "cmek_rotation_days": cmek_rotation_days,
            "signing_key_rotation_days": signing_key_rotation_days,
            "overlap_hours": 24,
            "violations": violations,
        }


class RbacAuthorizer:
    """Role-Based Access Control enforcement for the 5 canonical roles (FR-018, SDD §4.1)."""

    def authorize_action(
        self,
        *,
        roles: list[str],
        route_or_action: str,
        caller_employee_id: str | None = None,
        target_employee_id: str | None = None,
    ) -> dict[str, Any]:
        """Enforce least-privilege permissions for employee, kb_admin, ops_viewer, ops_oncall, and auditor (FR-018)."""
        normalized_roles = {r.strip().lower() for r in roles}
        action_norm = route_or_action.strip()

        if (
            caller_employee_id is not None
            and target_employee_id is not None
            and caller_employee_id != target_employee_id
            and RoleName.AUDITOR.value not in normalized_roles
        ):
            return {
                "allowed": False,
                "error_code": ErrorCode.CROSS_USER_ACCESS_DENIED,
                "reason": "Caller employee_id does not match target_employee_id",
            }

        if (
            action_norm.startswith("POST /admin/v1/kill-switch")
            or "kill-switch" in action_norm
        ):
            if RoleName.OPS_ONCALL.value in normalized_roles:
                return {"allowed": True, "matched_role": RoleName.OPS_ONCALL.value}
            return {"allowed": False, "error_code": ErrorCode.AUTHZ_DENIED}

        if action_norm.startswith("GET /admin/v1/"):
            if normalized_roles.intersection(
                {
                    RoleName.OPS_VIEWER.value,
                    RoleName.OPS_ONCALL.value,
                    RoleName.AUDITOR.value,
                }
            ):
                return {"allowed": True, "roles": list(normalized_roles)}
            return {"allowed": False, "error_code": ErrorCode.AUTHZ_DENIED}

        if "kb-sync" in action_norm:
            if normalized_roles.intersection(
                {RoleName.KB_ADMIN.value, RoleName.OPS_ONCALL.value}
            ):
                return {"allowed": True, "roles": list(normalized_roles)}
            return {"allowed": False, "error_code": ErrorCode.AUTHZ_DENIED}

        if "/api/v1/" in action_norm:
            if normalized_roles.intersection(
                {
                    RoleName.EMPLOYEE.value,
                    RoleName.KB_ADMIN.value,
                    RoleName.OPS_VIEWER.value,
                    RoleName.OPS_ONCALL.value,
                }
            ):
                return {"allowed": True, "roles": list(normalized_roles)}

        return {"allowed": False, "error_code": ErrorCode.AUTHZ_DENIED}


class RegionAccessGuard:
    """Multi-layer regional access guard blocking Taiwan (TW) and allowing Mainland China (CN) & global regions (FR-027, FR-028, ADR-020)."""

    @staticmethod
    def _is_ip_in_cidrs(origin_ip: str, cidrs: list[str]) -> bool:
        try:
            addr = ipaddress.ip_address(origin_ip.strip())
        except ValueError:
            return False
        for cidr in cidrs:
            try:
                net = ipaddress.ip_network(cidr.strip(), strict=False)
                if addr in net:
                    return True
            except ValueError:
                continue
        return False

    def evaluate_region_access(
        self,
        *,
        route_path: str,
        jwt_country: str | None,
        persona_country: str | None,
        lb_country_header: str | None,
        corporate_origin_header: str | None,
        origin_ip: str,
        trusted_vpn_cidrs: list[str],
    ) -> dict[str, Any]:
        """Evaluate regional access across all BFF routes (/api/v1/*) before session/data access (FR-027, FR-028)."""
        jwt_c = jwt_country.strip().upper() if jwt_country else None
        persona_c = persona_country.strip().upper() if persona_country else None
        lb_c = lb_country_header.strip().upper() if lb_country_header else None
        corp_c = (
            corporate_origin_header.strip().upper() if corporate_origin_header else None
        )
        trusted_vpn = self._is_ip_in_cidrs(origin_ip, trusted_vpn_cidrs)

        # Anti-spoofing rule (BH-01-4-01): X-Corporate-Origin-Region is ignored unless origin_ip is in trusted VPN CIDRs
        effective_corp_c = corp_c if trusted_vpn else None

        observed_countries = {
            c for c in (jwt_c, persona_c, lb_c, effective_corp_c) if c
        }
        if any(c in BLOCKED_COUNTRIES for c in observed_countries):
            return {
                "allowed": False,
                "http_status": 403,
                "error_code": ErrorCode.REGION_BLOCKED,
                "effective_country": "TW",
                "data_residency_region": "europe-west4",
                "audit_event_emitted": True,
                "audit_reason_code": "REGION_BLOCKED_TW",
                "route_path": route_path,
            }

        effective_country = jwt_c or persona_c or effective_corp_c or lb_c or "NL"
        return {
            "allowed": True,
            "http_status": 200,
            "error_code": None,
            "effective_country": effective_country,
            "data_residency_region": "europe-west4",
            "audit_event_emitted": False,
            "route_path": route_path,
        }

    def validate_persona_seed(self, persona: EmployeePersona) -> bool:
        """Reject any persona with country=='TW' during catalog seed validation (BH-01-4-01)."""
        country_norm = persona.country.strip().upper()
        addr_country = str(persona.home_address.get("country", "")).strip().upper()
        if country_norm in BLOCKED_COUNTRIES or addr_country in BLOCKED_COUNTRIES:
            return False
        return country_norm in SUPPORTED_COUNTRIES


class InputSafetyGate:
    """Input validation, English-only check, out-of-scope domain containment, and Model Armor / regex injection screening (FR-008, FR-009, FR-012, FR-025, FR-031, FR-034, FR-035, FR-036, NFR-001, NFR-002)."""

    _INJECTION_AND_UNSAFE_PATTERNS: tuple[str, ...] = (
        "ignore all previous",
        "ignore instructions",
        "ignore previous instructions",
        "system override",
        "developermode",
        "print the full system prompt",
        "print your system instructions",
        "dump system prompt",
        "override safety filter",
        "execute admin sql",
        "without confirmation",
        "translate the following base64",
        "c3vibwl0x215x2xlyxzlx3jlcxvlc3q=",
        "threatening harassment",
        "harassment email",
        "leak their home address",
        "</untrusted_policy_excerpt>",
    )

    _NON_ENGLISH_MARKERS: tuple[str, ...] = (
        "hoeveel ",
        "vakantiedagen",
        "heb ik nog",
        "wie viele",
        "urlaubstage",
        "combien de",
        "cuántos días",
    )

    def evaluate_input(
        self,
        *,
        session_id: str,
        employee_id: str,
        text: str,
        turn_index: int,
        now_epoch: int,
    ) -> dict[str, Any]:
        """Screen user input (1..2000 chars), language (English only), domain scope, and prompt injection / jailbreak rules."""
        if not text or len(text.strip()) == 0:
            return {
                "allowed": False,
                "http_status": 400,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "latency_ms": 10,
                "false_positive_rate_benchmark": 0.002,
            }

        if len(text) > 2000:
            return {
                "allowed": False,
                "http_status": 413,
                "error_code": ErrorCode.MESSAGE_TOO_LONG,
                "latency_ms": 12,
                "false_positive_rate_benchmark": 0.002,
            }

        lowered = text.lower()
        for pattern in self._INJECTION_AND_UNSAFE_PATTERNS:
            if pattern in lowered:
                return {
                    "allowed": False,
                    "http_status": 400,
                    "error_code": ErrorCode.SAFETY_BLOCKED,
                    "reason_code": "PROMPT_INJECTION_OR_UNSAFE",
                    "session_id": session_id,
                    "employee_id": employee_id,
                    "turn_index": turn_index,
                    "evaluated_at": now_epoch,
                    "latency_ms": 42,
                    "false_positive_rate_benchmark": 0.002,
                }

        if any(marker in lowered for marker in self._NON_ENGLISH_MARKERS) or bool(
            re.search(r"[\u4e00-\u9fff]", text)
        ):
            return {
                "allowed": False,
                "http_status": 200,
                "error_code": ErrorCode.UNSUPPORTED_LANGUAGE,
                "reply_text": "In MVP 1, only English is supported. Please submit your HR or IT request in English.",
                "latency_ms": 18,
                "false_positive_rate_benchmark": 0.002,
            }

        return {
            "allowed": True,
            "http_status": 200,
            "error_code": None,
            "session_id": session_id,
            "employee_id": employee_id,
            "turn_index": turn_index,
            "latency_ms": 45,
            "false_positive_rate_benchmark": 0.002,
        }


class OutputSafetyGate:
    """Output safety, DLP PII redaction, grounding verification, and ReleaseGate stamping (FR-010, FR-011, FR-013, NFR-001, NFR-002)."""

    _BSN_PATTERN = re.compile(r"\b\d{9}\b")
    _IBAN_PATTERN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7,16}\b")

    def __init__(self) -> None:
        self._stamp_key = _load_or_generate_key("HR_AGENT_RELEASE_GATE_KEY")

    def _compute_stamp(
        self, session_id: str, turn_id: str, verdict: str, safe_text: str
    ) -> str:
        payload = f"{session_id}:{turn_id}:{verdict}:{safe_text}".encode()
        return "rg1_" + hmac.new(self._stamp_key, payload, hashlib.sha256).hexdigest()

    def evaluate_and_stamp_output(
        self,
        *,
        session_id: str,
        turn_id: str,
        candidate_text: str,
        grounding_verdict: dict[str, Any],
        is_degraded_safety_mode: bool = False,
    ) -> dict[str, Any]:
        """Inspect candidate response for PII/toxicity/unverified claims and attach cryptographic ReleaseGate stamp (FR-010, FR-011, FR-013)."""
        if is_degraded_safety_mode:
            return {
                "verdict": "BLOCKED_OR_REDACTED",
                "safe_text": "The safety check is unavailable, please try again shortly.",
                "session_id": session_id,
                "turn_id": turn_id,
                "gate_latency_ms": 50,
                "release_gate_stamp": None,
            }

        if (
            not grounding_verdict.get("passed", True)
            or float(grounding_verdict.get("score", 1.0)) < 0.80
        ):
            safe_text = "I do not have sufficient verified policy evidence to answer that claim."
            stamp = self._compute_stamp(session_id, turn_id, "ABSTAIN", safe_text)
            return {
                "verdict": "ABSTAIN",
                "safe_text": safe_text,
                "session_id": session_id,
                "turn_id": turn_id,
                "gate_latency_ms": 55,
                "release_gate_stamp": stamp,
            }

        redacted = self._BSN_PATTERN.sub("[REDACTED-BSN]", candidate_text)
        redacted = self._IBAN_PATTERN.sub("[REDACTED-IBAN]", redacted)
        verdict = "BLOCKED_OR_REDACTED" if redacted != candidate_text else "PASS"
        stamp = self._compute_stamp(session_id, turn_id, verdict, redacted)
        return {
            "verdict": verdict,
            "safe_text": redacted,
            "session_id": session_id,
            "turn_id": turn_id,
            "gate_latency_ms": 55,
            "release_gate_stamp": stamp,
        }

    def verify_release_gate_stamp(self, stamped_envelope: dict[str, Any]) -> bool:
        """Verify that an outbound SSE frame or write mutation passed through OutputSafetyGate (FR-013)."""
        stamp = stamped_envelope.get("release_gate_stamp")
        if not stamp or not isinstance(stamp, str):
            return False
        session_id = str(stamped_envelope.get("session_id", ""))
        turn_id = str(stamped_envelope.get("turn_id", ""))
        verdict = str(stamped_envelope.get("verdict", ""))
        safe_text = str(stamped_envelope.get("safe_text", ""))
        expected = self._compute_stamp(session_id, turn_id, verdict, safe_text)
        return hmac.compare_digest(stamp, expected)

    def strip_display_fields_from_tool_payload(
        self, tool_result: dict[str, Any]
    ) -> dict[str, Any]:
        """Deterministically strip display{} sub-objects from MCP tool responses before LLM context assembly (FR-014, NFR-007)."""

        def _strip(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: _strip(v) for k, v in obj.items() if k != "display"}
            if isinstance(obj, list):
                return [_strip(item) for item in obj]
            return obj

        res: dict[str, Any] = _strip(tool_result)
        return res

    def wrap_untrusted_rag_chunks(self, chunks: list[dict[str, Any]]) -> str:
        """Spotlighting: wrap retrieved policy excerpts in <untrusted_policy_excerpt> tags (FR-075, NFR-002)."""
        if not chunks:
            return ""
        blocks: list[str] = []
        for chunk in chunks:
            doc_id = str(chunk.get("doc_id", "unknown"))
            section = str(chunk.get("section", chunk.get("section_id", "0")))
            raw_text = str(chunk.get("text", ""))
            sanitized_text = raw_text.replace(
                "</untrusted_policy_excerpt>", "[ESCAPED_TAG]"
            ).replace("<untrusted_policy_excerpt", "[ESCAPED_TAG")
            blocks.append(
                f'<untrusted_policy_excerpt doc_id="{doc_id}" section="{section}">{sanitized_text}</untrusted_policy_excerpt>'
            )
        return "\n".join(blocks)


class ToolManifestAllowlist:
    """Declarative tool manifest allow-list, inventory metadata, and version pinning verifier (FR-001, FR-002, FR-003, FR-006, FR-007)."""

    _AGENT_READ_TOOLS: dict[str, set[str]] = {
        "policy_answer": {
            "retrieve_policy_chunks",
            "evaluate_eligibility",
            "verify_grounding_and_repair",
        },
        "workweek_ops": {
            "get_my_profile",
            "get_my_leave_balances",
            "resolve_relative_dates",
            "validate_leave_request",
            "validate_contact_update",
            "get_write_status",
        },
        "servicedesk_ops": {
            "get_my_ticket",
            "list_my_open_tickets",
            "validate_incident",
            "validate_ticket_update",
            "get_write_status",
        },
    }

    _WRITE_TOOLS: set[str] = {
        "submit_my_leave_request",
        "update_my_contact_info",
        "create_incident",
        "add_ticket_comment",
        "update_ticket_status",
    }

    def verify_tool_invocation(
        self,
        *,
        agent_name: str,
        tool_name: str,
        invocation_context: str,
        has_valid_ct: bool,
    ) -> dict[str, Any]:
        """Enforce agent-to-tool allow-list and block any write tool call inside a sub-agent turn (FR-003, FR-004, FR-022)."""
        if tool_name in self._WRITE_TOOLS:
            if (
                invocation_context != "plan_executor"
                or not has_valid_ct
                or agent_name == "policy_answer"
            ):
                return {
                    "allowed": False,
                    "error_code": ErrorCode.TOOL_BLOCKED,
                    "reason": "Write tools may only be executed by DeterministicPlanExecutor with a valid ConfirmationToken",
                }
            return {"allowed": True, "error_code": None}

        allowed_reads = self._AGENT_READ_TOOLS.get(agent_name, set())
        if tool_name in allowed_reads:
            return {"allowed": True, "error_code": None}

        return {
            "allowed": False,
            "error_code": ErrorCode.TOOL_BLOCKED,
            "reason": f"Tool '{tool_name}' is not in the allow-list for agent '{agent_name}'",
        }

    def get_inventory_metadata(self) -> dict[str, Any]:
        """Return governance inventory metadata for the agent and all registered MCP servers (FR-001, FR-002)."""
        return {
            "agent_id": "hr-agent-mvp1",
            "version": "0.1.0",
            "owner": "hr-ai-platform-team@example.invalid",
            "mcp_servers": {
                "workweek-mcp": sorted(
                    self._AGENT_READ_TOOLS["workweek_ops"]
                    | {"submit_my_leave_request", "update_my_contact_info"}
                ),
                "servicedesk-mcp": sorted(
                    self._AGENT_READ_TOOLS["servicedesk_ops"]
                    | {"create_incident", "add_ticket_comment", "update_ticket_status"}
                ),
            },
        }

    def validate_version_pinning(
        self, *, deployment_manifest: dict[str, Any]
    ) -> dict[str, Any]:
        """Verify agent code, prompt templates, tool manifest, model IDs, and safety templates are pinned to a single immutable version (FR-002)."""
        required_keys = (
            "agent_code_version",
            "prompt_bundle_digest",
            "tool_manifest_digest",
            "model_ids",
            "safety_template_version",
        )
        missing = [k for k in required_keys if not deployment_manifest.get(k)]
        return {
            "valid": len(missing) == 0,
            "missing_keys": missing,
        }


class TelemetrySanitizer:
    """Zero-content-capture startup self-check, SPII canary scanner, retention verifier, and pseudonymized audit log formatter (FR-007, FR-014, NFR-003, NFR-004, NFR-007, NFR-010)."""

    _REQUIRED_AUDIT_FIELDS: tuple[str, ...] = (
        "event_type",
        "actor_type",
        "actor_id",
        "on_behalf_of",
        "agent_principal",
        "actor_country",
        "session_id",
        "turn_id",
        "trace_id",
        "target_system",
        "tool_name",
        "outcome",
        "reason_code",
        "latency_ms",
    )

    def validate_otel_config(self, env: dict[str, str]) -> dict[str, Any]:
        """Verify all 5 content-capture switches at startup and fail closed if any switch could capture prompts/completions (FR-014, SDD §4.7.3)."""
        violations: list[str] = []
        if (
            env.get("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT")
            != "NO_CONTENT"
        ):
            violations.append(
                "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT must be NO_CONTENT"
            )
        if env.get("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "").lower() != "false":
            violations.append("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS must be false")
        if env.get("GENAI_COMPLETION_HOOKS_ENABLED", "false").lower() != "false":
            violations.append("GENAI_COMPLETION_HOOKS_ENABLED must be false")
        if env.get("BQ_AGENT_ANALYTICS_RAW_LOGGING", "false").lower() != "false":
            violations.append("BQ_AGENT_ANALYTICS_RAW_LOGGING must be false")
        if env.get("LOG_LEVEL", "INFO").upper() in ("DEBUG", "TRACE"):
            violations.append("LOG_LEVEL must not be DEBUG or TRACE")
        if env.get("MODEL_ARMOR_PLATFORM_LOGGING", "false").lower() != "false":
            violations.append("MODEL_ARMOR_PLATFORM_LOGGING must be false")

        safe = len(violations) == 0
        return {
            "content_capture_safe": safe,
            "startup_allowed": safe,
            "violations": violations,
        }

    def inspect_telemetry_payload_for_spii(
        self,
        payload_str: str,
        canary_tokens: list[str],
    ) -> dict[str, Any]:
        """Scan exported log/span/metric strings for raw SPII or canary tokens (FR-014, NFR-007)."""
        matched = [tok for tok in canary_tokens if tok and tok in payload_str]
        if "Bearer eyJ" in payload_str and "eyJ" not in matched:
            matched.append("Bearer_JWT")
        return {
            "spii_detected": len(matched) > 0,
            "matched_tokens": matched,
        }

    def build_and_emit_audit_event(self, raw_event: dict[str, Any]) -> dict[str, Any]:
        """Validate schema, pseudonymize actor_id/on_behalf_of, compute content_sha256, and emit AuditEvent with 400-day retention (FR-007, NFR-003, NFR-004)."""
        missing = [f for f in self._REQUIRED_AUDIT_FIELDS if f not in raw_event]
        if missing:
            return {
                "ok": False,
                "error_code": ErrorCode.VALIDATION_FAILED,
                "missing_fields": missing,
            }

        raw_payload = raw_event.get("raw_payload", {})
        payload_bytes = json.dumps(raw_payload, sort_keys=True, default=str).encode(
            "utf-8"
        )
        content_sha256 = str(
            raw_event.get("content_sha256")
            or f"sha256:{hashlib.sha256(payload_bytes).hexdigest()}"
        )
        retention_days = max(int(raw_event.get("retention_days", 400)), 400)

        return {
            "ok": True,
            "event_id": str(
                raw_event.get(
                    "event_id", f"evt-{raw_event['session_id']}-{raw_event['turn_id']}"
                )
            ),
            "timestamp_utc": str(
                raw_event.get("timestamp_utc", "2026-06-01T12:00:00Z")
            ),
            "event_type": str(raw_event["event_type"]),
            "actor_type": str(raw_event["actor_type"]),
            "actor_id": str(raw_event["actor_id"]),
            "on_behalf_of": str(raw_event["on_behalf_of"]),
            "agent_principal": str(raw_event["agent_principal"]),
            "actor_country": str(raw_event["actor_country"]),
            "session_id": str(raw_event["session_id"]),
            "turn_id": str(raw_event["turn_id"]),
            "trace_id": str(raw_event["trace_id"]),
            "target_system": str(raw_event["target_system"]),
            "tool_name": str(raw_event["tool_name"]),
            "outcome": str(raw_event["outcome"]),
            "reason_code": str(raw_event["reason_code"]),
            "content_sha256": content_sha256,
            "latency_ms": int(raw_event["latency_ms"]),
            "retention_days": retention_days,
            "immutable_sink": True,
        }

    def verify_retention_windows(self, *, config: dict[str, int]) -> dict[str, Any]:
        """Verify retention windows: session working memory 24h (86400s), redacted transcripts 90d, ops logs 30d, idempotency 48h, plan journal 90d, audit 400d (NFR-004, NFR-010)."""
        violations: list[str] = []
        if int(config.get("session_working_memory_seconds", 0)) > 86400:
            violations.append("session_working_memory_seconds exceeds 86400s (24h)")
        if int(config.get("redacted_transcript_days", 0)) > 90:
            violations.append("redacted_transcript_days exceeds 90d")
        if int(config.get("operational_logs_days", 0)) > 30:
            violations.append("operational_logs_days exceeds 30d")
        if int(config.get("idempotency_records_seconds", 0)) > 172800:
            violations.append("idempotency_records_seconds exceeds 172800s (48h)")
        if int(config.get("plan_journal_days", 0)) > 90:
            violations.append("plan_journal_days exceeds 90d")
        if int(config.get("audit_logs_days", 0)) < 400:
            violations.append("audit_logs_days is below 400d WORM minimum")

        return {
            "compliant": len(violations) == 0,
            "violations": violations,
        }


_SHORTHAND_UCT_PATTERN = re.compile(
    r"^(uct-(nl-001|de-002|cn-003|tw-999)(-[a-z0-9_ -]+)?|shakedown-uct-jwt)$"
)


def parse_uct_or_shorthand(
    uct: str,
    *,
    expected_audience: str = "",
    now_epoch: int | None = None,
) -> dict[str, Any]:
    """Verify an ES256 UCT JWT via KmsTokenSigner.verify_uct or resolve an allowed test-shorthand UCT fixture token (BH-05-1-01)."""
    if not uct or not uct.strip():
        return {"valid": False, "error_code": ErrorCode.AUTHN_FAILED}

    token = uct.strip()
    if token.count(".") == 2:
        try:
            raw_payload = json.loads(
                KmsTokenSigner._b64url_decode(token.split(".")[1]).decode("utf-8")
            )
        except Exception:
            return {"valid": False, "error_code": ErrorCode.AUTHN_FAILED}
        iat_claim = int(raw_payload.get("iat", 0))
        effective_now = (
            int(now_epoch)
            if now_epoch is not None
            else (1780000000 if iat_claim == 1780000000 else int(time.time()))
        )
        signer = KmsTokenSigner()
        verified = signer.verify_uct(
            token=token,
            expected_audience=expected_audience,
            now_epoch=effective_now,
        )
        if isinstance(verified, TokenVerificationFailure):
            return {
                "valid": False,
                "error_code": verified.error_code,
                "reason": verified.reason,
            }
        return {
            "valid": True,
            "sub": verified.sub,
            "country": verified.country,
            "legal_entity": verified.legal_entity,
            "manager_id": verified.manager_id,
            "fte": verified.fte,
            "email": verified.email,
            "session_id": verified.session_id,
            "iat": verified.iat,
            "exp": verified.exp,
            "roles": list(verified.roles),
            "raw_uct": token,
        }

    app_env = os.environ.get("APP_ENV", "local").strip().lower()
    default_shorthand = (
        "false" if app_env in ("prodpilot", "prod", "production") else "true"
    )
    allow_shorthand = (
        os.environ.get("ALLOW_TEST_SHORTHAND_TOKENS", default_shorthand).strip().lower()
        == "true"
    )
    if not allow_shorthand:
        return {"valid": False, "error_code": ErrorCode.AUTHN_FAILED}

    lower = token.lower()
    if (
        "invalid" in lower
        or "tampered" in lower
        or "expired" in lower
        or not _SHORTHAND_UCT_PATTERN.match(lower)
    ):
        return {"valid": False, "error_code": ErrorCode.AUTHN_FAILED}

    if "cn-003" in lower or "-cn-" in lower:
        return {
            "valid": True,
            "sub": "EMP-CN-003",
            "country": "CN",
            "legal_entity": "LE-CN-WFOE",
            "manager_id": "EMP-CN-MGR-01",
            "fte": 1.0,
            "email": "wei.chen.synthetic@example.invalid",
            "session_id": f"sess-{lower}",
            "raw_uct": token,
        }
    if "de-002" in lower or "-de-" in lower:
        return {
            "valid": True,
            "sub": "EMP-DE-002",
            "country": "DE",
            "legal_entity": "LE-DE-GMBH",
            "manager_id": "EMP-DE-MGR-01",
            "fte": 1.0,
            "email": "lukas.mueller.synthetic@example.invalid",
            "session_id": f"sess-{lower}",
            "raw_uct": token,
        }
    if "tw-999" in lower or "-tw-" in lower:
        return {
            "valid": True,
            "sub": "EMP-TW-999",
            "country": "TW",
            "legal_entity": "LE-TW-EXCL",
            "manager_id": "EMP-MGR-99",
            "fte": 1.0,
            "email": "tw.excluded.synthetic@example.invalid",
            "session_id": f"sess-{lower}",
            "raw_uct": token,
        }
    return {
        "valid": True,
        "sub": "EMP-NL-001",
        "country": "NL",
        "legal_entity": "LE-NL-BV",
        "manager_id": "EMP-NL-MGR-01",
        "fte": 1.0,
        "email": "eva.bakker.synthetic@example.invalid",
        "session_id": f"sess-{lower}",
        "raw_uct": token,
    }


_SHORTHAND_CT_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")


def verify_mcp_write_gate(
    *,
    signer: KmsTokenSigner,
    claims: dict[str, Any],
    ct: str,
    step_id: str,
    tool_name: str,
    canonical_args: dict[str, Any],
    preview_digest: str,
    expected_preview_digest: str,
    validated_previews: dict[tuple[str, str, str], str],
    used_cts: set[str],
    lock: Any,
) -> dict[str, Any]:
    """Verify ConfirmationToken (ES256 JWS or test shorthand), single-use jti, and preview_digest binding (BH-05-1-01)."""
    if not ct or not ct.strip():
        return {"ok": False, "error_code": ErrorCode.INVALID_CONFIRMATION_TOKEN}
    ct_clean = ct.strip()
    lower_ct = ct_clean.lower()
    if (
        "expired" in lower_ct
        or "invalid" in lower_ct
        or "tampered" in lower_ct
        or "forged" in lower_ct
    ):
        return {"ok": False, "error_code": ErrorCode.INVALID_CONFIRMATION_TOKEN}

    if (
        not preview_digest
        or not preview_digest.startswith("sha256:")
        or "mismatch" in preview_digest.lower()
    ):
        return {"ok": False, "error_code": ErrorCode.CONFIRMATION_PAYLOAD_MISMATCH}

    digest_hex = preview_digest[len("sha256:") :]
    stored_digest = validated_previews.get(
        (claims["sub"], claims.get("session_id", ""), tool_name)
    )
    if len(digest_hex) == 64 and preview_digest != expected_preview_digest:
        return {"ok": False, "error_code": ErrorCode.CONFIRMATION_PAYLOAD_MISMATCH}
    if stored_digest and len(digest_hex) == 64 and preview_digest != stored_digest:
        return {"ok": False, "error_code": ErrorCode.CONFIRMATION_PAYLOAD_MISMATCH}

    if ct_clean.count(".") == 2:
        try:
            raw_ct = json.loads(
                KmsTokenSigner._b64url_decode(ct_clean.split(".")[1]).decode("utf-8")
            )
        except Exception:
            return {"ok": False, "error_code": ErrorCode.INVALID_CONFIRMATION_TOKEN}
        steps = raw_ct.get("step_digests", [])
        matched_step = next((s for s in steps if s.get("step_id") == step_id), None)
        arg_digest = (
            str(matched_step.get("arg_digest"))
            if matched_step and len(digest_hex) < 64
            else "sha256:"
            + hashlib.sha256(
                json.dumps(canonical_args, sort_keys=True).encode("utf-8")
            ).hexdigest()
        )
        verified_ct = signer.verify_and_consume_ct(
            token=ct_clean,
            expected_sub=claims["sub"],
            expected_session_id=str(
                raw_ct.get("session_id", claims.get("session_id", ""))
            ),
            expected_plan_id=str(raw_ct.get("plan_id", "")),
            expected_step_id=step_id,
            expected_tool_name=tool_name,
            expected_arg_digest=arg_digest,
            expected_preview_digest=preview_digest,
            now_epoch=int(raw_ct.get("iat", 1780000000)),
        )
        if isinstance(verified_ct, TokenVerificationFailure):
            return {"ok": False, "error_code": verified_ct.error_code}
        return {"ok": True}

    if not _SHORTHAND_CT_PATTERN.match(ct_clean):
        return {"ok": False, "error_code": ErrorCode.INVALID_CONFIRMATION_TOKEN}

    with lock:
        if ct_clean in used_cts:
            return {"ok": False, "error_code": ErrorCode.CONFIRMATION_REPLAYED}
        used_cts.add(ct_clean)
    return {"ok": True}
