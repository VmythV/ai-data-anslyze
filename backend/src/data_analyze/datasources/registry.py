"""按数据源 ID 缓存驱动（连接池）。数据源配置一改就丢弃旧的。"""

from __future__ import annotations

import asyncio

from data_analyze.datasources.base import ConnectionConfig, DataSourceError, Driver
from data_analyze.datasources.clickhouse import ClickHouseDriver
from data_analyze.datasources.mongodb import MongoDriver
from data_analyze.datasources.mysql import MySqlDriver
from data_analyze.datasources.postgres import PostgresDriver

DRIVERS: dict[str, type[Driver]] = {
    "mysql": MySqlDriver,
    "postgresql": PostgresDriver,
    "clickhouse": ClickHouseDriver,
    "mongodb": MongoDriver,
}
KIND_LABELS = {"mysql": "MySQL", "postgresql": "PostgreSQL", "clickhouse": "ClickHouse", "mongodb": "MongoDB"}


def create_driver(config: ConnectionConfig) -> Driver:
    driver_cls = DRIVERS.get(config.kind)
    if driver_cls is None:
        raise DataSourceError(f"暂不支持的数据源类型：{config.kind}")
    return driver_cls(config)


class DriverRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, tuple[ConnectionConfig, Driver]] = {}
        self._lock = asyncio.Lock()

    async def get(self, datasource_id: str, config: ConnectionConfig) -> Driver:
        async with self._lock:
            cached = self._drivers.get(datasource_id)
            if cached is not None and cached[0] == config:
                return cached[1]
            if cached is not None:
                await cached[1].close()
            driver = create_driver(config)
            self._drivers[datasource_id] = (config, driver)
            return driver

    async def evict(self, datasource_id: str) -> None:
        async with self._lock:
            cached = self._drivers.pop(datasource_id, None)
        if cached is not None:
            await cached[1].close()

    async def close_all(self) -> None:
        async with self._lock:
            drivers = list(self._drivers.values())
            self._drivers.clear()
        for _, driver in drivers:
            await driver.close()
