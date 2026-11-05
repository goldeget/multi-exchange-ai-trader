"""Operator policy drafts: no process or venue activation side effects."""

import asyncio
import hashlib
import json
import sqlite3
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from multi_exchange_trader.trading_product import InstrumentKey

from ..public_dashboard_origin import PublicDashboardOrigin, dashboard_headers_allowed
from . import builtin_sma_atr_v1 as builtin
from .modes import StrategyReference
from .operator_auth_v1 import (
    VerifiedOperatorPrincipalV1,
    verified_operator_principal_from_state_v1,
)
from .runtime_policy import RuntimePolicyBook, parse_json
from .strategy_publication_v1 import StrategyArtifactBytesV1, StrategyPublicationOwnerV1

AUTOMATIC_BODY_LIMIT = 64 * 1024
CAPABILITY_HEADER = {
    "X-Automatic-Policy-Sections-Version": "1",
    "X-Automatic-Strategy-Publication-Version": "1",
}


def install(application: FastAPI, *, public_origin: PublicDashboardOrigin | None = None) -> None:
    router = APIRouter(prefix="/api/v1/automatic")

    def book(request: Request) -> RuntimePolicyBook:
        value = getattr(request.app.state, "runtime_policy", None)
        if not isinstance(value, RuntimePolicyBook):
            raise OSError("policy storage unavailable")
        return value

    @router.get("/settings")
    async def read(request: Request) -> Any:
        try:
            return JSONResponse(content=await asyncio.to_thread(book(request).read), headers=CAPABILITY_HEADER)
        except (ValueError, TypeError, OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "automatic_storage_unavailable"})

    @router.put("/settings")
    async def save(request: Request) -> Any:
        return await write(request, migration=False)

    @router.get("/strategies")
    async def strategies(request: Request) -> Any:
        """Read current publication without approving a strategy or starting trading."""
        try:
            publication = await asyncio.to_thread(book(request).publication_snapshot_v1)
            return JSONResponse(content=publication, headers=CAPABILITY_HEADER)
        except (ValueError, TypeError, OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "automatic_storage_unavailable"})

    def require_operator_principal(
        request: Request,
    ) -> VerifiedOperatorPrincipalV1 | None:
        hosts = request.headers.getlist("host")
        origins = request.headers.getlist("origin")
        fetch_site = request.headers.get("sec-fetch-site")
        client = request.client
        if (client is None or client.host != "127.0.0.1" or public_origin is None
                or not dashboard_headers_allowed(hosts, origins, public_origin=public_origin)
                or origins != [public_origin.origin]
                or request.headers.get("authorization") is not None
                or fetch_site not in (None, "same-origin")):
            return None
        # Only trusted app-auth middleware may set this ASGI request-state value.
        # No request header is interpreted as an operator identity.
        return verified_operator_principal_from_state_v1(request)

    async def publication_body(request: Request, *, publishing: bool) -> dict[str, Any]:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise TypeError("json required")
        chunks = bytearray()
        async for chunk in request.stream():
            if len(chunks) + len(chunk) > AUTOMATIC_BODY_LIMIT:
                raise OverflowError("body limit")
            chunks.extend(chunk)
        value = parse_json(bytes(chunks))
        expected = {"schema_version", "expected_policy_revision", "expected_generation", "symbol", "configuration"}
        if publishing:
            expected.add("confirmation_fingerprint")
        if (type(value) is not dict or set(value) != expected
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or type(value["expected_policy_revision"]) is not int
                or type(value["expected_generation"]) is not int
                or type(value["symbol"]) is not str
                or type(value["configuration"]) is not dict):
            raise ValueError("publication fields")
        if publishing and (type(value["confirmation_fingerprint"]) is not str
                or len(value["confirmation_fingerprint"]) != 64
                or any(character not in "0123456789abcdef" for character in value["confirmation_fingerprint"])):
            raise ValueError("publication confirmation")
        return value

    def preview_publication(
        request: Request, value: dict[str, Any], principal: VerifiedOperatorPrincipalV1,
    ) -> tuple[dict[str, Any], StrategyArtifactBytesV1]:
        current = book(request).read()
        publication = book(request).publication_snapshot_v1()
        expected_generation = publication["generation"] if publication is not None else 0
        if (current["schema_version"] != 2
                or current["revision"] != value["expected_policy_revision"]
                or expected_generation != value["expected_generation"]):
            raise ValueError("publication revision conflict")
        reference = StrategyReference("builtin-sma-atr", "r1")
        if publication is not None and any(
                entry["reference"] == {"strategy_id": reference.strategy_id, "revision": reference.revision}
                for entry in publication["catalog"]["entries"]):
            raise ValueError("built-in strategy reference already published")
        instrument = InstrumentKey(venue="BYBIT", product="LINEAR", symbol=value["symbol"])
        configuration = value["configuration"]
        configuration_bytes = json.dumps(configuration, sort_keys=True, ensure_ascii=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")
        builtin.parse_configuration_v1(configuration_bytes)
        implementation = builtin.implementation_bytes_v1()
        implementation_hash = hashlib.sha256(implementation).hexdigest()
        configuration_hash = hashlib.sha256(configuration_bytes).hexdigest()
        preview = {
            "schema_version": 1,
            "expected_policy_revision": current["revision"],
            "expected_generation": expected_generation,
            "reference": {"strategy_id": reference.strategy_id, "revision": reference.revision},
            "instrument": {"venue": instrument.venue, "product": instrument.product, "symbol": instrument.symbol},
            "configuration": configuration,
            "implementation_sha256": implementation_hash,
            "configuration_sha256": configuration_hash,
            "approval_method": "APP_AUTHENTICATED_PRINCIPAL_SELF_APPROVAL_V1",
            "authenticated_operator": principal.confirmation_payload(),
        }
        fingerprint_payload = json.dumps(preview, sort_keys=True, ensure_ascii=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")
        fingerprint = hashlib.sha256(b"BuiltinStrategySelfApprovalPreviewV1\0" + fingerprint_payload).hexdigest()
        preview["confirmation_fingerprint"] = fingerprint
        approval = {
            "schema_version": 1,
            "action": "APPROVE_STRATEGY_V1",
            "reference": preview["reference"],
            "implementation_sha256": implementation_hash,
            "config_sha256": configuration_hash,
            "config_schema_version": 1,
            "engine_contract_version": builtin.ENGINE_CONTRACT_V1,
            "permitted_instruments": [preview["instrument"]],
            "approval_reference": "operator-self-approval:" + fingerprint,
            "approval_source_reference": principal.approval_source_reference,
            "approval_environments": ["DEMO"],
        }
        artifact = StrategyArtifactBytesV1(implementation, configuration_bytes,
            json.dumps(approval, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
        return preview, artifact

    @router.post("/strategies/preview")
    async def preview_strategy(request: Request) -> Any:
        try:
            principal = require_operator_principal(request)
            if type(principal) is not VerifiedOperatorPrincipalV1:
                raise PermissionError("operator required")
            value = await publication_body(request, publishing=False)
            preview, _artifact = await asyncio.to_thread(preview_publication, request, value, principal)
            return JSONResponse(content=preview, headers=CAPABILITY_HEADER)
        except PermissionError:
            return JSONResponse(status_code=403, content={"reason": "operator_confirmation_required"})
        except OverflowError:
            return JSONResponse(status_code=413, content={"reason": "automatic_body_limit"})
        except TypeError:
            return JSONResponse(status_code=415, content={"reason": "paper_json_required"})
        except (ValueError, KeyError, json.JSONDecodeError):
            return JSONResponse(status_code=409, content={"reason": "strategy_publication_conflict_or_invalid"})
        except (OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "automatic_storage_unavailable"})

    @router.post("/strategies/publish")
    async def publish_strategy(request: Request) -> Any:
        try:
            principal = require_operator_principal(request)
            if type(principal) is not VerifiedOperatorPrincipalV1:
                raise PermissionError("operator required")
            value = await publication_body(request, publishing=True)
            preview, artifact = await asyncio.to_thread(preview_publication, request, value, principal)
            if value["confirmation_fingerprint"] != preview["confirmation_fingerprint"]:
                raise ValueError("publication confirmation changed")
            result = await asyncio.to_thread(StrategyPublicationOwnerV1(book(request)).publish,
                (artifact,), expected_policy_revision=value["expected_policy_revision"],
                expected_generation=value["expected_generation"])
            publication = await asyncio.to_thread(book(request).publication_snapshot_v1)
            if publication is None:
                raise ValueError("publication result missing")
            return JSONResponse(content={"schema_version": 1, "generation": result.generation,
                "policy_revision": result.policy_revision, "catalog": publication["catalog"],
                "reference": preview["reference"], "implementation_sha256": preview["implementation_sha256"],
                "configuration_sha256": preview["configuration_sha256"],
                "approval_method": preview["approval_method"]}, headers=CAPABILITY_HEADER)
        except PermissionError:
            return JSONResponse(status_code=403, content={"reason": "operator_confirmation_required"})
        except OverflowError:
            return JSONResponse(status_code=413, content={"reason": "automatic_body_limit"})
        except TypeError:
            return JSONResponse(status_code=415, content={"reason": "paper_json_required"})
        except (ValueError, KeyError, json.JSONDecodeError):
            return JSONResponse(status_code=409, content={"reason": "strategy_publication_conflict_or_invalid"})
        except (OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "automatic_storage_unavailable"})

    @router.post("/settings/migrate")
    async def migrate(request: Request) -> Any:
        return await write(request, migration=True)

    async def write(request: Request, *, migration: bool) -> Any:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return JSONResponse(status_code=415, content={"reason": "paper_json_required"})
        try:
            # Bound bytes before JSON parsing, including requests without Content-Length.
            chunks = bytearray()
            async for chunk in request.stream():
                if len(chunks) + len(chunk) > AUTOMATIC_BODY_LIMIT:
                    return JSONResponse(status_code=413, content={"reason": "automatic_body_limit"})
                chunks.extend(chunk)
            value = parse_json(bytes(chunks))
            expected = {"schema_version", "revision"} if migration else {
                "schema_version", "revision", "policy", "expected_strategy_generation"}
            if not migration and type(value) is dict and "policy_sections_version" in value:
                expected.add("policy_sections_version")
                if type(value["policy_sections_version"]) is not int or value["policy_sections_version"] != 1:
                    raise ValueError("settings capability")
            if (type(value) is not dict or set(value) != expected
                    or type(value["schema_version"]) is not int
                    or value["schema_version"] != (1 if migration else 2)):
                raise ValueError("settings fields")
            if migration:
                result = await asyncio.to_thread(book(request).migrate_v1, value["revision"])
            else:
                result = await asyncio.to_thread(book(request).save_v2, value["revision"], value["policy"],
                    expected_strategy_generation=value["expected_strategy_generation"],
                    policy_sections_version=value.get("policy_sections_version"))
            return JSONResponse(content=result, headers=CAPABILITY_HEADER)
        except (ValueError, TypeError):
            return JSONResponse(status_code=409, content={"reason": "automatic_settings_conflict_or_invalid"})
        except (OSError, sqlite3.Error):
            return JSONResponse(status_code=503, content={"reason": "automatic_storage_unavailable"})

    application.include_router(router)
