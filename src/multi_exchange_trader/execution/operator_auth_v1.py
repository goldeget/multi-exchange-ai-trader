"""Server-authenticated operator identity used for approval provenance."""

import hashlib
import re
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_NGINX_BASIC_AUTH_METHOD = "nginx-basic-auth-v1"
_NGINX_BASIC_AUTH_EVIDENCE_REFERENCE = "nginx-basic-auth-v1"
_NGINX_SOCKET_PATH = "/run/trader-api.sock"


@dataclass(frozen=True)
class VerifiedOperatorPrincipalV1:
    """Identity and authentication evidence issued by trusted app auth middleware."""

    principal_id: str
    evidence_reference: str
    evidence_sha256: str
    authentication_method: str

    def __post_init__(self) -> None:
        if (type(self.principal_id) is not str or len(self.principal_id) > 32
                or _IDENTIFIER.fullmatch(self.principal_id) is None
                or type(self.evidence_reference) is not str or len(self.evidence_reference) > 48
                or _IDENTIFIER.fullmatch(self.evidence_reference) is None
                or type(self.evidence_sha256) is not str or _SHA256.fullmatch(self.evidence_sha256) is None
                or type(self.authentication_method) is not str or len(self.authentication_method) > 32
                or _IDENTIFIER.fullmatch(self.authentication_method) is None):
            raise ValueError("VERIFIED_OPERATOR_PRINCIPAL_INVALID")

    @property
    def approval_source_reference(self) -> str:
        return f"app-auth-v1/{self.principal_id}/{self.evidence_reference}"

    def confirmation_payload(self) -> dict[str, str]:
        return {
            "principal_id": self.principal_id,
            "evidence_reference": self.evidence_reference,
            "evidence_sha256": self.evidence_sha256,
            "authentication_method": self.authentication_method,
        }


def verified_operator_principal_from_state_v1(
    request: Request,
) -> VerifiedOperatorPrincipalV1 | None:
    """Read identity set by trusted authentication middleware, never by request headers."""
    value = getattr(request.state, "verified_operator_principal_v1", None)
    return value if type(value) is VerifiedOperatorPrincipalV1 else None


def _verified_nginx_basic_auth_principal_v1(
    request: Request,
) -> VerifiedOperatorPrincipalV1 | None:
    """Convert the overwritten Nginx assertion received through its protected UDS."""
    operators = request.headers.getlist("x-trader-operator")
    if (request.client is not None
            or request.scope.get("server") != (_NGINX_SOCKET_PATH, None)
            or len(operators) != 1
            or request.headers.getlist("authorization")):
        return None
    principal_id = operators[0]
    try:
        evidence = (_NGINX_BASIC_AUTH_METHOD + "\0" + principal_id).encode("ascii")
        return VerifiedOperatorPrincipalV1(
            principal_id=principal_id,
            evidence_reference=_NGINX_BASIC_AUTH_EVIDENCE_REFERENCE,
            evidence_sha256=hashlib.sha256(evidence).hexdigest(),
            authentication_method=_NGINX_BASIC_AUTH_METHOD,
        )
    except (UnicodeEncodeError, ValueError):
        return None


def install_nginx_basic_auth_principal_v1(application: FastAPI) -> None:
    """Install the receiver for the configured Nginx auth assertion.

    The public Nginx template overwrites ``X-Trader-Operator`` with ``$remote_user``
    only after ``auth_basic`` succeeds, and clears Basic credentials before proxying.
    systemd exposes the API only through the permission-restricted Unix socket. Missing,
    duplicated, malformed, TCP, wrong-socket, or unstripped credential assertions remain
    unauthenticated.
    """

    @application.middleware("http")
    async def set_verified_operator_principal(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Replace any pre-populated ASGI state; callers cannot inject this trusted value.
        request.state.verified_operator_principal_v1 = _verified_nginx_basic_auth_principal_v1(
            request
        )
        return await call_next(request)
