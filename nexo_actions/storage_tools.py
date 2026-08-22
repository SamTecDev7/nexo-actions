"""Fast, cancellable, cached storage analysis over Windows files from WSL."""

from __future__ import annotations

import copy
import base64
import heapq
import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, Optional, Tuple


POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
ALLOWED_SCOPES = {"profile", "downloads", "desktop", "documents"}
DEFAULT_SCAN_TIMEOUT = 20.0
DEFAULT_CACHE_TTL = 120.0
DEFAULT_PROGRESS_AFTER = 3.0
_ROOT_CACHE_TTL = 300.0


FIND_LARGE_FILES_TOOL = {
    "type": "function",
    "function": {
        "name": "find_large_files",
        "description": (
            "Procura os maiores arquivos no perfil do Windows. Pode analisar "
            "Downloads, Desktop, Documentos ou todo o perfil do usuário."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": sorted(ALLOWED_SCOPES)},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "min_size_mb": {"type": "integer", "minimum": 1, "maximum": 100000},
            },
            "required": ["scope", "limit", "min_size_mb"],
            "additionalProperties": False,
        },
    },
}


FIND_LARGE_FOLDERS_TOOL = {
    "type": "function",
    "function": {
        "name": "find_large_folders",
        "description": (
            "Analisa quais pastas estão ocupando mais espaço em Downloads, "
            "Desktop, Documentos ou no perfil do usuário."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": sorted(ALLOWED_SCOPES)},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["scope", "limit"],
            "additionalProperties": False,
        },
    },
}


STORAGE_TOOLS = [FIND_LARGE_FILES_TOOL, FIND_LARGE_FOLDERS_TOOL]
_ROOT_CACHE: Dict[str, Any] = {"timestamp": 0.0, "paths": {}}
_ROOT_LOCK = threading.Lock()
_SCAN_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_SCAN_LOCK = threading.Lock()


def _validate_scope(scope: str) -> str:
    scope = str(scope).strip().casefold()
    if scope not in ALLOWED_SCOPES:
        raise ValueError(f"scope inválido: {scope}")
    return scope


def _discover_windows_roots() -> Dict[str, str]:
    """Resolve redirected known folders once using PowerShell 5.1-compatible code."""
    now = time.monotonic()
    with _ROOT_LOCK:
        if _ROOT_CACHE["paths"] and now - _ROOT_CACHE["timestamp"] < _ROOT_CACHE_TTL:
            return dict(_ROOT_CACHE["paths"])

    script = r'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$profile = $env:USERPROFILE
