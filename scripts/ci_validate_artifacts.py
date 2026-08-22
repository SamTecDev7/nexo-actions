#!/usr/bin/env python3
"""Validate Nexo Windows release artifacts without executing them."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


PE_HEADER = b"MZ"
MIN_EXE_BYTES = 16 * 1024
SECRET_NAME_RE = re.compile(
    r"(^|[._-])(\.env|id_rsa|id_ed25519|credentials?|secrets?)([._-]|$)", re.I
)
SECRET_CONTENT_RE = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{40,}|github_pat_[A-Za-z0-9_]{50,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16})"
)


def _validate_executable(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"artefato ausente: {path}")
    size = path.stat().st_size
    if size < MIN_EXE_BYTES:
        raise ValueError(f"artefato pequeno demais ({size} bytes): {path}")
    payload = path.read_bytes()
    if not payload.startswith(PE_HEADER):
        raise ValueError(f"artefato não possui cabeçalho PE/MZ: {path}")
    if SECRET_CONTENT_RE.search(payload):
        raise ValueError(f"possível segredo incorporado no artefato: {path}")
    return {
        "name": path.name,
        "bytes": size,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _validate_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if path.name != ".env.example" and SECRET_NAME_RE.search(relative):
            raise ValueError(f"arquivo sensível não pode compor a release: {relative}")


def _verify_manifest(path: Path, report: dict[str, object]) -> None:
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifesto de release inválido: {path}: {exc}") from exc
    if expected.get("source_commit") != report.get("source_commit"):
        raise ValueError("source_commit do manifesto não corresponde ao build atual")
    expected_artifacts = expected.get("artifacts")
    actual_artifacts = report.get("artifacts")
    if not isinstance(expected_artifacts, list) or not isinstance(actual_artifacts, list):
        raise ValueError("lista de artefatos ausente ou inválida no manifesto")
    expected_items = {
        item.get("name"): (item.get("bytes"), item.get("sha256"))
        for item in expected_artifacts
        if isinstance(item, dict)
    }
    actual_items = {
        item.get("name"): (item.get("bytes"), item.get("sha256"))
        for item in actual_artifacts
        if isinstance(item, dict)
    }
    if expected_items != actual_items:
        raise ValueError("hashes/tamanhos não correspondem ao ReleaseManifest.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("dist/windows"))
    parser.add_argument("--require-launcher", action="store_true")
    parser.add_argument("--require-installer", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--verify-manifest", type=Path)
    parser.add_argument("--source-commit", default="")
    args = parser.parse_args()

    directory = args.directory.resolve()
    if not directory.is_dir():
        raise SystemExit(f"diretório de artefatos ausente: {directory}")
    _validate_tree(directory)

    results: list[dict[str, object]] = []
    expected = []
    if args.require_launcher:
        expected.append(directory / "Nexo.exe")
    if args.require_installer:
        expected.append(directory / "NexoSetup.exe")
    for path in expected:
        try:
            results.append(_validate_executable(path))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    source_commit = args.source_commit.strip().lower()
    if source_commit and not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise SystemExit("--source-commit deve ser um SHA Git completo de 40 caracteres")
    report = {
        "ok": True,
        "directory": directory.name,
        "source_commit": source_commit or None,
        "artifacts": results,
    }
    if args.verify_manifest:
        try:
            _verify_manifest(args.verify_manifest.resolve(), report)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    encoded = json.dumps(report, indent=2, sort_keys=True)
    print(encoded)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
