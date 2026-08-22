"""Install the optional official Chatterbox pt-BR runtime and verified assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_ROOT = Path.home() / ".local" / "share" / "nexo" / "neural-tts"
SPACE_ID = "ResembleAI/Chatterbox-Multilingual-TTS-pt-br"
MODEL_ID = "ResembleAI/Chatterbox-Multilingual-pt-br"
BASE_MODEL_ID = "ResembleAI/chatterbox"
KOKORO_MODEL_ID = "hexgrad/Kokoro-82M"
PROMPT_URL = (
    "https://storage.googleapis.com/chatterbox-demo-samples/"
    "mtl-v3-single-language-prompts/pt-br/pt_br_f2.wav"
)
USER_AGENT = "Nexo-Neural-TTS-Installer/1.0"
_LAST_PROGRESS: tuple[int, str] | None = None


def progress(percent: int, message: str) -> None:
    global _LAST_PROGRESS
    update = (max(0, min(100, int(percent))), message)
    if update == _LAST_PROGRESS:
        return
    _LAST_PROGRESS = update
    print(f"PROGRESS {update[0]} {update[1]}", flush=True)


def _api(kind: str, identifier: str) -> dict:
    safe_id = "/".join(urllib.parse.quote(part, safe="") for part in identifier.split("/"))
    url = f"https://huggingface.co/api/{kind}s/{safe_id}?blobs=true"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read(10_000_000).decode("utf-8"))


def _download(
    url: str,
    destination: Path,
    *,
    expected_size: int = 0,
    expected_sha256: str = "",
    maximum: int,
    on_chunk=None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    downloaded = 0
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            declared = int(response.headers.get("Content-Length") or expected_size or 0)
            if declared > maximum:
                raise RuntimeError(f"Arquivo excede o limite seguro: {destination.name}")
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                downloaded += len(block)
                if downloaded > maximum:
                    raise RuntimeError(f"Arquivo excede o limite seguro: {destination.name}")
                if on_chunk:
                    on_chunk(downloaded, declared or expected_size)
            output.flush()
            os.fsync(output.fileno())
        if expected_size and downloaded != expected_size:
            raise RuntimeError(
                f"Tamanho inválido para {destination.name}: {downloaded} != {expected_size}"
            )
        if expected_sha256 and digest.hexdigest() != expected_sha256.casefold():
            raise RuntimeError(f"SHA-256 inválido para {destination.name}")
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _run(command: list[str], label: str, timeout: int = 1800) -> None:
    progress(0, label)
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    if process.returncode:
        tail = "\n".join((process.stdout or "").splitlines()[-30:])
        raise RuntimeError(f"{label} falhou:\n{tail}")


def _install_environment(root: Path, project_root: Path) -> Path:
    venv = root.parent / "neural-tts-venv"
    python = venv / "bin" / "python"
    if not python.exists():
        progress(4, "Criando ambiente isolado da voz neural")
        _run([sys.executable, "-m", "venv", str(venv)], "Criação do ambiente", timeout=180)
    marker = venv / ".nexo-dependencies-v4"
    if not marker.exists():
        progress(8, "Instalando PyTorch CUDA 12.8")
        _run([
            str(python), "-m", "pip", "install", "--upgrade",
            "pip", "setuptools==80.9.0", "wheel",
        ], "Atualização do instalador", timeout=300)
        _run([
            str(python), "-m", "pip", "install",
            "--extra-index-url", "https://download.pytorch.org/whl/cu128",
            "torch==2.8.0", "torchaudio==2.8.0",
        ], "Instalação do PyTorch", timeout=1800)
        progress(24, "Instalando o sintetizador neural")
        _run([
            str(python), "-m", "pip", "install",
            "numpy==1.26.0", "resampy==0.4.3", "librosa==0.10.0",
            "s3tokenizer", "transformers==4.46.3", "diffusers==0.29.0",
            "omegaconf==2.3.0", "resemble-perth==1.0.1", "silero-vad==5.1.2",
            "conformer==0.3.2", "safetensors", "kokoro==0.9.4", "soundfile",
        ], "Instalação das dependências Chatterbox", timeout=1800)
        marker.write_text("official-space-requirements-v4\n", encoding="utf-8")
    progress(34, "Registrando o runtime do Nexo")
    _run([
        str(python), "-m", "pip", "install", "--no-deps", "-e", str(project_root),
    ], "Registro do runtime", timeout=180)
    return python


def _download_source(root: Path) -> str:
    metadata = _api("space", SPACE_ID)
    revision = str(metadata.get("sha") or "main")
    source = root / "source"
    files = [
        item for item in metadata.get("siblings", [])
        if str(item.get("rfilename") or "").startswith("chatterbox/")
        and str(item.get("rfilename") or "").endswith(".py")
    ]
    for index, item in enumerate(files, 1):
        name = str(item["rfilename"])
        target = source / name
        if target.is_file() and (not item.get("size") or target.stat().st_size == int(item["size"])):
            continue
        encoded = urllib.parse.quote(name, safe="/")
        url = f"https://huggingface.co/spaces/{SPACE_ID}/resolve/{revision}/{encoded}"
        _download(url, target, expected_size=int(item.get("size") or 0), maximum=5_000_000)
        progress(35 + int(index / max(1, len(files)) * 5), "Baixando código oficial pt-BR")
    return revision


def _model_files(identifier: str, names: set[str]) -> tuple[str, list[dict]]:
    metadata = _api("model", identifier)
    revision = str(metadata.get("sha") or "main")
    return revision, [item for item in metadata.get("siblings", []) if item.get("rfilename") in names]


def _download_models(root: Path) -> dict[str, str]:
    assembled = root / "assembled"
    pt_revision, pt_files = _model_files(
        MODEL_ID,
        {"t3_pt_br.safetensors", "s3gen_v3.pt", "grapheme_mtl_merged_expanded_v1.json"},
    )
    base_revision, base_files = _model_files(
        BASE_MODEL_ID,
        {"ve.pt", "conds.pt", "grapheme_mtl_merged_expanded_v1.json"},
    )
    selected: list[tuple[str, str, dict]] = []
    for item in base_files:
        if item["rfilename"] != "grapheme_mtl_merged_expanded_v1.json":
            selected.append((BASE_MODEL_ID, base_revision, item))
    selected.extend((MODEL_ID, pt_revision, item) for item in pt_files)
    total = sum(int(item.get("size") or 0) for _repo, _rev, item in selected)
    completed = 0
    for repo, revision, item in selected:
        name = str(item["rfilename"])
        expected = int(item.get("size") or 0)
        sha = str((item.get("lfs") or {}).get("sha256") or "")
        destination = assembled / name
        if destination.is_file() and destination.stat().st_size == expected:
            completed += expected
            continue
        url = f"https://huggingface.co/{repo}/resolve/{revision}/{urllib.parse.quote(name, safe='/')}"
        start = completed
        _download(
            url,
            destination,
            expected_size=expected,
            expected_sha256=sha,
            maximum=2_300_000_000,
            on_chunk=lambda current, _declared, base=start: progress(
                42 + int((base + current) / max(1, total) * 48),
                f"Baixando e validando {name}",
            ),
        )
        completed += expected
    return {"pt_br": pt_revision, "base": base_revision}


def _download_kokoro(root: Path) -> str:
    names = {
        "config.json",
        "kokoro-v1_0.pth",
        "voices/pf_dora.pt",
        "voices/pm_alex.pt",
        "voices/pm_santa.pt",
    }
    revision, files = _model_files(KOKORO_MODEL_ID, names)
    if {str(item.get("rfilename") or "") for item in files} != names:
        raise RuntimeError("O pacote oficial Kokoro pt-BR está incompleto")
    total = sum(int(item.get("size") or 0) for item in files)
    completed = 0
    for item in files:
        name = str(item["rfilename"])
        expected = int(item.get("size") or 0)
        sha = str((item.get("lfs") or {}).get("sha256") or "")
        destination = root / "kokoro" / name
        if destination.is_file() and destination.stat().st_size == expected:
            completed += expected
            continue
        url = (
            f"https://huggingface.co/{KOKORO_MODEL_ID}/resolve/{revision}/"
            f"{urllib.parse.quote(name, safe='/')}"
        )
        start = completed
        _download(
            url,
            destination,
            expected_size=expected,
            expected_sha256=sha,
            maximum=500_000_000,
            on_chunk=lambda current, _declared, base=start: progress(
                90 + int((base + current) / max(1, total) * 2),
                "Baixando e validando vozes rápidas Kokoro",
            ),
        )
        completed += expected
    return revision


def _install_prompt(root: Path) -> None:
    destination = root / "prompts" / "pt_br_f2.wav"
    if not destination.is_file():
        progress(92, "Baixando identidade vocal brasileira")
        _download(PROMPT_URL, destination, maximum=20_000_000)
    if destination.read_bytes()[:4] != b"RIFF":
        raise RuntimeError("A referência oficial de voz não é um WAV válido")


def _write_marker(root: Path, payload: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".installed-", dir=str(root))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, root / "installed.json")
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def install(root: Path, project_root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _install_environment(root, project_root)
    source_revision = _download_source(root)
    model_revisions = _download_models(root)
    kokoro_revision = _download_kokoro(root)
    _install_prompt(root)
    _write_marker(root, {
        "engine": "chatterbox-v3-pt-br",
        "source": SPACE_ID,
        "source_revision": source_revision,
        "model": MODEL_ID,
        "model_revision": model_revisions["pt_br"],
        "base_revision": model_revisions["base"],
        "kokoro_model": KOKORO_MODEL_ID,
        "kokoro_revision": kokoro_revision,
    })
    progress(100, "Voz Neural pt-BR instalada e verificada")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    install(Path(args.root).expanduser(), Path(args.project_root).expanduser().resolve())


if __name__ == "__main__":
    main()
