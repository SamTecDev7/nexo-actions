"""Safe filesystem tools for the Windows profile from WSL."""

from __future__ import annotations

import heapq
import hashlib
import hmac
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

from .config import known_directories, windows_to_wsl, wsl_to_windows
from .windows_bridge import WindowsBridgeClient


EXPLORER = "/mnt/c/Windows/explorer.exe"
BACKUP_ROOT = Path.home() / ".local" / "share" / "nexo" / "file-backups"
MAX_TEXT_BYTES = 1_000_000
TEXT_SUFFIXES = {
    ".bat", ".c", ".cjs", ".cpp", ".cs", ".css", ".csv", ".go",
    ".h", ".hpp", ".htm", ".html", ".ini", ".java", ".js", ".json",
    ".jsx", ".md", ".mjs", ".php", ".ps1", ".py", ".rb", ".rs",
    ".scss", ".sh", ".sql", ".svelte", ".toml", ".ts", ".tsx", ".txt",
    ".vue", ".xml", ".yaml", ".yml",
}
TEXT_FILENAMES = {"dockerfile", "makefile", "procfile"}
PROTECTED_PATH_FRAGMENTS = {
    "/.git/", "/.gnupg/", "/.ssh/", "/appdata/", "/credentials/",
    "/google/chrome/", "/microsoft/edge/", "/mozilla/",
}


def _spoken_filename_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", "", normalized.casefold())


def _resolve_path(value: str, *, must_exist: bool = False) -> Path:
    raw = str(value).strip().strip('"')
    if not raw:
        raise ValueError("Caminho não pode ser vazio")
    aliases = known_directories()
    normalized = raw.replace("\\", "/")
    alias_name, separator, remainder = normalized.partition("/")
    if alias_name.casefold() in aliases:
        base = windows_to_wsl(aliases[alias_name.casefold()])
        raw_path = (
            base.joinpath(*[part for part in remainder.split("/") if part])
            if separator else base
        )
    else:
        raw_path = windows_to_wsl(raw)
    raw_path = raw_path.expanduser()
    if not raw_path.is_absolute():
        raise ValueError("Use um caminho absoluto ou diretório conhecido")
    path = raw_path.resolve(strict=False)
    if must_exist and not path.exists() and path.parent.is_dir():
        # STT commonly transcribes ``formulario.html`` as ``formulário .html``.
        # Resolve only an unambiguous accent/case/spacing equivalent in the
        # requested directory; never perform a broad fuzzy filesystem search.
        expected = _spoken_filename_key(path.name)
        matches = [
            child for child in path.parent.iterdir()
            if _spoken_filename_key(child.name) == expected
        ]
        if len(matches) == 1:
            path = matches[0].resolve(strict=False)
    if must_exist and not path.exists():
        raise FileNotFoundError(str(path))
    return path


def _allowed_workspace_roots() -> tuple[Path, ...]:
    roots = {Path(__file__).resolve().parents[1]}
    for value in known_directories().values():
        candidate = windows_to_wsl(value).expanduser()
        if candidate.is_absolute():
            roots.add(candidate.resolve(strict=False))
    return tuple(sorted(roots, key=lambda item: len(str(item)), reverse=True))


def _assert_text_workspace(target: Path, *, must_exist: bool = True) -> Path:
    target = target.resolve(strict=must_exist)
    if not any(target == root or root in target.parents for root in _allowed_workspace_roots()):
        raise PermissionError(
            "O arquivo precisa estar no perfil do usuário ou em um projeto conhecido"
        )
    lowered = "/" + str(target).replace("\\", "/").strip("/").casefold() + "/"
    if any(fragment in lowered for fragment in PROTECTED_PATH_FRAGMENTS):
        raise PermissionError(
            "Esse local pode conter credenciais ou dados privados e não pode ser lido pelo Nexo"
        )
    if target.name.casefold() in {
        ".env", ".git-credentials", "id_rsa", "id_ed25519",
    }:
        raise PermissionError("Arquivos de credenciais não podem ser lidos pelo Nexo")
    if (
        target.suffix.casefold() not in TEXT_SUFFIXES
        and target.name.casefold() not in TEXT_FILENAMES
    ):
        raise ValueError("Esse formato não é um arquivo de texto ou código permitido")
    if must_exist:
        if not target.is_file():
            raise IsADirectoryError(str(target))
        if target.stat().st_size > MAX_TEXT_BYTES:
            raise ValueError("O arquivo excede o limite seguro de 1 MB")
    return target


