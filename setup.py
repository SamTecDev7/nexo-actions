from setuptools import setup, find_packages

setup(
    name="nexo-actions",
    version="2.0.0",
    description="Nexo OpenAI-first realtime voice and Windows computer agent",
    python_requires=">=3.10,<3.13",
    packages=find_packages(),
    install_requires=["websocket-client>=1.8,<2"],
    include_package_data=True,
    package_data={
        "nexo_actions": [
            "locale/*/*.voc",
            "locale/*/*.intent",
            "locale/*/*.dialog",
            "ui/static/*.html",
            "ui/static/*.css",
            "ui/static/*.js"
        ]
    },
    entry_points={
        "opm.skill": [
            "nexo-actions.samtec=nexo_actions.skill:NexoActionsSkill"
        ],
        "mycroft.plugin.tts": [
            "nexo-hybrid-tts=nexo_actions.tts_plugin:NexoHybridTTS"
        ],
        "mycroft.plugin.tts.config": [
            "nexo-hybrid-tts.config=nexo_actions.tts_plugin:NexoHybridTTSConfig"
        ],
        "opm.stt": [
            "nexo-smart-stt=nexo_actions.stt_plugin:NexoSmartSTT"
        ],
        "opm.stt.config": [
            "nexo-smart-stt.config=nexo_actions.stt_plugin:NexoSmartSTTConfig"
        ],
        "console_scripts": [
            "nexo-ui=nexo_actions.ui.server:main",
            "nexo-neural-tts=nexo_actions.neural_tts_server:main",
            "nexo-install-neural-tts=nexo_actions.neural_tts_install:main"
        ],
    }
)
