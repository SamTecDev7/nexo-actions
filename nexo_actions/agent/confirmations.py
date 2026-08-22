"""One-shot confirmation state for medium/high-risk tools."""

from __future__ import annotations

import threading
import time
import unicodedata
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class ConfirmationDecision:
    understood: bool
    approved: bool = False
    cancelled: bool = False


class ConfirmationManager:
    def __init__(self, ttl_seconds: float = 60, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = max(10.0, float(ttl_seconds))
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: dict[str, Any] | None = None

    @property
    def pending(self) -> bool:
        with self._lock:
            self._expire_unlocked()
            return self._pending is not None

    def request(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        prompt: str,
        *,
        utterance: str = "",
        strength: str = "normal",
    ) -> dict[str, Any]:
        if strength not in {"normal", "strong"}:
            raise ValueError("Nível de confirmação inválido")
        now = self._clock()
        with self._lock:
            self._pending = {
                "confirmation_id": uuid.uuid4().hex,
                "tool_name": str(tool_name),
                "arguments": deepcopy(dict(arguments)),
                "prompt": str(prompt),
                "utterance": str(utterance),
                "strength": strength,
                "created_at": now,
                "expires_at": now + self.ttl_seconds,
            }
            return deepcopy(self._pending)

    def peek(self) -> dict[str, Any] | None:
        with self._lock:
            self._expire_unlocked()
            return deepcopy(self._pending)

    def consume(self, utterance: str) -> tuple[ConfirmationDecision, dict[str, Any] | None]:
        normalized = self._normalize(utterance)
        with self._lock:
            self._expire_unlocked()
            if not self._pending:
                return ConfirmationDecision(False), None
            strength = self._pending.get("strength", "normal")
            if self._matches(normalized, approved=True, strength=strength):
                item = self._pending
                self._pending = None
                return ConfirmationDecision(True, approved=True), deepcopy(item)
            if self._matches(normalized, approved=False, strength=strength):
                item = self._pending
                self._pending = None
                return ConfirmationDecision(True, cancelled=True), deepcopy(item)
            return ConfirmationDecision(False), deepcopy(self._pending)

    def cancel(self) -> dict[str, Any] | None:
        with self._lock:
            item = self._pending
            self._pending = None
            return deepcopy(item)

    def _expire_unlocked(self) -> None:
        if self._pending and self._clock() >= self._pending["expires_at"]:
            self._pending = None

    @staticmethod
    def _normalize(value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value))
        text = "".join(char for char in text if not unicodedata.combining(char))
        return " ".join(text.casefold().replace(",", " ").replace(".", " ").split())

    @staticmethod
    def _matches(text: str, *, approved: bool, strength: str = "normal") -> bool:
        if approved and strength == "strong":
            return text in {
                "confirmo exclusao permanente",
                "sim apagar permanentemente",
                "autorizo apagar permanentemente",
            }
        phrases = (
            ("sim", "pode", "confirmo", "continue", "continua", "pode fazer", "autorizo")
            if approved
            else ("nao", "não", "cancela", "cancelar", "deixa", "melhor nao", "pare")
        )
        return text in phrases or any(text.startswith(phrase + " ") for phrase in phrases)


def confirmation_prompt(tool_name: str, arguments: Mapping[str, Any]) -> str:
    if tool_name == "window_action" and arguments.get("action") == "close":
        target = arguments.get("query") or "a janela atual"
        return f"Posso fechar {target}, mas pode haver alterações não salvas. Quer que eu continue?"
    if tool_name == "close_app" and arguments.get("force"):
        target = arguments.get("app_name") or "o aplicativo"
        return f"Forçar o fechamento de {target} pode perder trabalho não salvo. Quer que eu continue?"
    if tool_name in {"move_file", "rename_file"}:
        target = arguments.get("source") or arguments.get("path") or "o arquivo"
        return f"Vou alterar {target}. Quer que eu continue?"
    if tool_name.startswith("delete_"):
        target = arguments.get("path") or "esse item"
        if arguments.get("permanent"):
            return (
                f"A exclusão de {target} será permanente e não poderá ser desfeita. "
                "Para autorizar, diga: confirmo exclusão permanente."
            )
        return f"Quer mesmo enviar {target} para a Lixeira?"
    if tool_name == "power_action":
        action = arguments.get("action")
        labels = {"shutdown": "desligar", "restart": "reiniciar", "sleep": "suspender"}
        label = labels.get(action, "alterar o estado de energia deste computador")
        return (
            f"Vou {label} o computador. Isso pode interromper tarefas e perder trabalho "
            "não salvo. Quer que eu continue?"
        )
    if tool_name in {"email_send", "email_reply", "email_forward"}:
        if tool_name == "email_send":
            target = arguments.get("to") or "o destinatário informado"
            subject = arguments.get("subject") or "sem assunto"
            return f"Vou enviar para {target}, com o assunto {subject}. Quer que eu envie agora?"
        if tool_name == "email_forward":
            target = arguments.get("to") or "o destinatário informado"
            return f"Vou encaminhar a mensagem para {target}. Quer que eu envie agora?"
        return "Vou responder à mensagem selecionada. Quer que eu envie agora?"
    if tool_name == "computer_action":
        reason = " ".join(str(arguments.get("confirmation_reason") or "").split())[:240]
        if reason:
            return f"Essa interação vai {reason}. Quer que eu continue?"
        return "Essa interação na tela pode produzir um efeito externo. Quer que eu continue?"
    return "Essa ação precisa da sua confirmação. Quer que eu continue?"