def _read_text_payload(target: Path) -> tuple[str, str, bytes]:
    raw = target.read_bytes()
    if b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("O arquivo parece binário")
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding), encoding, raw
        except UnicodeDecodeError:
            continue
    raise ValueError("Não consegui identificar a codificação desse arquivo")


def _content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _backup_file(target: Path, raw: bytes) -> Path:
    identity = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:16]
    folder = BACKUP_ROOT / identity
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = folder / f"{stamp}-{target.name}"
    suffix = 1
    while backup.exists():
        backup = folder / f"{stamp}-{suffix}-{target.name}"
        suffix += 1
    with backup.open("xb") as handle:
        handle.write(raw)
    return backup


def _atomic_rewrite(target: Path, content: str, original: bytes) -> dict[str, Any]:
    encoded = str(content).encode("utf-8")
    if len(encoded) > MAX_TEXT_BYTES:
        raise ValueError("O conteúdo excede o limite seguro de 1 MB")
    backup = _backup_file(target, original)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, target.stat().st_mode)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return {
        "path": wsl_to_windows(target),
        "backup_path": str(backup),
        "bytes": len(encoded),
        "sha256": _content_hash(encoded),
    }


def read_text_file(
    path: str,
    start_line: int = 1,
    end_line: int = 0,
    max_chars: int = 12000,
) -> dict[str, Any]:
    target = _assert_text_workspace(_resolve_path(path, must_exist=True))
    content, encoding, raw = _read_text_payload(target)
    lines = content.splitlines(keepends=True)
    start = max(1, int(start_line))
    end = int(end_line) if int(end_line) > 0 else len(lines)
    if end < start:
        raise ValueError("A linha final precisa ser igual ou posterior à inicial")
    maximum = max(500, min(int(max_chars), 30000))
    selected = "".join(lines[start - 1:end])
    returned = selected[:maximum]
    return {
        "ok": True,
        "tool": "read_text_file",
        "path": wsl_to_windows(target),
        "name": target.name,
        "encoding": encoding,
        "sha256": _content_hash(raw),
        "total_lines": len(lines),
        "start_line": start,
        "end_line": min(end, len(lines)),
        "content": returned,
        "complete": len(returned) == len(selected) and end >= len(lines),
        "truncated": len(returned) < len(selected),
    }


def rewrite_text_file(path: str, content: str, expected_sha256: str) -> dict[str, Any]:
    target = _assert_text_workspace(_resolve_path(path, must_exist=True))
    _current, _encoding, raw = _read_text_payload(target)
    actual = _content_hash(raw)
    if not expected_sha256 or not hmac.compare_digest(actual, str(expected_sha256)):
        return {
            "ok": False,
            "tool": "rewrite_text_file",
            "conflict": True,
            "reason": "file_changed",
            "path": wsl_to_windows(target),
            "sha256": actual,
        }
    result = _atomic_rewrite(target, str(content), raw)
    return {"ok": True, "tool": "rewrite_text_file", **result}


def replace_text_in_file(
    path: str,
    search: str,
    replacement: str,
    expected_sha256: str,
    replace_all: bool = False,
) -> dict[str, Any]:
    target = _assert_text_workspace(_resolve_path(path, must_exist=True))
    current, _encoding, raw = _read_text_payload(target)
    actual = _content_hash(raw)
    if not expected_sha256 or not hmac.compare_digest(actual, str(expected_sha256)):
        return {
            "ok": False,
            "tool": "replace_text_in_file",
            "conflict": True,
            "reason": "file_changed",
            "path": wsl_to_windows(target),
            "sha256": actual,
        }
    needle = str(search)
    if not needle:
        raise ValueError("O trecho procurado não pode ser vazio")
    occurrences = current.count(needle)
    if not occurrences:
        return {
            "ok": False,
            "tool": "replace_text_in_file",
            "conflict": True,
            "reason": "text_not_found",
            "path": wsl_to_windows(target),
            "sha256": actual,
        }
    if occurrences > 1 and not replace_all:
        return {
            "ok": False,
            "tool": "replace_text_in_file",
            "conflict": True,
            "reason": "text_is_ambiguous",
            "occurrences": occurrences,
            "path": wsl_to_windows(target),
            "sha256": actual,
        }
    updated = current.replace(needle, str(replacement), -1 if replace_all else 1)
    result = _atomic_rewrite(target, updated, raw)
    return {
        "ok": True,
        "tool": "replace_text_in_file",
        "replacements": occurrences if replace_all else 1,
        **result,
    }


