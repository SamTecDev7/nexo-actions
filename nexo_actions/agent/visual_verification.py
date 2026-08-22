"""Restricted post-confirmation visual verification for Windows actions."""

from __future__ import annotations

from typing import Any, Mapping

from ..llm.openai_provider import OpenAIResponsesClient
from ..tool_registry import ToolRegistry, ToolSpec
from .orchestrator import AgentOrchestrator


def verify_visual_effect(
    agent: Any,
    *,
    action: Mapping[str, Any],
    observe_spec: ToolSpec,
) -> dict[str, Any]:
    """Observe once and require a structured visual verdict without more actions."""
    source_client = getattr(agent, "client", None)
    if (
        source_client is None
        or str(getattr(source_client, "provider_name", "")).casefold() != "openai"
    ):
        return {
            "verified": False,
            "effect_visible": False,
            "observation_completed": False,
            "summary": "A ação foi enviada, mas a verificação visual exige o modo OpenAI.",
        }

    state = {"observed": False}

    def observe_once(
        scope: str = "active_window",
        max_width: int = 1280,
        max_height: int = 720,
    ) -> Any:
        result = observe_spec.handler(
            scope=scope,
            max_width=max_width,
            max_height=max_height,
        )
        state["observed"] = bool(
            isinstance(result, Mapping)
            and result.get("observation_id")
            and (
                result.get("image_base64")
                or result.get("screenshot_base64")
                or result.get("png_base64")
            )
        )
        return result

    def report_verification(effect_visible: bool, summary: str) -> dict[str, Any]:
        if not state["observed"]:
            raise RuntimeError("Observe a tela antes de registrar o resultado")
        return {
            "ok": True,
            "effect_visible": bool(effect_visible),
            "summary": " ".join(str(summary).split())[:320],
        }

    restricted_observe = ToolSpec(
        name=observe_spec.name,
        description=observe_spec.description,
        parameters=observe_spec.parameters,
        handler=observe_once,
        timeout=observe_spec.timeout,
        category=observe_spec.category,
        risk_level="read_only",
        log_policy="none",
    )
    report = ToolSpec(
        name="report_visual_verification",
        description=(
            "Registra se a captura posterior mostra evidência visual do efeito "
            "esperado. Deve ser chamada somente depois de observe_desktop."
        ),
        parameters={
            "type": "object",
            "properties": {
                "effect_visible": {"type": "boolean"},
                "summary": {"type": "string"},
            },
            "required": ["effect_visible", "summary"],
            "additionalProperties": False,
        },
        handler=report_verification,
        timeout=5,
        category="desktop",
        risk_level="read_only",
        log_policy="none",
    )

    # Use a separate client so the verification cannot inherit an unrelated
    # Responses continuation. Web search is deliberately unavailable here.
    client = OpenAIResponsesClient(
        model=str(getattr(source_client, "model", "gpt-5.6-sol")),
        reasoning_effort=str(getattr(source_client, "reasoning_effort", "low")),
        web_search=False,
        store=bool(getattr(source_client, "store", True)),
        max_output_tokens=2000,
        usage_ledger=getattr(source_client, "usage_ledger", None),
    )
    verifier = AgentOrchestrator(
        ToolRegistry([restricted_observe, report]),
        client=client,
        max_steps=2,
        timeout_seconds=50,
    )
    action_name = str(action.get("action") or "interação")
    expected = " ".join(str(action.get("confirmation_reason") or "").split())[:240]
    result = verifier.run(
        "Verifique o efeito visual de uma ação já executada. "
        f"Tipo da ação: {action_name}. "
        + (f"Efeito esperado: {expected}. " if expected else "")
        + "Primeiro chame observe_desktop. Depois chame obrigatoriamente "
        "report_visual_verification. Não clique, não digite e não execute "
        "nenhuma outra ação."
    )
    report_payload: Mapping[str, Any] | None = None
    for entry in result.get("tool_results") or []:
        if entry.get("name") == "report_visual_verification":
            candidate = entry.get("result")
            if isinstance(candidate, Mapping):
                report_payload = candidate
    completed = bool(state["observed"] and report_payload)
    effect_visible = bool(
        completed and report_payload and report_payload.get("effect_visible") is True
    )
    summary = (
        str(report_payload.get("summary") or "")
        if report_payload
        else "A captura posterior não produziu um veredito visual confiável."
    )
    return {
        "verified": effect_visible,
        "effect_visible": effect_visible,
        "observation_completed": completed,
        "summary": " ".join(summary.split())[:320],
    }
