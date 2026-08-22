"""Voice tools for diagnosing and reapplying Nexo's external microphone."""

from __future__ import annotations

AUDIO_INPUT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "audio_input_status",
            "description": (
                "Verifica o fluxo real do microfone usado pelo Nexo, incluindo "
                "a placa externa, a ponte WSLg e o cancelamento de eco. Use quando "
                "o usuário disser que o Nexo não está ouvindo ou que o microfone sumiu."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repair_audio_input",
            "description": (
                "Reaplica e recupera o microfone externo do Nexo. Reconstrói a "
                "ponte WSLg, o cancelamento de eco e o listener somente quando necessário."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


def audio_input_status():
    from .audio_recovery import AudioRecovery

    return AudioRecovery().health(probe_audio=True, write_status=True)


def repair_audio_input(*, job_context=None):
    from .audio_recovery import AudioRecovery

    if job_context:
        job_context.report_progress(10, "Verificando o fluxo do microfone")
        job_context.raise_if_cancelled()
    result = AudioRecovery().repair(reason="comando-de-voz")
    if job_context:
        job_context.report_progress(100, "Microfone reaplicado")
    return result