def _safe_filename(filename: str) -> str:
    name = str(filename).strip().strip('"')
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError("Nome de arquivo inválido")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError("Nome de arquivo contém caracteres inválidos no Windows")
    stem = name.split(".", 1)[0].rstrip(" .").casefold()
    if stem in {
        "con", "prn", "aux", "nul", "clock$",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }:
        raise ValueError("Nome reservado pelo Windows")
    return name.rstrip(" .")


def open_folder(path: str) -> dict[str, Any]:
    target = _resolve_path(path, must_exist=True)
    if not target.is_dir():
        raise NotADirectoryError(str(target))
    windows_path = wsl_to_windows(target)
    subprocess.Popen(
        [EXPLORER, windows_path],
        cwd="/mnt/c",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "tool": "open_folder", "path": windows_path}


def open_file(path: str) -> dict[str, Any]:
    target = _resolve_path(path, must_exist=True)
    if not target.is_file():
        raise IsADirectoryError(str(target))
    windows_path = wsl_to_windows(target)
    os.startfile(windows_path) if hasattr(os, "startfile") else subprocess.Popen(
        [EXPLORER, windows_path],
        cwd="/mnt/c",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "tool": "open_file", "path": windows_path}


def reveal_file(path: str) -> dict[str, Any]:
    target = _resolve_path(path, must_exist=True)
    windows_path = wsl_to_windows(target)
    subprocess.Popen(
        [EXPLORER, "/select,", windows_path],
        cwd="/mnt/c",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "tool": "reveal_file", "path": windows_path}


def create_folder(parent: str, name: str) -> dict[str, Any]:
    parent_path = _resolve_path(parent, must_exist=True)
    if not parent_path.is_dir():
        raise NotADirectoryError(str(parent_path))
    target = parent_path / _safe_filename(name)
    existed = target.exists()
    target.mkdir(parents=False, exist_ok=True)
    return {
        "ok": True,
        "tool": "create_folder",
        "path": wsl_to_windows(target),
        "created": not existed,
    }


