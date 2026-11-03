"""Private OpenRouter composition; construction never quotes, enables or dispatches."""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..credential_resolution import CredentialResolver
from ..credentials import CredentialStoreError
from .budget import BudgetBook
from .dispatch import AIDispatcher
from .generation import OpenRouterGenerationClient
from .openrouter_metadata import OpenRouterMetadataClient
from .provider_config import load_ai_provider_config


@dataclass(frozen=True, slots=True)
class OpenRouterClients:
    """Private clients only, never an API response or generic serialized model."""

    metadata: OpenRouterMetadataClient = field(repr=False)
    generation: OpenRouterGenerationClient = field(repr=False)
    dispatcher: AIDispatcher = field(repr=False)


def create_openrouter_clients(*, resolver: CredentialResolver, budget: BudgetBook,
                              dispatch_path: Path,
                              transport: httpx.BaseTransport | None = None,
                              reasoning_enabled: bool | None = None) -> OpenRouterClients:
    if reasoning_enabled is not None and type(reasoning_enabled) is not bool:
        raise ValueError("Invalid generation configuration") from None
    values = resolver.ai_values()
    config = load_ai_provider_config(values)
    if (config.provider != "OPENROUTER" or not config.registered or
            config.status != "REGISTERED_UNVERIFIED" or config.model_id is None):
        raise CredentialStoreError("INVALID_VALUES")
    # The same fixed provider/key/model selection is used by both existing clients.
    key = values["OPENROUTER_API_KEY"].strip()
    metadata = OpenRouterMetadataClient(key, config.model_id, transport=transport)
    generation = OpenRouterGenerationClient(key, config.model_id, transport=transport,
                                            reasoning_enabled=reasoning_enabled)
    try:
        dispatcher = AIDispatcher(dispatch_path, budget, generation)
    except (OSError, ValueError, sqlite3.Error):
        raise CredentialStoreError("PERSISTENCE_FAILED") from None
    return OpenRouterClients(metadata, generation, dispatcher)
