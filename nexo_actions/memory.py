"""Small, private and bounded persistent memory for Nexo.

Only explicit preferences and metadata-only task receipts belong here. Raw
conversation transcripts, audio, screenshots and credentials must never be
stored in this database.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
DEFAULT_MEMORY_PATH = Path.home() / ".local" / "state" / "nexo" / "memory.sqlite3"

MAX_KEY_BYTES = 160
MAX_SOURCE_BYTES = 64
MAX_VALUE_BYTES = 8 * 1024
MAX_QUERY_BYTES = 512
MAX_ACTION_BYTES = 1024
MAX_STATUS_BYTES = 64
MAX_SUMMARY_BYTES = 4 * 1024
MAX_TARGET_BYTES = 1024
MAX_METADATA_BYTES = 8 * 1024
MAX_TASK_ID_BYTES = 160
MAX_PREFERENCES = 1_000
MAX_TASK_RECEIPTS = 2_000
MAX_SEARCH_LIMIT = 100


class SensitiveMemoryError(ValueError):
    """Raised when data appears to contain a credential or access token."""


class MemorySizeError(ValueError):
    """Raised when a memory item exceeds its deliberate storage boundary."""


_SECRET_FIELD_MARKERS = {
    "accesstoken",
    "apikey",
    "authorization",
    "clientsecret",
    "cookie",
    "credential",
    "password",
    "passwd",
    "privatekey",
    "refreshtoken",
    "secret",
    "sessiontoken",
    "token",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b(?:ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{12,}", re.IGNORECASE),
    re.compile(r"\bAKIA[A-Z0-9]{12,}"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}"),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
    ),
    re.compile(
        r"\b(?:OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)


class MemoryStore:
    """Thread-safe SQLite memory with a versioned, bounded schema."""

    def __init__(self, path: Path | str = DEFAULT_MEMORY_PATH) -> None:
        raw_path = os.fspath(path)
        self.path = Path(raw_path).expanduser()
        self._in_memory = raw_path == ":memory:"
        self._lock = threading.RLock()
        self._closed = False

        database = ":memory:" if self._in_memory else str(self.path)
        if not self._in_memory:
            self._prepare_private_path()
        self._connection = sqlite3.connect(
            database,
            timeout=5.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        try:
            self._configure()
            self._migrate()
            self._secure_files()
        except Exception:
            self._connection.close()
            raise

    def remember_preference(
        self,
        key: str,
        value: Any,
        source: str = "user",
        confidence: float = 1.0,
        confirmed: bool = True,
    ) -> bool:
        """Persist a preference, returning False for weak unconfirmed inference."""

        clean_key = self._text(key, "key", MAX_KEY_BYTES, allow_empty=False)
        clean_source = self._text(source, "source", MAX_SOURCE_BYTES, allow_empty=False)
        try:
            score = float(confidence)
        except (TypeError, ValueError) as exc:
            raise ValueError("confidence must be a number between 0 and 1") from exc
        if not math.isfinite(score) or score < 0.0 or score > 1.0:
            raise ValueError("confidence must be a number between 0 and 1")
        if not isinstance(confirmed, bool):
            raise TypeError("confirmed must be a boolean")
        is_confirmed = confirmed
        if not is_confirmed and score < 0.9:
            return False

        self._assert_not_sensitive({clean_key: value})
        self._assert_not_sensitive(clean_source)
        encoded = self._json(value, "preference value", MAX_VALUE_BYTES)
        now = self._now()
        with self._lock:
            connection = self._open_connection()
            with connection:
                connection.execute(
                    """
                    INSERT INTO preferences(
                        key, value_json, source, confidence, confirmed,
                        created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json=excluded.value_json,
                        source=excluded.source,
                        confidence=excluded.confidence,
                        confirmed=excluded.confirmed,
                        updated_at=excluded.updated_at
                    """,
                    (
                        clean_key,
                        encoded,
                        clean_source,
                        score,
                        int(is_confirmed),
                        now,
                        now,
                    ),
                )
                self._trim_preferences(connection)
            self._secure_files()
        return True

    def get_preference(self, key: str) -> dict[str, Any] | None:
        clean_key = self._text(key, "key", MAX_KEY_BYTES, allow_empty=False)
        with self._lock:
            row = self._open_connection().execute(
                """
                SELECT key, value_json, source, confidence, confirmed,
                       created_at, updated_at
                  FROM preferences
                 WHERE key = ? COLLATE NOCASE
                """,
                (clean_key,),
            ).fetchone()
        return self._preference(row) if row is not None else None

    def search_preferences(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clean_query = self._text(
            query,
            "query",
            MAX_QUERY_BYTES,
            allow_empty=True,
        )
        bounded_limit = self._limit(limit)
        needle = self._search_text(clean_query)
        with self._lock:
            rows = self._open_connection().execute(
                """
                SELECT key, value_json, source, confidence, confirmed,
                       created_at, updated_at
                  FROM preferences
                 ORDER BY updated_at DESC, key ASC
                """
            ).fetchall()
        matches = [
            row
            for row in rows
            if needle in self._search_text(
                str(row["key"]) + "\n" + str(row["value_json"])
            )
        ]
        return [self._preference(row) for row in matches[:bounded_limit]]

    def forget_preference(self, key: str) -> bool:
        """Delete one preference; repeated deletion is harmless."""

        clean_key = self._text(key, "key", MAX_KEY_BYTES, allow_empty=False)
        with self._lock:
            connection = self._open_connection()
            with connection:
                cursor = connection.execute(
                    "DELETE FROM preferences WHERE key = ? COLLATE NOCASE",
                    (clean_key,),
                )
            self._secure_files()
            return cursor.rowcount > 0

    def record_task_receipt(
        self,
        action: str,
        status: str = "completed",
        *,
        task_id: str | None = None,
        summary: str = "",
        target: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record bounded execution evidence, never raw prompts or tool secrets."""

        clean_action = self._text(
            action,
            "action",
            MAX_ACTION_BYTES,
            allow_empty=False,
        )
        clean_status = self._text(
            status,
            "status",
            MAX_STATUS_BYTES,
            allow_empty=False,
        )
        clean_summary = self._text(
            summary,
            "summary",
            MAX_SUMMARY_BYTES,
            allow_empty=True,
        )
        clean_target = self._text(
            target,
            "target",
            MAX_TARGET_BYTES,
            allow_empty=True,
        )
        clean_task_id = self._text(
            task_id or uuid.uuid4().hex,
            "task_id",
            MAX_TASK_ID_BYTES,
            allow_empty=False,
        )
        if metadata is None:
            clean_metadata: dict[str, Any] = {}
        elif isinstance(metadata, Mapping):
            clean_metadata = dict(metadata)
        else:
            raise TypeError("metadata must be a mapping")

        receipt_content = {
            "task_id": clean_task_id,
            "action": clean_action,
            "status": clean_status,
            "summary": clean_summary,
            "target": clean_target,
            "metadata": clean_metadata,
        }
        self._assert_not_sensitive(receipt_content)
        encoded_metadata = self._json(
            clean_metadata,
            "receipt metadata",
            MAX_METADATA_BYTES,
        )
        created_at = self._now()
        with self._lock:
            connection = self._open_connection()
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO task_receipts(
                        task_id, action, status, summary, target,
                        metadata_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_task_id,
                        clean_action,
                        clean_status,
                        clean_summary,
                        clean_target,
                        encoded_metadata,
                        created_at,
                    ),
                )
                self._trim_receipts(connection)
            self._secure_files()
            receipt_id = int(cursor.lastrowid)
        return {
            "id": receipt_id,
            "task_id": clean_task_id,
            "action": clean_action,
            "status": clean_status,
            "summary": clean_summary,
            "target": clean_target,
            "metadata": clean_metadata,
            "created_at": created_at,
        }

    def recent_task_receipts(
        self,
        limit: int = 20,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        bounded_limit = self._limit(limit)
        parameters: tuple[Any, ...]
        if status is None:
            sql = """
                SELECT id, task_id, action, status, summary, target,
                       metadata_json, created_at
                  FROM task_receipts
                 ORDER BY id DESC
                 LIMIT ?
            """
            parameters = (bounded_limit,)
        else:
            clean_status = self._text(
                status,
                "status",
                MAX_STATUS_BYTES,
                allow_empty=False,
            )
            sql = """
                SELECT id, task_id, action, status, summary, target,
                       metadata_json, created_at
                  FROM task_receipts
                 WHERE status = ? COLLATE NOCASE
                 ORDER BY id DESC
                 LIMIT ?
            """
            parameters = (clean_status, bounded_limit)
        with self._lock:
            rows = self._open_connection().execute(sql, parameters).fetchall()
        return [self._receipt(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True
            self._secure_files()

    def _configure(self) -> None:
        with self._lock:
            connection = self._open_connection()
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA secure_delete=ON")
            connection.execute("PRAGMA wal_autocheckpoint=100")
            connection.execute("PRAGMA journal_size_limit=4194304")
            if not self._in_memory:
                mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0])
                if mode.lower() != "wal":
                    raise RuntimeError("SQLite WAL mode could not be enabled")

    def _migrate(self) -> None:
        with self._lock:
            connection = self._open_connection()
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Memory schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version < 1:
                with connection:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS preferences(
                            key TEXT PRIMARY KEY COLLATE NOCASE,
                            value_json TEXT NOT NULL,
                            source TEXT NOT NULL,
                            confidence REAL NOT NULL
                                CHECK(confidence >= 0.0 AND confidence <= 1.0),
                            confirmed INTEGER NOT NULL
                                CHECK(confirmed IN (0, 1)),
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS preferences_updated_idx
                            ON preferences(updated_at DESC);

                        CREATE TABLE IF NOT EXISTS task_receipts(
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            task_id TEXT NOT NULL,
                            action TEXT NOT NULL,
                            status TEXT NOT NULL,
                            summary TEXT NOT NULL,
                            target TEXT NOT NULL,
                            metadata_json TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS task_receipts_created_idx
                            ON task_receipts(id DESC);
                        CREATE INDEX IF NOT EXISTS task_receipts_status_idx
                            ON task_receipts(status, id DESC);
                        PRAGMA user_version=1;
                        """
                    )

    def _prepare_private_path(self) -> None:
        if self.path.exists() and self.path.is_symlink():
            raise ValueError("Memory database path must not be a symbolic link")
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not parent_existed:
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _secure_files(self) -> None:
        if self._in_memory:
            return
        for candidate in (
            self.path,
            Path(str(self.path) + "-wal"),
            Path(str(self.path) + "-shm"),
        ):
            try:
                if candidate.exists():
                    candidate.chmod(0o600)
            except OSError:
                pass

    def _open_connection(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("MemoryStore is closed")
        return self._connection

    @staticmethod
    def _now() -> str:
        return (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _search_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", str(value))
        return "".join(
            character for character in normalized
            if not unicodedata.combining(character)
        ).casefold()

    @staticmethod
    def _text(
        value: Any,
        field: str,
        max_bytes: int,
        *,
        allow_empty: bool,
    ) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field} must be text")
        cleaned = value.strip()
        if not allow_empty and not cleaned:
            raise ValueError(f"{field} must not be empty")
        if "\x00" in cleaned:
            raise ValueError(f"{field} contains a null character")
        if len(cleaned.encode("utf-8")) > max_bytes:
            raise MemorySizeError(f"{field} exceeds {max_bytes} UTF-8 bytes")
        return cleaned

    @staticmethod
    def _json(value: Any, field: str, max_bytes: int) -> str:
        try:
            encoded = json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{field} must be JSON serializable") from exc
        if len(encoded.encode("utf-8")) > max_bytes:
            raise MemorySizeError(f"{field} exceeds {max_bytes} UTF-8 bytes")
        return encoded

    @classmethod
    def _assert_not_sensitive(cls, value: Any) -> None:
        seen: set[int] = set()
        nodes = 0

        def visit(item: Any, depth: int, field_name: str | None = None) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > 512 or depth > 8:
                raise MemorySizeError("memory value is too deeply nested")
            if field_name and cls._sensitive_field(field_name):
                raise SensitiveMemoryError(
                    f"sensitive field '{field_name}' cannot be persisted"
                )
            if isinstance(item, str):
                if any(pattern.search(item) for pattern in _SECRET_VALUE_PATTERNS):
                    raise SensitiveMemoryError(
                        "credential-like content cannot be persisted"
                    )
                return
            if isinstance(item, Mapping):
                identity = id(item)
                if identity in seen:
                    raise ValueError("cyclic values cannot be persisted")
                seen.add(identity)
                for key, child in item.items():
                    visit(child, depth + 1, str(key))
                seen.remove(identity)
                return
            if isinstance(item, (list, tuple)):
                identity = id(item)
                if identity in seen:
                    raise ValueError("cyclic values cannot be persisted")
                seen.add(identity)
                for child in item:
                    visit(child, depth + 1)
                seen.remove(identity)

        visit(value, 0)

    @staticmethod
    def _sensitive_field(field: str) -> bool:
        normalized = "".join(character for character in field.casefold() if character.isalnum())
        return normalized in _SECRET_FIELD_MARKERS or any(
            marker in normalized
            for marker in (
                "apikey",
                "accesstoken",
                "authorization",
                "clientsecret",
                "cookie",
                "credential",
                "password",
                "passwd",
                "privatekey",
                "refreshtoken",
                "secret",
                "sessiontoken",
            )
        )

    @staticmethod
    def _limit(value: int) -> int:
        try:
            integer = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be a positive integer") from exc
        if integer <= 0:
            raise ValueError("limit must be a positive integer")
        return min(integer, MAX_SEARCH_LIMIT)

    @staticmethod
    def _preference(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "key": str(row["key"]),
            "value": json.loads(str(row["value_json"])),
            "source": str(row["source"]),
            "confidence": float(row["confidence"]),
            "confirmed": bool(row["confirmed"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _receipt(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "task_id": str(row["task_id"]),
            "action": str(row["action"]),
            "status": str(row["status"]),
            "summary": str(row["summary"]),
            "target": str(row["target"]),
            "metadata": json.loads(str(row["metadata_json"])),
            "created_at": str(row["created_at"]),
        }

    @staticmethod
    def _trim_preferences(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM preferences
             WHERE key IN (
                SELECT key
                  FROM preferences
                 ORDER BY updated_at DESC, key ASC
                 LIMIT -1 OFFSET ?
             )
            """,
            (MAX_PREFERENCES,),
        )

    @staticmethod
    def _trim_receipts(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM task_receipts
             WHERE id IN (
                SELECT id
                  FROM task_receipts
                 ORDER BY id DESC
                 LIMIT -1 OFFSET ?
             )
            """,
            (MAX_TASK_RECEIPTS,),
        )

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()


__all__ = [
    "DEFAULT_MEMORY_PATH",
    "MAX_PREFERENCES",
    "MAX_TASK_RECEIPTS",
    "MemorySizeError",
    "MemoryStore",
    "SCHEMA_VERSION",
    "SensitiveMemoryError",
]
