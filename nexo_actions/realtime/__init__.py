"""OpenAI Realtime building blocks for Nexo.

The package is intentionally not wired into the current OVOS skill yet.  It can
be imported and tested independently while the administrator chooses the cloud
provider in the UI.
"""

from .protocol import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_VOICE,
    FunctionCall,
    ProtocolError,
    RealtimeServerError,
)
from .pulse import AEC_SINK, AEC_SOURCE, PulseCapture, PulsePlayback
from .session import (
    RealtimeConnectionError,
    RealtimeSession,
    RealtimeTransport,
    SessionState,
    WebSocketClientTransport,
)
from .coordinator import NexoRealtimeCoordinator

__all__ = [
    "AEC_SINK",
    "AEC_SOURCE",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_SAMPLE_RATE",
    "DEFAULT_VOICE",
    "FunctionCall",
    "ProtocolError",
    "RealtimeServerError",
    "PulseCapture",
    "PulsePlayback",
    "RealtimeConnectionError",
    "RealtimeSession",
    "RealtimeTransport",
    "SessionState",
    "WebSocketClientTransport",
    "NexoRealtimeCoordinator",
]
