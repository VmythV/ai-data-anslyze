from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from data_analyze.config import Settings
from data_analyze.datasources.catalog import SchemaCatalog
from data_analyze.datasources.federation import Federation
from data_analyze.datasources.registry import DriverRegistry
from data_analyze.relations.infer import RelationInference
from data_analyze.runs.housekeeping import Housekeeper
from data_analyze.runs.manager import RunManager
from data_analyze.security import SecretBox
from data_analyze.store.db import Database


@dataclass
class Services:
    settings: Settings
    db: Database
    box: SecretBox
    drivers: DriverRegistry
    catalog: SchemaCatalog
    federation: Federation
    inference: RelationInference
    runs: RunManager
    housekeeper: Housekeeper


def services(request: Request) -> Services:
    return request.app.state.services


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.services.db.sessions() as session:
        yield session