def create_text_file(
    directory: str,
    filename: str,
    content: str,
    overwrite: bool = False,
    open_after: bool = True,
) -> dict[str, Any]:
    directory_path = _resolve_path(directory, must_exist=True)
    if not directory_path.is_dir():
        raise NotADirectoryError(str(directory_path))
    encoded = str(content).encode("utf-8")
    if len(encoded) > 1_000_000:
        raise ValueError("Conteúdo de texto excede o limite de 1 MB")
    target = directory_path / _safe_filename(filename)
    if target.exists() and not overwrite:
        return {
            "ok": False,
            "tool": "create_text_file",
            "conflict": True,
            "path": wsl_to_windows(target),
            "reason": "file_exists",
        }
    mode = "w" if overwrite else "x"
    with target.open(mode, encoding="utf-8", newline="") as handle:
        handle.write(str(content))
    windows_path = wsl_to_windows(target)
    if open_after:
        subprocess.Popen(
            [EXPLORER, windows_path],
            cwd="/mnt/c",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return {
        "ok": True,
        "tool": "create_text_file",
        "path": windows_path,
        "bytes": len(encoded),
        "opened": bool(open_after),
    }


def find_files(
    query: str = "",
    scope: str = "profile",
    extension: str = "",
    recent: bool = False,
    limit: int = 10,
    *,
    job_context=None,
    root_override: str | Path | None = None,
    timeout_seconds: float = 15,
) -> dict[str, Any]:
    root = Path(root_override).resolve() if root_override else _resolve_path(scope, must_exist=True)
    normalized_query = str(query).casefold().strip()
    normalized_ext = str(extension).casefold().strip()
    if normalized_ext and not normalized_ext.startswith("."):
        normalized_ext = "." + normalized_ext
    limit = max(1, min(int(limit), 50))
    deadline = time.monotonic() + max(0.01, min(float(timeout_seconds), 30))
    heap: list[tuple[float, str, int, float]] = []
    scanned = 0
    errors = 0
    partial = False
    last_progress = time.monotonic()
    stack = [root]

    while stack:
        if time.monotonic() >= deadline or (job_context and job_context.cancelled):
            partial = True
            break
        directory = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if time.monotonic() >= deadline or (job_context and job_context.cancelled):
                        partial = True
                        stack.clear()
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        scanned += 1
                        name = entry.name.casefold()
                        if normalized_query and normalized_query not in name:
                            continue
                        if normalized_ext and not name.endswith(normalized_ext):
                            continue
                        stat = entry.stat(follow_symlinks=False)
                        relative_depth = len(Path(entry.path).relative_to(root).parts)
                        # Without a recency request, a file directly in the
                        # named folder is the least ambiguous match.  This
                        # keeps Downloads/style.css ahead of unrelated nested
                        # project copies while still returning all candidates.
                        rank = stat.st_mtime if recent else -float(relative_depth)
                        item = (rank, entry.path, stat.st_size, stat.st_mtime)
                        if len(heap) < limit:
                            heapq.heappush(heap, item)
                        elif item > heap[0]:
                            heapq.heapreplace(heap, item)
                    except (OSError, PermissionError):
                        errors += 1
        except (OSError, PermissionError):
            errors += 1

        if job_context and time.monotonic() - last_progress >= 3:
            job_context.report_progress(
                None,
                "Ainda estou procurando.",
                {"files_scanned": scanned},
            )
            last_progress = time.monotonic()

    matches = sorted(heap, reverse=True)
    return {
        "ok": True,
        "tool": "find_files",
        "root": wsl_to_windows(root),
        "query": query,
        "extension": normalized_ext,
        "recent": bool(recent),
        "files": [
            {
                "name": Path(path).name,
                "path": wsl_to_windows(path),
                "size_bytes": size,
                "modified_at": modified,
            }
            for _rank, path, size, modified in matches
        ],
        "found": len(matches),
        "files_scanned": scanned,
        "errors": errors,
        "partial": partial,
        "complete": not partial,
        "cancelled": bool(job_context and job_context.cancelled),
    }


def copy_file(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
    source_path = _resolve_path(source, must_exist=True)
    destination_path = _resolve_path(destination)
    if not source_path.is_file():
        raise ValueError("A origem precisa ser um arquivo")
    if destination_path.is_dir():
        destination_path = destination_path / source_path.name
    if destination_path.exists() and not overwrite:
        return {"ok": False, "conflict": True, "path": wsl_to_windows(destination_path)}
    destination_path.parent.mkdir(parents=False, exist_ok=True)
    shutil.copy2(source_path, destination_path)
    return {
        "ok": True,
        "tool": "copy_file",
        "source": wsl_to_windows(source_path),
        "destination": wsl_to_windows(destination_path),
    }


def move_file(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
    source_path = _resolve_path(source, must_exist=True)
    destination_path = _resolve_path(destination)
    if not source_path.is_file():
        raise ValueError("A origem precisa ser um arquivo")
    if destination_path.is_dir():
        destination_path = destination_path / source_path.name
    if destination_path.exists() and not overwrite:
        return {"ok": False, "conflict": True, "path": wsl_to_windows(destination_path)}
    if destination_path.exists():
        destination_path.unlink()
    destination_path.parent.mkdir(parents=False, exist_ok=True)
    shutil.move(str(source_path), str(destination_path))
    return {
        "ok": True,
        "tool": "move_file",
        "source": wsl_to_windows(source_path),
        "destination": wsl_to_windows(destination_path),
    }


def rename_file(path: str, new_name: str) -> dict[str, Any]:
    source = _resolve_path(path, must_exist=True)
    target = source.with_name(_safe_filename(new_name))
    if target.exists():
        return {"ok": False, "conflict": True, "path": wsl_to_windows(target)}
    source.rename(target)
    return {
        "ok": True,
        "tool": "rename_file",
        "source": wsl_to_windows(source),
        "destination": wsl_to_windows(target),
    }


def _assert_deletable(target: Path, *, expect_directory: bool) -> None:
    target = target.resolve(strict=True)
    if target.is_dir() != expect_directory:
        kind = "pasta" if expect_directory else "arquivo"
        raise ValueError(f"O alvo precisa ser uma {kind}")
    aliases = known_directories()
    project_root = Path(__file__).resolve().parents[1]
    system_roots = {
        project_root,
        windows_to_wsl("C:\\Windows").resolve(strict=False),
        windows_to_wsl("C:\\Program Files").resolve(strict=False),
        windows_to_wsl("C:\\Program Files (x86)").resolve(strict=False),
    }
    protected = {
        Path("/"), Path("/mnt/c"),
        *system_roots,
        windows_to_wsl("C:\\Users").resolve(strict=False),
        *(windows_to_wsl(value).resolve(strict=False) for value in aliases.values()),
    }
    if (
        target in protected
        or any(root in target.parents for root in system_roots)
        or (len(target.parts) <= 3 and str(target).startswith("/mnt/"))
    ):
        raise PermissionError("Esse caminho raiz/protegido não pode ser excluído pelo Nexo")


def _delete_path(path: str, *, permanent: bool, expect_directory: bool) -> dict[str, Any]:
    target = _resolve_path(path, must_exist=True)
    _assert_deletable(target, expect_directory=expect_directory)
    windows_path = wsl_to_windows(target)
    if permanent:
        if expect_directory and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        recycled = False
    else:
        WindowsBridgeClient().call("recycle_path", {"path": windows_path})
        recycled = True
    return {
        "ok": True,
        "path": windows_path,
        "permanent": bool(permanent),
        "recycled": recycled,
    }


def delete_file(path: str, permanent: bool = False) -> dict[str, Any]:
    return {"tool": "delete_file", **_delete_path(path, permanent=permanent, expect_directory=False)}


def delete_folder(path: str, permanent: bool = False) -> dict[str, Any]:
    return {"tool": "delete_folder", **_delete_path(path, permanent=permanent, expect_directory=True)}


FILE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_folder",
            "description": "Abre Downloads, Desktop, Documentos, Nexo ou uma pasta por caminho.",
            "parameters": {
                "type": "object", "properties": {"path": {"type": "string"}},
                "required": ["path"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Procura arquivos por nome, extensão ou data recente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "scope": {"type": "string"},
                    "extension": {"type": "string"},
                    "recent": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_folder",
            "description": "Cria uma pasta dentro de um diretório existente.",
            "parameters": {
                "type": "object",
                "properties": {"parent": {"type": "string"}, "name": {"type": "string"}},
                "required": ["parent", "name"], "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_text_file",
            "description": "Cria um arquivo de texto e opcionalmente o abre no Windows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string"}, "filename": {"type": "string"},
                    "content": {"type": "string"}, "overwrite": {"type": "boolean"},
                    "open_after": {"type": "boolean"},
                },
                "required": ["directory", "filename", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_file", "description": "Abre um arquivo existente no aplicativo padrão.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reveal_file", "description": "Revela e seleciona um arquivo no Explorador.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "copy_file", "description": "Copia um arquivo para outro caminho.",
            "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "overwrite": {"type": "boolean"}}, "required": ["source", "destination"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_file", "description": "Move um arquivo para outro caminho.",
            "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}, "overwrite": {"type": "boolean"}}, "required": ["source", "destination"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_file", "description": "Renomeia um arquivo ou pasta existente.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "new_name": {"type": "string"}}, "required": ["path", "new_name"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file", "description": "Envia um arquivo para a Lixeira; permanent=true apaga definitivamente.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "permanent": {"type": "boolean"}}, "required": ["path"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_folder", "description": "Envia uma pasta para a Lixeira; permanent=true apaga definitivamente.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "permanent": {"type": "boolean"}}, "required": ["path"], "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_text_file",
            "description": (
                "Lê com segurança um arquivo de texto ou código no perfil do usuário "
                "ou em um projeto conhecido. Retorna conteúdo, linhas e sha256."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 0},
                    "max_chars": {"type": "integer", "minimum": 500, "maximum": 30000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rewrite_text_file",
            "description": (
                "Reescreve por completo um arquivo de código já lido. Exige o sha256 "
                "retornado pela leitura e cria backup automático antes da alteração."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                },
                "required": ["path", "content", "expected_sha256"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_text_in_file",
            "description": (
                "Substitui um trecho exato em arquivo de código já lido. Exige sha256, "
                "detecta ambiguidades e cria backup automático."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "search": {"type": "string"},
                    "replacement": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "search", "replacement", "expected_sha256"],
                "additionalProperties": False,
            },
        },
    },
]
