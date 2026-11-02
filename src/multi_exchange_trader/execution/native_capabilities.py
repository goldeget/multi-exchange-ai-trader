"""Audited parameter forwarding only; these facts never authorize execution.

The pinned Python adapter boundary forwards attached trigger sources. Explicit
standalone sources are not forwarded, even when a venue default might coincide.
Neither result proves HTTP serialization, venue acceptance, or child ownership.
"""

from dataclasses import dataclass, field
from typing import Literal

CommandKind = Literal["ATTACHED_ENTRY", "STANDALONE_STOP", "STANDALONE_TAKE_PROFIT"]
TriggerSource = Literal["MarkPrice", "LastPrice", "IndexPrice"]


@dataclass(frozen=True)
class NativeCapability:
    native_version: str
    command_kind: CommandKind
    trigger_source: TriggerSource
    source_commit: str = field(init=False, default="27a8e54e7ac3c57d6cbf8891f0283dfbaee97317")
    environment: Literal["DEMO"] = field(init=False, default="DEMO")
    product: Literal["LINEAR"] = field(init=False, default="LINEAR")
    parameter_forwarding: Literal["SUPPORTED", "UNSUPPORTED"] = field(init=False)
    reason_code: Literal[
        "ATTACHED_TRIGGER_SOURCE_FORWARDED", "EXPLICIT_TRIGGER_SOURCE_NOT_FORWARDED",
    ] = field(init=False)
    production_supported: Literal[False] = field(init=False, default=False)

    def __post_init__(self) -> None:
        if (type(self.native_version) is not str or self.native_version != "1.231.0"
                or type(self.command_kind) is not str or self.command_kind not in (
                    "ATTACHED_ENTRY", "STANDALONE_STOP", "STANDALONE_TAKE_PROFIT")
                or type(self.trigger_source) is not str or self.trigger_source not in (
                    "MarkPrice", "LastPrice", "IndexPrice")):
            raise ValueError("NATIVE_CAPABILITY_INPUT_UNSUPPORTED")
        attached = self.command_kind == "ATTACHED_ENTRY"
        object.__setattr__(self, "parameter_forwarding", "SUPPORTED" if attached else "UNSUPPORTED")
        object.__setattr__(self, "reason_code", "ATTACHED_TRIGGER_SOURCE_FORWARDED" if attached
                           else "EXPLICIT_TRIGGER_SOURCE_NOT_FORWARDED")


def describe_native_capability(*, native_version: str, command_kind: CommandKind,
                               trigger_source: TriggerSource) -> NativeCapability:
    return NativeCapability(native_version, command_kind, trigger_source)