$shell = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -ErrorAction SilentlyContinue
$downloads = $null
if ($shell) {
    $property = $shell.PSObject.Properties['{374DE290-123F-4565-9164-39C4925E467B}']
    if ($property) { $downloads = [Environment]::ExpandEnvironmentVariables([string]$property.Value) }
}
if (-not $downloads) { $downloads = Join-Path $profile 'Downloads' }
[ordered]@{
    profile = $profile
    downloads = $downloads
    desktop = [Environment]::GetFolderPath('Desktop')
    documents = [Environment]::GetFolderPath('MyDocuments')
} | ConvertTo-Json -Compress
'''
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Falha ao descobrir pastas do Windows")
    raw = proc.stdout.strip().lstrip("\ufeff")
    paths = json.loads(raw)
    with _ROOT_LOCK:
        _ROOT_CACHE["timestamp"] = now
        _ROOT_CACHE["paths"] = dict(paths)
    return paths


def _windows_to_wsl(path: str) -> Path:
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", path.strip())
    if not match:
        return Path(path)
    drive, rest = match.groups()
    return Path("/mnt") / drive.casefold() / Path(rest.replace("\\", "/"))


def resolve_storage_root(scope: str) -> Tuple[Path, str]:
    scope = _validate_scope(scope)
    windows_path = _discover_windows_roots().get(scope)
    if not windows_path:
        raise RuntimeError(f"Windows não informou a pasta para {scope}")
    return _windows_to_wsl(windows_path), windows_path


def _display_path(path: Path, root: Path, display_root: str) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    if re.match(r"^[A-Za-z]:[\\/]", display_root):
        return str(PureWindowsPath(display_root).joinpath(*relative.parts))
    return str(path)


def _emit_progress(callback: Optional[Callable], elapsed: float, files: int, folders: int) -> None:
    if callback:
        callback({
            "message": "Ainda estou analisando o armazenamento.",
            "elapsed_seconds": round(elapsed, 2),
            "files_scanned": files,
            "folders_scanned": folders,
        })


def _scan_tree(
    root: Path,
    display_root: str,
    *,
    timeout_seconds: float = DEFAULT_SCAN_TIMEOUT,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    progress_after_seconds: float = DEFAULT_PROGRESS_AFTER,
) -> Dict[str, Any]:
    """Traverse each directory once and aggregate direct-child folder sizes."""
    started = time.monotonic()
    deadline = started + max(0.0, float(timeout_seconds))
    next_progress = started + max(0.0, float(progress_after_seconds))
    cancel_event = cancel_event or threading.Event()
    folder_sizes: Dict[str, int] = {}
    folder_info: Dict[str, Dict[str, Any]] = {}
    largest_files: list[tuple[int, int, Dict[str, Any]]] = []
    stack: list[tuple[Path, Optional[str]]] = [(root, None)]
    visited_dirs = set()
    files_scanned = 0
    folders_scanned = 0
    errors = 0
    sequence = 0
    timed_out = False
    cancelled = False

    while stack:
        now = time.monotonic()
        if cancel_event.is_set():
            cancelled = True
            break
        if now >= deadline:
            timed_out = True
            break
        if now >= next_progress:
            _emit_progress(progress_callback, now - started, files_scanned, folders_scanned)
            next_progress = now + 10.0

        directory, top_key = stack.pop()
        try:
            stat = directory.stat(follow_symlinks=False)
            identity = (stat.st_dev, stat.st_ino)
            if identity in visited_dirs:
                continue
            visited_dirs.add(identity)
            with os.scandir(directory) as entries:
                for entry in entries:
                    sequence += 1
                    if sequence % 128 == 0:
                        now = time.monotonic()
                        if cancel_event.is_set():
                            cancelled = True
                            break
                        if now >= deadline:
                            timed_out = True
                            break
                        if now >= next_progress:
                            _emit_progress(progress_callback, now - started, files_scanned, folders_scanned)
                            next_progress = now + 10.0
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            folders_scanned += 1
                            child = Path(entry.path)
                            child_key = top_key
                            if directory == root:
                                child_key = entry.name
                                entry_stat = entry.stat(follow_symlinks=False)
                                folder_sizes.setdefault(child_key, 0)
                                folder_info[child_key] = {
                                    "name": entry.name,
                                    "path": _display_path(child, root, display_root),
                                    "modified": time.strftime(
                                        "%Y-%m-%d %H:%M:%S",
                                        time.localtime(entry_stat.st_mtime),
                                    ),
                                }
                            stack.append((child, child_key))
                        elif entry.is_file(follow_symlinks=False):
                            entry_stat = entry.stat(follow_symlinks=False)
                            size = int(entry_stat.st_size)
                            files_scanned += 1
                            if top_key is not None:
                                folder_sizes[top_key] = folder_sizes.get(top_key, 0) + size
                            item = {
                                "name": entry.name,
                                "path": _display_path(Path(entry.path), root, display_root),
                                "extension": Path(entry.name).suffix,
                                "size_bytes": size,
                                "modified": time.strftime(
                                    "%Y-%m-%d %H:%M:%S",
                                    time.localtime(entry_stat.st_mtime),
                                ),
                            }
                            candidate = (size, sequence, item)
                            if len(largest_files) < 20:
                                heapq.heappush(largest_files, candidate)
                            elif size > largest_files[0][0]:
                                heapq.heapreplace(largest_files, candidate)
                    except (OSError, PermissionError):
                        errors += 1
                if timed_out or cancelled:
                    break
        except (OSError, PermissionError):
            errors += 1

    elapsed = time.monotonic() - started
    folders = []
    for key, size in folder_sizes.items():
        item = dict(folder_info[key])
        item.update({
            "size_bytes": size,
            "size_mb": round(size / (1024 ** 2), 1),
            "size_gb": round(size / (1024 ** 3), 2),
        })
        folders.append(item)
    folders.sort(key=lambda item: item["size_bytes"], reverse=True)

    files = []
    for _, _, item in sorted(largest_files, key=lambda row: row[0], reverse=True):
        value = dict(item)
        value.update({
            "size_mb": round(value["size_bytes"] / (1024 ** 2), 1),
            "size_gb": round(value["size_bytes"] / (1024 ** 3), 2),
        })
        files.append(value)

    return {
        "root": display_root,
        "exists": True,
        "complete": not timed_out and not cancelled,
        "partial": timed_out or cancelled,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "elapsed_seconds": round(elapsed, 3),
        "files_scanned": files_scanned,
        "folders_scanned": folders_scanned,
        "errors_skipped": errors,
        "folders": folders,
        "files": files,
    }


def _scan_windows(
    scope: str,
    mode: str,
    *,
    timeout_seconds: float,
    cancel_event: Optional[threading.Event],
    progress_callback: Optional[Callable],
    progress_after_seconds: float,
) -> Dict[str, Any]:
    """Single-pass scan using native Windows enumeration.

    PowerShell streams progress snapshots as JSON. Python owns the hard timeout
    and can terminate the child while retaining the most recent partial result.
    """
    scope = _validate_scope(scope)
    if mode not in {"folders", "files"}:
        raise ValueError(f"modo de scan inválido: {mode}")
    cancel_event = cancel_event or threading.Event()
    progress_after = max(0.0, float(progress_after_seconds))
    process_body = r'''
    $size = [double]$_.Length
    $relative = $_.FullName.Substring($prefix.Length)
    $separator = $relative.IndexOf('\')

    if ($mode -eq 'folders' -and $separator -gt 0) {
        $top = $relative.Substring(0, $separator)
        if ($totals.ContainsKey($top)) {
            $totals[$top] = [double]$totals[$top] + $size
        }
        else {
            $totals[$top] = $size
        }
    }

    if ($mode -eq 'files') {
        $item = [pscustomobject][ordered]@{
            name = $_.Name
            path = $_.FullName
            extension = $_.Extension
            size_bytes = [long]$_.Length
            size_mb = [math]::Round($size / 1MB, 1)
            size_gb = [math]::Round($size / 1GB, 2)
            modified = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
        }
        if ($largest.Count -lt 20) {
            $null = $largest.Add($item)
        }
        else {
            $minimumIndex = 0
            for ($index = 1; $index -lt $largest.Count; $index++) {
                if ($largest[$index].size_bytes -lt $largest[$minimumIndex].size_bytes) {
                    $minimumIndex = $index
                }
            }
            if ($item.size_bytes -gt $largest[$minimumIndex].size_bytes) {
                $largest[$minimumIndex] = $item
            }
        }
    }

    $files++
    if ($sw.Elapsed.TotalSeconds -ge $nextProgress) {
        if ($mode -eq 'folders') {
            $partial = @(Get-FolderResults)
        }
        else {
            $partial = @($largest | Sort-Object size_bytes -Descending)
        }
        [ordered]@{
            type = 'progress'
            root = $root
            elapsed_seconds = [math]::Round($sw.Elapsed.TotalSeconds, 3)
            files_scanned = $files
            folders_scanned = $folders
            items = $partial
        } | ConvertTo-Json -Compress -Depth 7 | Write-Output
        $nextProgress = $sw.Elapsed.TotalSeconds + 5
    }
'''
    script = rf'''
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ProgressPreference = 'SilentlyContinue'
$mode = '{mode}'
switch ('{scope}') {{
    'profile' {{ $root = $env:USERPROFILE }}
    'downloads' {{
        $shell = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -ErrorAction SilentlyContinue
        $property = $null
        if ($shell) {{ $property = $shell.PSObject.Properties['{{374DE290-123F-4565-9164-39C4925E467B}}'] }}
        if ($property) {{ $root = [Environment]::ExpandEnvironmentVariables([string]$property.Value) }}
        else {{ $root = Join-Path $env:USERPROFILE 'Downloads' }}
    }}
    'desktop' {{ $root = [Environment]::GetFolderPath('Desktop') }}
    'documents' {{ $root = [Environment]::GetFolderPath('MyDocuments') }}
}}

if (-not $root -or -not (Test-Path -LiteralPath $root)) {{
    [ordered]@{{ type='result'; root=$root; exists=$false; folders=@(); files=@() }} |
        ConvertTo-Json -Compress -Depth 4
    exit
}}

[ordered]@{{ type='started'; root=$root; exists=$true }} | ConvertTo-Json -Compress
$prefix = $root.TrimEnd('\') + '\'
$totals = @{{}}
$modified = @{{}}
$largest = New-Object System.Collections.ArrayList
$files = 0
$folders = 0

Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction SilentlyContinue |
    ForEach-Object {{
        $totals[$_.Name] = [double]0
        $modified[$_.Name] = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
    }}

function Get-FolderResults {{
    @($totals.GetEnumerator() | ForEach-Object {{
        $folderPath = Join-Path $root $_.Key
        [pscustomobject][ordered]@{{
            name = $_.Key
            path = $folderPath
            modified = $modified[$_.Key]
            size_bytes = [long]$_.Value
            size_mb = [math]::Round([double]$_.Value / 1MB, 1)
            size_gb = [math]::Round([double]$_.Value / 1GB, 2)
        }}
    }} | Sort-Object size_bytes -Descending | Select-Object -First 20)
}}

$sw = [Diagnostics.Stopwatch]::StartNew()
$nextProgress = {progress_after}
$scanParams = @{{
    LiteralPath = $root
    Recurse = $true
    Force = $true
    ErrorAction = 'SilentlyContinue'
}}
if ($mode -eq 'files') {{ $scanParams['File'] = $true }}

Get-ChildItem @scanParams |
ForEach-Object {{
    if ($mode -eq 'folders' -and $_.PSIsContainer) {{
        $folders++
    }}
    else {{
{process_body}
    }}
}}

$sw.Stop()
if ($mode -eq 'folders') {{ $folderResults = @(Get-FolderResults); $fileResults = @() }}
else {{ $folderResults = @(); $fileResults = @($largest | Sort-Object size_bytes -Descending) }}
[ordered]@{{
    type = 'result'
    root = $root
    exists = $true
    elapsed_seconds = [math]::Round($sw.Elapsed.TotalSeconds, 3)
    files_scanned = $files
    folders_scanned = $folders
    folders = $folderResults
    files = $fileResults
}} | ConvertTo-Json -Compress -Depth 7
'''
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    proc = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output: queue.Queue = queue.Queue()

    def read_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            output.put(line)
        output.put(None)

    reader = threading.Thread(target=read_stdout, daemon=True, name="nexo-storage-output")
    reader.start()
    started = time.monotonic()
    root = None
    latest_items = []
    files_scanned = 0
    folders_scanned = 0
    final = None
    timed_out = False
    cancelled = False
    stream_closed = False

    while not stream_closed:
        elapsed = time.monotonic() - started
        if cancel_event.is_set():
            cancelled = True
            break
        if elapsed >= max(0.0, float(timeout_seconds)):
            timed_out = True
            break
        try:
            line = output.get(timeout=min(0.1, max(0.01, timeout_seconds - elapsed)))
        except queue.Empty:
            if proc.poll() is not None:
                stream_closed = True
            continue
        if line is None:
            stream_closed = True
            continue
        raw = line.strip().lstrip("\ufeff")
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        root = event.get("root") or root
        if event.get("type") == "progress":
            latest_items = event.get("items") or latest_items
            files_scanned = int(event.get("files_scanned", files_scanned))
            folders_scanned = int(event.get("folders_scanned", folders_scanned))
            _emit_progress(progress_callback, elapsed, files_scanned, folders_scanned)
        elif event.get("type") == "result":
            final = event

    if timed_out or cancelled:
        proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)

    wall_elapsed = time.monotonic() - started
    if final is not None:
        return {
            "root": final.get("root"),
            "exists": bool(final.get("exists")),
            "complete": True,
            "partial": False,
            "timed_out": False,
            "cancelled": False,
            "elapsed_seconds": round(wall_elapsed, 3),
            "backend_elapsed_seconds": final.get("elapsed_seconds", 0.0),
            "files_scanned": int(final.get("files_scanned", 0)),
            "folders_scanned": int(final.get("folders_scanned", 0)),
            "errors_skipped": 0,
            "folders": final.get("folders") or [],
            "files": final.get("files") or [],
            "backend": "windows",
        }
    if proc.returncode not in {0, None, -15} and not (timed_out or cancelled):
        stderr = proc.stderr.read().strip() if proc.stderr else ""
        raise RuntimeError(stderr or "Scanner nativo do Windows falhou")
    return {
        "root": root,
        "exists": bool(root),
        "complete": False,
        "partial": True,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "elapsed_seconds": round(wall_elapsed, 3),
        "backend_elapsed_seconds": None,
        "files_scanned": files_scanned,
        "folders_scanned": folders_scanned,
        "errors_skipped": 0,
        "folders": latest_items if mode == "folders" else [],
        "files": latest_items if mode == "files" else [],
        "backend": "windows",
    }


def _scan_windows_cached(
    scope: str,
    mode: str,
    *,
    timeout_seconds: float,
    cache_ttl: float,
    use_cache: bool,
    cancel_event: Optional[threading.Event],
    progress_callback: Optional[Callable],
    progress_after_seconds: float,
) -> Dict[str, Any]:
    key = f"windows:{scope}:{mode}"
    now = time.monotonic()
    if use_cache:
        with _SCAN_LOCK:
            cached = _SCAN_CACHE.get(key)
            if cached and now - cached[0] < max(0.0, cache_ttl):
                result = copy.deepcopy(cached[1])
                result["cached"] = True
                result["cache_age_seconds"] = round(now - cached[0], 2)
                return result
    result = _scan_windows(
        scope,
        mode,
        timeout_seconds=timeout_seconds,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        progress_after_seconds=progress_after_seconds,
    )
    result["cached"] = False
    if result["complete"]:
        with _SCAN_LOCK:
            _SCAN_CACHE[key] = (time.monotonic(), copy.deepcopy(result))
    return result


def _scan_cached(
    root: Path,
    display_root: str,
    *,
    timeout_seconds: float,
    cache_ttl: float,
    use_cache: bool,
    cancel_event: Optional[threading.Event],
    progress_callback: Optional[Callable],
    progress_after_seconds: float,
) -> Dict[str, Any]:
    key = str(root.resolve(strict=False))
    now = time.monotonic()
    if use_cache:
        with _SCAN_LOCK:
            cached = _SCAN_CACHE.get(key)
            if cached and now - cached[0] < max(0.0, cache_ttl):
                result = copy.deepcopy(cached[1])
                result["cached"] = True
                result["cache_age_seconds"] = round(now - cached[0], 2)
                return result

    result = _scan_tree(
        root,
        display_root,
        timeout_seconds=timeout_seconds,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        progress_after_seconds=progress_after_seconds,
    )
    result["cached"] = False
    if result["complete"]:
        with _SCAN_LOCK:
            _SCAN_CACHE[key] = (time.monotonic(), copy.deepcopy(result))
    return result


def _tool_scan(
    scope: str,
    *,
    mode: str,
    timeout_seconds: float,
    cache_ttl: float,
    use_cache: bool,
    cancel_event: Optional[threading.Event],
    progress_callback: Optional[Callable],
    progress_after_seconds: float,
    root_override: Optional[os.PathLike],
    job_context: Any,
    backend: str,
) -> Dict[str, Any]:
    scope = _validate_scope(scope)
    effective_cancel = cancel_event
    if job_context is not None:
        effective_cancel = job_context.cancel_event

        def job_progress(data: Dict[str, Any]) -> None:
            job_context.report_progress(message=data.get("message"), data=data)
            if progress_callback:
                progress_callback(data)
        effective_progress = job_progress
    else:
        effective_progress = progress_callback

    if backend not in {"auto", "windows", "python"}:
        raise ValueError(f"backend inválido: {backend}")
    if root_override is None and backend in {"auto", "windows"}:
        try:
            return _scan_windows_cached(
                scope,
                mode,
                timeout_seconds=timeout_seconds,
                cache_ttl=cache_ttl,
                use_cache=use_cache,
                cancel_event=effective_cancel,
                progress_callback=effective_progress,
                progress_after_seconds=progress_after_seconds,
            )
        except Exception:
            if backend == "windows":
                raise

    if root_override is None:
        root, display_root = resolve_storage_root(scope)
    else:
        root = Path(root_override)
        display_root = str(root)

    if not root.exists() or not root.is_dir():
        return {
            "root": display_root,
            "exists": False,
            "complete": True,
            "partial": False,
            "timed_out": False,
            "cancelled": False,
            "elapsed_seconds": 0.0,
            "files_scanned": 0,
            "folders_scanned": 0,
            "errors_skipped": 0,
            "folders": [],
            "files": [],
            "cached": False,
            "backend": "python",
        }

    result = _scan_cached(
        root,
        display_root,
        timeout_seconds=timeout_seconds,
        cache_ttl=cache_ttl,
        use_cache=use_cache,
        cancel_event=effective_cancel,
        progress_callback=effective_progress,
        progress_after_seconds=progress_after_seconds,
    )
    result["backend"] = "python"
    return result


def find_large_folders(
    scope: str,
    limit: int = 10,
    *,
    timeout_seconds: float = DEFAULT_SCAN_TIMEOUT,
    cache_ttl: float = DEFAULT_CACHE_TTL,
    use_cache: bool = True,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable] = None,
    progress_after_seconds: float = DEFAULT_PROGRESS_AFTER,
    root_override: Optional[os.PathLike] = None,
    job_context: Any = None,
    backend: str = "auto",
) -> Dict[str, Any]:
    limit = max(1, min(20, int(limit)))
    data = _tool_scan(
        scope,
        mode="folders",
        timeout_seconds=timeout_seconds,
        cache_ttl=cache_ttl,
        use_cache=use_cache,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        progress_after_seconds=progress_after_seconds,
        root_override=root_override,
        job_context=job_context,
        backend=backend,
    )
    data.pop("files", None)
    data["folders"] = data["folders"][:limit]
    return {"ok": True, "tool": "find_large_folders", "scope": scope, **data}


def find_large_files(
    scope: str,
    limit: int = 10,
    min_size_mb: int = 100,
    *,
    timeout_seconds: float = DEFAULT_SCAN_TIMEOUT,
    cache_ttl: float = DEFAULT_CACHE_TTL,
    use_cache: bool = True,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable] = None,
    progress_after_seconds: float = DEFAULT_PROGRESS_AFTER,
    root_override: Optional[os.PathLike] = None,
    job_context: Any = None,
    backend: str = "auto",
) -> Dict[str, Any]:
    limit = max(1, min(20, int(limit)))
    min_size_mb = max(1, min(100000, int(min_size_mb)))
    data = _tool_scan(
        scope,
        mode="files",
        timeout_seconds=timeout_seconds,
        cache_ttl=cache_ttl,
        use_cache=use_cache,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        progress_after_seconds=progress_after_seconds,
        root_override=root_override,
        job_context=job_context,
        backend=backend,
    )
    minimum = min_size_mb * 1024 ** 2
    data.pop("folders", None)
    data["files"] = [item for item in data["files"] if item["size_bytes"] >= minimum][:limit]
    data["minimum_size_mb"] = min_size_mb
    return {"ok": True, "tool": "find_large_files", "scope": scope, **data}


def clear_storage_cache() -> None:
    with _SCAN_LOCK:
        _SCAN_CACHE.clear()
