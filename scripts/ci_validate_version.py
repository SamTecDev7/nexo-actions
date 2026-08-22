#!/usr/bin/env python3
"""Ensure source versions and a release tag describe the same Nexo release."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def _capture(path: Path, pattern: str, label: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    if not match:
        raise SystemExit(f"não foi possível ler {label} em {path}")
    return match.group(1)


def _tag_version(tag: str) -> str:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("a tag deve usar exatamente vMAJOR.MINOR.PATCH")
    return tag[1:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default="")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()

    package_version = _capture(
        root / "setup.py", r'^\s*version=["\']([^"\']+)["\']', "versão Python"
    )
    launcher_version = _capture(
        root / "windows/NexoLauncher.cs",
        r'AssemblyInformationalVersion\("([^"]+)"\)',
        "versão do launcher",
    )
    if package_version != launcher_version:
        raise SystemExit(
            f"versões divergentes: setup.py={package_version}, launcher={launcher_version}"
        )

    installer_source = root / "installer/windows/NexoSetup.cs"
    if installer_source.is_file():
        installer_file_version = _capture(
            installer_source,
            r'AssemblyFileVersion\("([0-9]+\.[0-9]+\.[0-9]+)(?:\.[0-9]+)?"\)',
            "versão do instalador",
        )
        if package_version != installer_file_version:
            raise SystemExit(
                "versões divergentes: "
                f"setup.py={package_version}, installer={installer_file_version}"
            )

    install_script = root / "installer/windows/Install-Nexo.ps1"
    if install_script.is_file():
        receipt_version = _capture(
            install_script,
            r"^\s*version\s*=\s*'([^']+)'",
            "versão do recibo de instalação",
        )
        if package_version != receipt_version:
            raise SystemExit(
                "versões divergentes: "
                f"setup.py={package_version}, installation.json={receipt_version}"
            )

    if args.tag:
        try:
            tag_version = _tag_version(args.tag)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if tag_version != package_version:
            raise SystemExit(
                f"tag {args.tag} não corresponde à versão do código {package_version}"
            )
    print(f"version={package_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
