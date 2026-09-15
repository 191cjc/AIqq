"""SQLite connection lifecycle shared by database repositories."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


class SQLiteConnection:
    def __init__(self, path: str | Path, *, timeout_seconds: int = 10) -> None:
        self.path = Path(path).expanduser()
        self.timeout_seconds = timeout_seconds
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    async def initialize(self) -> None:
        async with self._lock:
            await self._initialize_locked()

    async def _initialize_locked(self) -> aiosqlite.Connection:
        if self._connection is not None:
            return self._connection
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(
            self.path, timeout=self.timeout_seconds
        )
        try:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA journal_mode=WAL")
            await connection.execute("PRAGMA synchronous=NORMAL")
            await connection.execute("PRAGMA busy_timeout=10000")
            await connection.execute("PRAGMA foreign_keys=ON")
        except Exception:
            await connection.close()
            raise
        self._connection = connection
        os.chmod(self.path, 0o600)
        return connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            connection = await self._initialize_locked()
            try:
                yield connection
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    @asynccontextmanager
    async def read(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            yield await self._initialize_locked()

    async def close(self) -> None:
        async with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                await connection.close()
