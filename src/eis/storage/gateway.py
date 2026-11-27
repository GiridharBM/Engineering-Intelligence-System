# SPDX-License-Identifier: Apache-2.0

"""Composition root for the durable SQLite model gateway."""

from __future__ import annotations

import httpx

from eis.adapters.models.registry import ProviderRegistry
from eis.config import ConfigurationError, Settings
from eis.core.consent import ConsentGate
from eis.core.model_gateway import ModelGateway
from eis.storage.audit import SqliteAuditSink
from eis.storage.consents import ConsentStore
from eis.storage.db import default_database


def build_sqlite_gateway(
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> ModelGateway:
    """Build the durable gateway; no provider means no gateway."""
    config = settings.provider_config
    if config is None:
        raise ConfigurationError("no external model provider is configured")
    database = default_database(settings)
    consent_store = ConsentStore(database)
    if settings.consent is not None:
        consent_store.ensure(settings.consent)
    provider = ProviderRegistry().create(config, client)
    return ModelGateway(
        provider,
        ConsentGate(lookup=consent_store),
        SqliteAuditSink(database),
    )
