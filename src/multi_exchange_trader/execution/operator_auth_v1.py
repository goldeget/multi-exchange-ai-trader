"""Server-authenticated operator identity used for approval provenance."""

import re
from dataclasses import dataclass

from fastapi import Request

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


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
