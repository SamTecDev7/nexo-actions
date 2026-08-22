"""Typed registry used to expose and execute every Nexo tool."""

from __future__ import annotations

import copy
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence


LOG = logging.getLogger("nexo.tools")
RISK_LEVELS = {"read_only", "low", "medium", "high"}
LOG_POLICIES = {"standard", "metadata_only", "none"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: Callable[..., Any] = field(repr=False)
    timeout: float = 15.0
    category: str = "general"
    risk_level: str = "read_only"
    requires_confirmation: bool = False
    long_running: bool = False
    log_policy: str = "standard"
    redact_fields: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.name or not callable(self.handler):
            raise ValueError("ToolSpec exige nome e handler callable")
        if self.risk_level not in RISK_LEVELS:
            raise ValueError(f"risk_level inválido: {self.risk_level}")
        if self.log_policy not in LOG_POLICIES:
            raise ValueError(f"log_policy inválido: {self.log_policy}")
        if float(self.timeout) <= 0:
            raise ValueError("timeout deve ser positivo")

    @classmethod
    def from_ollama_schema(cls, schema: Mapping[str, Any], handler: Callable, **metadata: Any):
        function = schema.get("function", {})
        return cls(
            name=function["name"],
            description=function.get("description", ""),
            parameters=function.get("parameters", {"type": "object", "properties": {}}),
            handler=handler,
            **metadata,
        )

    def ollama_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": copy.deepcopy(dict(self.parameters)),
            },
        }


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec] = ()):
        self._specs: Dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Tool duplicada: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ValueError(f"Tool desconhecida: {name}") from exc

    def names(self) -> list[str]:
        return list(self._specs)

    def schemas(self, categories: Optional[Iterable[str]] = None) -> list[Dict[str, Any]]:
        allowed = set(categories) if categories is not None else None
        return [
            spec.ollama_schema()
            for spec in self._specs.values()
            if allowed is None or spec.category in allowed
        ]

    def specs(self, categories: Optional[Iterable[str]] = None) -> list[ToolSpec]:
        allowed = set(categories) if categories is not None else None
        return [
            spec for spec in self._specs.values()
            if allowed is None or spec.category in allowed
        ]

    def handlers(self) -> Dict[str, Callable[..., Any]]:
        return {name: spec.handler for name, spec in self._specs.items()}

    def execute(
        self,
        name: str,
        arguments: Optional[Mapping[str, Any]] = None,
        *,
        job_context: Any = None,
    ) -> Any:
        spec = self.get(name)
        args = dict(arguments or {})
        self.validate_arguments(spec, args)
        safe_args = self._safe_log_value(spec, args)
        LOG.info("TOOL_ARGS name=%s arguments=%s", name, safe_args)
        started = time.perf_counter()
        LOG.info("TOOL_STARTED name=%s category=%s", name, spec.category)
        try:
            if job_context is not None and "job_context" in inspect.signature(spec.handler).parameters:
                result = spec.handler(**args, job_context=job_context)
            else:
                result = spec.handler(**args)
        except Exception:
            LOG.exception(
                "TOOL_FAILED name=%s tool_ms=%.1f",
                name,
                (time.perf_counter() - started) * 1000,
            )
            raise
        LOG.info(
            "TOOL_RESULT name=%s tool_ms=%.1f result=%s",
            name,
            (time.perf_counter() - started) * 1000,
            self._safe_log_value(spec, result),
        )
        return result

    @classmethod
    def validate_arguments(cls, spec: ToolSpec, arguments: Mapping[str, Any]) -> None:
        schema = spec.parameters
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema inválido para {spec.name}: raiz não é object")
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        missing = sorted(required - set(arguments))
        if missing:
            raise ValueError(f"Argumentos obrigatórios ausentes em {spec.name}: {missing}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(arguments) - set(properties))
            if extra:
                raise ValueError(f"Argumentos desconhecidos em {spec.name}: {extra}")
        for key, value in arguments.items():
            if key in properties:
                cls._validate_value(spec.name, key, value, properties[key])

    @classmethod
    def _validate_value(cls, tool: str, key: str, value: Any, schema: Mapping[str, Any]) -> None:
        expected = schema.get("type")
        valid = True
        if expected == "string":
            valid = isinstance(value, str)
        elif expected == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif expected == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif expected == "boolean":
            valid = isinstance(value, bool)
        elif expected == "array":
            valid = isinstance(value, list)
        elif expected == "object":
            valid = isinstance(value, dict)
        if not valid:
            raise ValueError(f"{tool}.{key} deve ser {expected}")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"{tool}.{key} fora dos valores permitidos")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise ValueError(f"{tool}.{key} abaixo do mínimo")
            if "maximum" in schema and value > schema["maximum"]:
                raise ValueError(f"{tool}.{key} acima do máximo")

    @staticmethod
    def _safe_log_value(spec: ToolSpec, value: Any) -> Any:
        if spec.log_policy == "none":
            return "<omitted>"
        if spec.log_policy == "metadata_only":
            if isinstance(value, Mapping):
                return {"keys": sorted(str(key) for key in value.keys())}
            return {"type": type(value).__name__}
        redacted = {field.casefold() for field in spec.redact_fields}

        def clean(item: Any) -> Any:
            if isinstance(item, Mapping):
                return {
                    key: ("<redacted>" if str(key).casefold() in redacted else clean(child))
                    for key, child in item.items()
                }
            if isinstance(item, (list, tuple)):
                return [clean(child) for child in item]
            return item

        return clean(value)
