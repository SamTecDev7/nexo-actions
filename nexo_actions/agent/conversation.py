"""Bounded in-memory conversation state for natural voice follow-ups."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional


class ConversationManager:
    """Owns one local voice session with compact history and references."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 180,
        max_messages: int = 24,
        max_tokens: int = 2200,
        summary_chars: int = 1400,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl_seconds = max(5.0, float(ttl_seconds))
        self.max_messages = max(4, int(max_messages))
        self.max_tokens = max(256, int(max_tokens))
        self.summary_chars = max(200, int(summary_chars))
        self._clock = clock
        self._lock = threading.RLock()
        self._active = False
        self._session_id: Optional[str] = None
        self._started_at: Optional[float] = None
        self._last_activity: Optional[float] = None
        self._ended_reason: Optional[str] = None
        self._messages: list[Dict[str, Any]] = []
        self._summary = ""
        self._references: Dict[str, Any] = {}
        self._stale_file_reference_pruned = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active and not self._is_expired_unlocked()

    @property
    def expired(self) -> bool:
        with self._lock:
            return self._active and self._is_expired_unlocked()

    @property
    def session_id(self) -> Optional[str]:
        with self._lock:
            return self._session_id

    @property
    def references(self) -> Dict[str, Any]:
        with self._lock:
            self._prune_stale_file_references_unlocked()
            return deepcopy(self._references)

    def start(self) -> str:
        with self._lock:
            now = self._clock()
            self._active = True
            self._session_id = uuid.uuid4().hex
            self._started_at = now
            self._last_activity = now
            self._ended_reason = None
            self._messages = []
            self._summary = ""
            self._references = {}
            self._stale_file_reference_pruned = False
            return self._session_id

    def touch(self) -> None:
        with self._lock:
            if self._active:
                self._last_activity = self._clock()

    def end(self, reason: str = "user") -> None:
        with self._lock:
            self._active = False
            self._ended_reason = reason

    def reset(self) -> None:
        with self._lock:
            self._active = False
            self._session_id = None
            self._started_at = None
            self._last_activity = None
            self._ended_reason = "reset"
            self._messages = []
            self._summary = ""
            self._references = {}
            self._stale_file_reference_pruned = False

    def add_user(self, content: str) -> None:
        self._append("user", content)

    def add_assistant(self, content: str) -> None:
        self._append("assistant", content)

    def add_tool_call(self, name: str, arguments: Mapping[str, Any]) -> None:
        args = dict(arguments)
        self._update_references_from_call(name, args)
        self._append(
            "assistant",
            f"[Ação escolhida: {name}({json.dumps(args, ensure_ascii=False, sort_keys=True)})]",
            kind="tool_call",
            name=name,
        )

    def add_tool_result(self, name: str, result: Any) -> None:
        summary = self._summarize_result(result)
        self._update_references_from_result(name, result)
        self._append(
            "assistant",
            f"[Resultado de {name}: {summary}]",
            kind="tool_result",
            name=name,
        )

    def remember_tool_call(self, name: str, arguments: Mapping[str, Any]) -> None:
        """Keep short-lived references without opening continuous conversation."""
        with self._lock:
            self._last_activity = self._clock()
        self._update_references_from_call(name, dict(arguments))

    def remember_tool_result(self, name: str, result: Any) -> None:
        """Update recent references without adding an inactive chat turn."""
        with self._lock:
            self._last_activity = self._clock()
        self._update_references_from_result(name, result)

    def remember_user(self, content: str) -> None:
        """Keep a bounded one-shot command for a later wakeword follow-up."""
        self._remember_turn("user", content)

    def remember_assistant(self, content: str) -> None:
        """Keep a completed one-shot answer without opening free conversation."""
        self._remember_turn("assistant", content)

    def _remember_turn(self, role: str, content: str) -> None:
        normalized = " ".join(str(content).split()).strip()
        if not normalized:
            return
        with self._lock:
            # A command can pass through wake capture and the generic action
            # entry point.  Do not let that duplicate the same model turn.
            if (
                self._messages
                and self._messages[-1].get("role") == role
                and self._messages[-1].get("content") == normalized
            ):
                self._last_activity = self._clock()
                return
        self._append(role, normalized, kind="one_shot")

    def reference_context(self) -> str:
        """Return only fresh references for a later wakeword command."""
        with self._lock:
            self._prune_stale_file_references_unlocked()
            if (
                not self._references
                or self._last_activity is None
                or self._clock() - self._last_activity >= self.ttl_seconds
            ):
                return ""
            refs = ", ".join(
                f"{key}={value}"
                for key, value in sorted(self._references.items())
            )
            return f"Referências recentes da última ação: {refs}"

    def recent_model_messages(self) -> list[Dict[str, str]]:
        """Return fresh one-shot turns and references without activating chat.

        Voice workflows are commonly split across several wakeword commands,
        for example: locate a file, inspect it, then "edit it".  These turns
        need short-lived continuity even though free conversation is disabled.
        """
        with self._lock:
            self._prune_stale_file_references_unlocked()
            if (
                self._last_activity is None
                or self._clock() - self._last_activity >= self.ttl_seconds
            ):
                return []
            messages: list[Dict[str, str]] = []
            context = self._context_header_unlocked()
            if context:
                messages.append({"role": "system", "content": context})
            messages.extend(
                {"role": item["role"], "content": item["content"]}
                for item in self._messages
            )
            return messages

    def model_messages(self) -> list[Dict[str, str]]:
        """Return compact messages accepted by Ollama's chat endpoint."""
        with self._lock:
            self._prune_stale_file_references_unlocked()
            messages: list[Dict[str, str]] = []
            context = self._context_header_unlocked()
            if context:
                messages.append({"role": "system", "content": context})
            messages.extend(
                {"role": item["role"], "content": item["content"]}
                for item in self._messages
            )
            return messages

    def context_text(self) -> str:
        with self._lock:
            self._prune_stale_file_references_unlocked()
            parts = []
            header = self._context_header_unlocked()
            if header:
                parts.append(header)
            for item in self._messages[-8:]:
                label = "Usuário" if item["role"] == "user" else "Nexo"
                parts.append(f"{label}: {item['content']}")
            return "\n".join(parts)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self._prune_stale_file_references_unlocked()
            return {
                "active": self._active and not self._is_expired_unlocked(),
                "expired": self._active and self._is_expired_unlocked(),
                "session_id": self._session_id,
                "started_at": self._started_at,
                "last_activity": self._last_activity,
                "ended_reason": self._ended_reason,
                "summary": self._summary,
                "references": deepcopy(self._references),
                "messages": deepcopy(self._messages),
                "approx_tokens": self._approx_tokens_unlocked(),
            }

    def _append(self, role: str, content: str, **metadata: Any) -> None:
        content = " ".join(str(content).split()).strip()
        if not content:
            return
        with self._lock:
            item = {
                "role": role,
                "content": content,
                "timestamp": self._clock(),
                **metadata,
            }
            self._messages.append(item)
            self._last_activity = item["timestamp"]
            self._compact_unlocked()

    def _compact_unlocked(self) -> None:
        removed = []
        while (
            len(self._messages) > self.max_messages
            or self._approx_tokens_unlocked() > self.max_tokens
        ) and len(self._messages) > 4:
            removed.append(self._messages.pop(0))
        if removed:
            fragments = []
            for item in removed:
                label = "U" if item["role"] == "user" else "N"
                fragments.append(f"{label}: {item['content']}")
            addition = " | ".join(fragments)
            self._summary = " | ".join(
                part for part in (self._summary, addition) if part
            )
            if len(self._summary) > self.summary_chars:
                self._summary = "…" + self._summary[-(self.summary_chars - 1):]

        # ``max_tokens`` is deliberately approximate, but it is still a hard
        # upper bound.  A single very long utterance/tool summary must not be
        # able to bypass it just because only the four newest messages remain.
        budget_chars = self.max_tokens * 4
        summary_budget = min(self.summary_chars, max(120, budget_chars // 3))
        if len(self._summary) > summary_budget:
            self._summary = "…" + self._summary[-(summary_budget - 1):]

        while self._approx_tokens_unlocked() > self.max_tokens and self._messages:
            overflow = (
                self._approx_tokens_unlocked() - self.max_tokens
            ) * 4
            target = next(
                (
                    item for item in self._messages
                    if len(item["content"]) > 120
                ),
                None,
            )
            if target is None:
                break
            # Reserve one character for the ellipsis so each pass always
            # makes progress and cannot loop forever at exactly 121 chars.
            keep = max(119, len(target["content"]) - overflow - 4)
            target["content"] = target["content"][:keep].rstrip() + "…"

    def _approx_tokens_unlocked(self) -> int:
        chars = len(self._summary)
        chars += sum(len(item["content"]) for item in self._messages)
        chars += len(json.dumps(self._references, ensure_ascii=False))
        return max(1, chars // 4)

    def _is_expired_unlocked(self) -> bool:
        return bool(
            self._active
            and self._last_activity is not None
            and self._clock() - self._last_activity >= self.ttl_seconds
        )

    def _context_header_unlocked(self) -> str:
        parts = []
        if self._summary:
            parts.append(f"Resumo anterior: {self._summary}")
        if self._references:
            refs = ", ".join(
                f"{key}={value}" for key, value in sorted(self._references.items())
            )
            parts.append(f"Referências atuais: {refs}")
        if self._stale_file_reference_pruned:
            parts.append(
                "Uma referência antiga apontava para um arquivo que não existe "
                "mais e foi removida. Não tente reconstruir esse caminho; localize "
                "o arquivo atual antes de agir."
            )
        return "\n".join(parts)

    @staticmethod
    def _reference_local_path(value: Any) -> Optional[Path]:
        """Map absolute Windows/WSL references to a checkable local path."""
        raw = str(value or "").strip()
        if not raw:
            return None
        normalized = raw.replace("\\", "/")
        drive = re.match(r"^([A-Za-z]):/(.*)$", normalized)
        if drive:
            return Path("/mnt") / drive.group(1).casefold() / drive.group(2)
        if normalized.startswith("//wsl.localhost/"):
            parts = [part for part in normalized.split("/") if part]
            if len(parts) >= 3:
                return Path("/").joinpath(*parts[2:])
        if normalized.startswith("/"):
            return Path(normalized)
        # Aliases such as Downloads/style.css are resolved by the file tool;
        # they cannot be validated safely here without duplicating its config.
        return None

    def _prune_stale_file_references_unlocked(self) -> None:
        recent = list(self._references.get("recent_files") or [])
        valid_recent = []
        stale = set()
        for item in recent:
            local = self._reference_local_path(item)
            if local is not None and not local.exists():
                stale.add(str(item).casefold())
            else:
                valid_recent.append(item)

        last_path = self._references.get("last_path")
        local_last = self._reference_local_path(last_path)
        if local_last is not None and not local_last.exists():
            stale.add(str(last_path).casefold())

        if not stale:
            return
        self._stale_file_reference_pruned = True
        if valid_recent:
            self._references["recent_files"] = valid_recent[:6]
            self._references["last_path"] = valid_recent[0]
            self._references["last_file"] = (
                str(valid_recent[0]).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            )
        else:
            self._references.pop("recent_files", None)
            self._references.pop("last_path", None)
            self._references.pop("last_file", None)

    def _update_references_from_call(self, name: str, arguments: Mapping[str, Any]) -> None:
        with self._lock:
            self._references["last_tool"] = name
            if name in {"open_app", "close_app"} and arguments.get("app_name"):
                self._references["last_app"] = arguments["app_name"]
            if name == "process_status" and arguments.get("query"):
                self._references["last_process"] = arguments["query"]
            if name == "youtube_search" and arguments.get("query"):
                self._references["last_media_query"] = arguments["query"]
            if name == "create_text_file":
                directory = str(arguments.get("directory") or "").rstrip("/\\")
                filename = str(arguments.get("filename") or "").strip()
                if directory:
                    self._references["last_directory"] = directory
                if directory and filename:
                    self._remember_file_unlocked(directory + "/" + filename)
            elif name in {
                "open_file", "reveal_file", "read_text_file", "rewrite_text_file",
                "replace_text_in_file", "rename_file",
            } and arguments.get("path"):
                self._remember_file_unlocked(str(arguments["path"]))
            elif name in {"copy_file", "move_file"}:
                path = arguments.get("destination") or arguments.get("source")
                if path:
                    self._remember_file_unlocked(str(path))
            if name.startswith("email_"):
                if arguments.get("account"):
                    self._references["last_email_account"] = arguments["account"]
                if arguments.get("message_id"):
                    self._references["last_email_message_id"] = arguments["message_id"]
                if arguments.get("attachment_id"):
                    self._references["last_email_attachment_id"] = arguments["attachment_id"]

    def _update_references_from_result(self, name: str, result: Any) -> None:
        if not isinstance(result, Mapping):
            return
        with self._lock:
            if name in {"open_app", "close_app"}:
                matched = result.get("matched") or result.get("requested")
                if matched:
                    self._references["last_app"] = matched
            root = result.get("root")
            if root:
                self._references["last_path"] = root
            path = result.get("destination") or result.get("path")
            if path and name in {
                "create_text_file", "open_file", "reveal_file", "read_text_file",
                "rewrite_text_file", "replace_text_in_file", "copy_file", "move_file",
                "rename_file",
            }:
                self._remember_file_unlocked(str(path))
            files = result.get("files")
            if isinstance(files, list) and files:
                first = files[0]
                if isinstance(first, Mapping) and first.get("path"):
                    self._remember_file_unlocked(str(first["path"]))
            if name.startswith("email_"):
                if result.get("account"):
                    self._references["last_email_account"] = result["account"]
                message = result.get("message")
                messages = result.get("messages")
                if not isinstance(message, Mapping) and isinstance(messages, list) and messages:
                    message = messages[0]
                if isinstance(message, Mapping) and message.get("message_id"):
                    self._references["last_email_message_id"] = message["message_id"]
                attachments = result.get("attachments")
                if isinstance(attachments, list) and attachments:
                    attachment = attachments[0]
                    if isinstance(attachment, Mapping) and attachment.get("attachment_id"):
                        self._references["last_email_attachment_id"] = attachment["attachment_id"]

    def _remember_file_unlocked(self, path: str) -> None:
        path = str(path).strip()
        if not path:
            return
        self._stale_file_reference_pruned = False
        self._references["last_path"] = path
        self._references["last_file"] = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        recent = [
            item for item in self._references.get("recent_files", [])
            if str(item).casefold() != path.casefold()
        ]
        self._references["recent_files"] = [path, *recent][:6]

    @classmethod
    def _summarize_result(cls, result: Any) -> str:
        if not isinstance(result, Mapping):
            return cls._short(result)
        preferred = (
            "ok", "matched", "requested", "closed", "found", "scope",
            "volume_percent", "muted", "cpu_percent", "memory_mb",
            "download_mbps", "upload_mbps", "ping_ms", "average_ms",
            "loss_percent", "complete", "partial", "timed_out", "root",
            "account", "query", "count",
        )
        compact = {key: result[key] for key in preferred if key in result}
        for key in ("cpu", "memory", "gpu", "internet"):
            if key in result:
                compact[key] = cls._bounded_mapping(result[key], limit=8)
        if isinstance(result.get("message"), Mapping):
            compact["message"] = cls._bounded_mapping(result["message"], limit=10)
        for key in ("processes", "folders", "files", "disks", "messages", "attachments", "accounts"):
            if key in result and isinstance(result[key], (list, tuple)):
                compact[key] = [
                    cls._bounded_mapping(item, limit=6)
                    for item in list(result[key])[:3]
                ]
        if not compact:
            compact = cls._bounded_mapping(result, limit=10)
        return cls._short(json.dumps(compact, ensure_ascii=False, separators=(",", ":")), 900)

    @staticmethod
    def _bounded_mapping(value: Any, limit: int) -> Any:
        if not isinstance(value, Mapping):
            return value
        safe = {}
        for key, item in list(value.items())[:limit]:
            lowered = str(key).casefold()
            if any(secret in lowered for secret in ("token", "secret", "password", "authorization")):
                safe[key] = "<redacted>"
            elif lowered in {"body", "raw", "content"}:
                continue
            elif isinstance(item, (str, int, float, bool)) or item is None:
                safe[key] = (
                    item[:239] + "…"
                    if isinstance(item, str) and len(item) > 240
                    else item
                )
        return safe

    @staticmethod
    def _short(value: Any, limit: int = 500) -> str:
        text = " ".join(str(value).split())
        return text if len(text) <= limit else text[: limit - 1] + "…"
