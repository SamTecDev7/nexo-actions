"""Short-lived bindings between a visual observation and a Windows target.

This module is deliberately platform independent.  The Windows companion owns
the store, while unit tests can exercise the stale-target policy without a real
desktop session.
"""

from __future__ import annotations

import secrets
import threading
import time
from copy import deepcopy
from typing import Any, Callable, Mapping


class ObservationRejected(RuntimeError):
    """Raised when an input action no longer matches what the user observed."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class ObservationStore:
    """Bounded, one-shot observation store with focus and geometry checks."""

    def __init__(
        self,
        ttl_seconds: float = 30.0,
        max_entries: int = 32,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        self.ttl_seconds = max(5.0, min(float(ttl_seconds), 120.0))
        self.max_entries = max(4, min(int(max_entries), 128))
        self._clock = clock
        self._wall_clock = wall_clock
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _bounds(value: Mapping[str, Any]) -> dict[str, int]:
        return {
            "left": int(value["left"]),
            "top": int(value["top"]),
            "width": int(value["width"]),
            "height": int(value["height"]),
        }

    def create(
        self,
        *,
        hwnd: int,
        window_bounds: Mapping[str, Any],
        capture_bounds: Mapping[str, Any],
        image_width: int,
        image_height: int,
        scope: str,
    ) -> dict[str, Any]:
        now = self._clock()
        created_at = self._wall_clock()
        item = {
            "observation_id": secrets.token_urlsafe(24),
            "scope": str(scope),
            "hwnd": int(hwnd),
            "window_bounds": self._bounds(window_bounds),
            "capture_bounds": self._bounds(capture_bounds),
            "image_width": int(image_width),
            "image_height": int(image_height),
            "created_at": created_at,
            "expires_at": created_at + self.ttl_seconds,
            "_created_monotonic": now,
        }
        with self._lock:
            self._prune_unlocked(now)
            while len(self._items) >= self.max_entries:
                oldest = min(
                    self._items,
                    key=lambda key: self._items[key]["_created_monotonic"],
                )
                self._items.pop(oldest, None)
            self._items[item["observation_id"]] = item
        return self._public(item)

    def validate(
        self,
        observation_id: str,
        *,
        current_hwnd: int,
        current_bounds: Mapping[str, Any],
        consume: bool = True,
    ) -> dict[str, Any]:
        identifier = str(observation_id or "").strip()
        if not identifier:
            raise ObservationRejected(
                "missing_observation",
                "A ação visual exige uma observação recente da tela",
            )
        now = self._clock()
        with self._lock:
            item = self._items.get(identifier)
            if item is None:
                self._prune_unlocked(now)
                raise ObservationRejected(
                    "unknown_observation",
                    "A observação não existe, expirou ou já foi utilizada",
                )
            if now - item["_created_monotonic"] > self.ttl_seconds:
                self._items.pop(identifier, None)
                raise ObservationRejected(
                    "expired_observation",
                    "A tela observada ficou antiga; observe novamente antes de agir",
                )
            if int(current_hwnd) != item["hwnd"]:
                raise ObservationRejected(
                    "focus_changed",
                    "A janela ativa mudou desde a observação; a ação foi recusada",
                )
            if self._bounds(current_bounds) != item["window_bounds"]:
                raise ObservationRejected(
                    "window_changed",
                    "A janela mudou de posição ou tamanho; observe novamente antes de agir",
                )
            if consume:
                self._items.pop(identifier, None)
            return self._public(item)

    def _prune_unlocked(self, now: float) -> None:
        expired = [
            key
            for key, item in self._items.items()
            if now - item["_created_monotonic"] > self.ttl_seconds
        ]
        for key in expired:
            self._items.pop(key, None)

    @staticmethod
    def _public(item: Mapping[str, Any]) -> dict[str, Any]:
        result = deepcopy(dict(item))
        result.pop("_created_monotonic", None)
        return result
