"""Metadata-only OpenAI usage ledger for the local ADM dashboard."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


DEFAULT_USAGE_PATH = Path.home() / ".local" / "state" / "nexo" / "usage.jsonl"


class UsageLedger:
    """Append token counts and latency, never prompts, audio, images or secrets."""

    def __init__(self, path: Path | str = DEFAULT_USAGE_PATH) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()

    def record(
        self,
        *,
        provider: str,
        model: str,
        usage: Mapping[str, Any] | None,
        latency_ms: float,
        response_id: str = "",
    ) -> None:
        raw = dict(usage or {})
        item = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "provider": str(provider)[:32],
            "model": str(model)[:96],
            "response_id": str(response_id)[:128],
            "input_tokens": self._integer(raw.get("input_tokens")),
            "output_tokens": self._integer(raw.get("output_tokens")),
            "total_tokens": self._integer(raw.get("total_tokens")),
            "latency_ms": round(max(0.0, float(latency_ms)), 1),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        current = (now or datetime.now().astimezone()).astimezone()
        today = current.date().isoformat()
        month = today[:7]
        totals = {
            "session": self._empty_totals(),
            "today": self._empty_totals(),
            "month": self._empty_totals(),
        }
        entries = self._read_entries()
        for item in entries:
            self._add(totals["session"], item)
            stamp = str(item.get("at") or "")
            if stamp.startswith(today):
                self._add(totals["today"], item)
            if stamp.startswith(month):
                self._add(totals["month"], item)
        return {
            "available": True,
            "currency": None,
            "estimated_cost": None,
            "note": "Tokens medidos pela API; custo indisponível sem tabela de preços versionada.",
            **totals,
        }

    def _read_entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        with self._lock, self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    entries.append(item)
        return entries[-5000:]

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _empty_totals(cls) -> dict[str, int | float]:
        return {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "latency_ms": 0.0}

    @classmethod
    def _add(cls, target: dict[str, Any], item: Mapping[str, Any]) -> None:
        target["requests"] += 1
        target["input_tokens"] += cls._integer(item.get("input_tokens"))
        target["output_tokens"] += cls._integer(item.get("output_tokens"))
        target["total_tokens"] += cls._integer(item.get("total_tokens"))
        target["latency_ms"] = round(float(target["latency_ms"]) + float(item.get("latency_ms") or 0), 1)
