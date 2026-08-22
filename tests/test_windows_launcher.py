from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "windows/NexoLauncher.cs"


def method_body(source: str, signature: str) -> str:
    """Return a C# method body without depending on indentation or line layout."""
    match = re.search(signature, source)
    if match is None:
        raise AssertionError(f"method not found: {signature}")
    opening = source.find("{", match.end())
    if opening < 0:
        raise AssertionError(f"method has no body: {signature}")

    depth = 0
    for index in range(opening, len(source)):
        character = source[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : index]
    raise AssertionError(f"unterminated method: {signature}")


class WindowsLauncherRecoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = LAUNCHER_PATH.read_text(encoding="utf-8")
        cls._launcher_temp = None
        cls._compiled_launcher = None
        cls._powershell = None

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._launcher_temp is not None:
            cls._launcher_temp.cleanup()

    def compiled_windows_launcher(self) -> tuple[str, str]:
        cls = type(self)
        if cls._compiled_launcher is not None and cls._powershell is not None:
            return cls._powershell, cls._compiled_launcher

        powershell = shutil.which("powershell.exe")
        wslpath = shutil.which("wslpath")
        if not powershell or not wslpath:
            self.skipTest("Windows PowerShell interop is unavailable")

        windows_temp_result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "$OutputEncoding=[Console]::OutputEncoding="
                "[Text.UTF8Encoding]::new($false); "
                "[Console]::Write([IO.Path]::GetTempPath())",
            ],
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(
            windows_temp_result.returncode,
            0,
            windows_temp_result.stderr.decode("utf-8", errors="replace"),
        )
        windows_temp = windows_temp_result.stdout.decode("utf-8").strip()
        windows_temp_wsl = subprocess.check_output(
            [wslpath, "-u", windows_temp], text=True
        ).strip()

        cls._launcher_temp = tempfile.TemporaryDirectory(
            prefix="nexo-launcher-reflection-",
            dir=windows_temp_wsl,
        )
        temporary = cls._launcher_temp.name
        script_windows = subprocess.check_output(
            [wslpath, "-w", str(ROOT / "scripts/build_windows_exe.ps1")],
            text=True,
        ).strip()
        output_windows = subprocess.check_output(
            [wslpath, "-w", temporary], text=True
        ).strip()
        build = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_windows,
                "-OutputDirectory",
                output_windows,
                "-BuildOnly",
            ],
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(
            build.returncode,
            0,
            build.stderr.decode("utf-8", errors="replace"),
        )
        executable = Path(temporary) / "Nexo.exe"
        self.assertTrue(executable.is_file())
        cls._compiled_launcher = subprocess.check_output(
            [wslpath, "-w", str(executable)], text=True
        ).strip()
        cls._powershell = powershell
        return powershell, cls._compiled_launcher

    def test_launcher_writes_a_persistent_local_diagnostic_log(self):
        self.assertIn("launcher.log", self.source)
        self.assertIn("Directory.CreateDirectory", self.source)
        self.assertRegex(
            self.source,
            r"(?:File\.AppendAllText|FileMode\.Append|new\s+StreamWriter\s*\([^;]+,\s*true)",
        )
        ensure_started = method_body(
            self.source,
            r"\bStartupResult\s+EnsureStarted\s*\(",
        )
        self.assertRegex(
            ensure_started,
            r"(?:\bLog\w*|\bLauncherLog\.\w+)\s*\(",
        )

    def test_missing_runtime_config_is_explicit_and_points_to_installer(self):
        self.assertIn("runtime.conf", self.source)
        self.assertIn("NexoSetup.exe", self.source)
        self.assertRegex(
            self.source,
            r"\b(?:ConfigPresent|HasRuntimeConfig|RuntimeConfigExists)\b",
        )
        self.assertRegex(
            self.source,
            r"(?:ConfigPresent|HasRuntimeConfig|RuntimeConfigExists)\s*=\s*File\.Exists\s*\(",
        )
        ensure_started = method_body(
            self.source,
            r"\bStartupResult\s+EnsureStarted\s*\(",
        )
        self.assertRegex(
            ensure_started,
            r"if\s*\(\s*!\s*Settings\.(?:ConfigPresent|HasRuntimeConfig|RuntimeConfigExists)\s*\)",
        )

    def test_cold_start_retries_then_performs_a_final_service_validation(self):
        retry = method_body(
            self.source,
            r"\bProcessResult\s+StartServicesWithRetry\s*\(",
        )
        self.assertRegex(retry, r"\b(?:for|while)\s*\(")
        self.assertRegex(retry, r"RunWsl\s*\(")
        self.assertRegex(retry, r"(?:Thread\.Sleep|WaitHandle|Backoff)")

        ensure_started = method_body(
            self.source,
            r"\bStartupResult\s+EnsureStarted\s*\(",
        )
        retry_call = ensure_started.find("StartServicesWithRetry(")
        validation = ensure_started.find("CheckEssentialServices()", retry_call)
        self.assertGreaterEqual(retry_call, 0)
        self.assertGreater(validation, retry_call)

        service_check_wrapper = method_body(
            self.source,
            r"\bProcessResult\s+CheckEssentialServices\s*\(\s*\)",
        )
        service_check_timed = method_body(
            self.source,
            r"\bProcessResult\s+CheckEssentialServices\s*\(\s*int\s+timeout\s*\)",
        )
        self.assertRegex(
            service_check_wrapper,
            r"return\s+CheckEssentialServices\s*\(\s*\d+\s*\)",
        )
        self.assertIn("systemctl --user is-active", service_check_timed)
        self.assertRegex(
            service_check_timed,
            r"RunWsl\s*\([\s\S]*,\s*timeout\s*\)",
        )

        active_guard = re.search(
            r"if\s*\(\s*active\.TimedOut\s*\|\|\s*active\.ExitCode\s*!=\s*0\s*\)",
            ensure_started,
        )
        self.assertIsNotNone(active_guard)

    def test_wait_for_ui_uses_a_real_elapsed_time_deadline(self):
        wait_for_ui = method_body(
            self.source,
            r"\bbool\s+WaitForUi\s*\(\s*int\s+seconds\s*\)",
        )
        self.assertIn("Stopwatch.StartNew()", wait_for_ui)
        self.assertRegex(wait_for_ui, r"\.Elapsed\b")
        self.assertRegex(wait_for_ui, r"TimeSpan\.FromSeconds\s*\(\s*seconds\s*\)")
        self.assertNotRegex(wait_for_ui, r"seconds\s*\*\s*2")

    def test_process_output_is_decoded_from_raw_utf16le_without_nuls(self):
        run_process = method_body(
            self.source,
            r"\bProcessResult\s+RunProcess\s*\(",
        )
        self.assertNotIn("StandardOutputEncoding = Encoding.UTF8", run_process)
        self.assertNotIn("StandardErrorEncoding = Encoding.UTF8", run_process)
        self.assertRegex(run_process, r"DecodeProcessOutput\s*\(")

        decoder = method_body(
            self.source,
            r"\bstring\s+DecodeProcessOutput\s*\(\s*byte\[\]\s+\w+\s*\)",
        )
        self.assertIn("encoding = Encoding.Unicode", decoder)
        self.assertRegex(decoder, r"\bencoding\.GetString\s*\(")
        self.assertRegex(decoder, r"(?:0xFF\s*&&[^\n]+0xFE|odd|nul|null)")

    def test_decoder_semantically_handles_bomless_wsl_utf16le(self):
        powershell, executable_windows = self.compiled_windows_launcher()
        escaped_executable = executable_windows.replace("'", "''")
        command = (
            "$ErrorActionPreference='Stop'; "
            "$OutputEncoding=[Console]::OutputEncoding="
            "[Text.UTF8Encoding]::new($false); "
            f"$assembly=[Reflection.Assembly]::LoadFile('{escaped_executable}'); "
            "$type=$assembly.GetType('Nexo.WindowsLauncher.LauncherRuntime',$true); "
            "$flags=[Reflection.BindingFlags]'NonPublic,Static'; "
            "$method=$type.GetMethod('DecodeProcessOutput',$flags); "
            "if($null -eq $method){throw 'DecodeProcessOutput ausente'}; "
            "$expected='Não há distribuição com o nome fornecido.'; "
            "$bytes=[Text.Encoding]::Unicode.GetBytes($expected); "
            "$arguments=[object[]]::new(1); $arguments[0]=$bytes; "
            "$decoded=[string]$method.Invoke($null,$arguments); "
            "[ordered]@{decoded=$decoded;nuls=($decoded.ToCharArray() | "
            "Where-Object {[int]$_ -eq 0}).Count} | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            timeout=30,
        )
        output = result.stdout.decode("utf-8", errors="replace")
        error = result.stderr.decode("utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, error)
        match = re.search(rb"\{.*\}\s*$", result.stdout, flags=re.S)
        self.assertIsNotNone(match, output)
        decoded = json.loads(match.group(0).decode("utf-8"))
        self.assertEqual(
            decoded["decoded"],
            "Não há distribuição com o nome fornecido.",
        )
        self.assertEqual(decoded["nuls"], 0)

    def test_runtime_user_is_loaded_and_run_wsl_quotes_each_argument(self):
        settings_load = method_body(
            self.source,
            r"\bRuntimeSettings\s+Load\s*\(",
        )
        self.assertIn('key == "wsl_user"', settings_load)
        self.assertRegex(settings_load, r"settings\.WslUser\s*=\s*value")

        run_wsl = method_body(
            self.source,
            r"\bProcessResult\s+RunWsl\s*\(",
        )
        self.assertIn("Settings.WslUser", run_wsl)
        self.assertRegex(
            run_wsl,
            r'" -u "\s*\+\s*QuoteWindowsArgument\s*\('
            r"ValidateWslUser\s*\(\s*Settings\.WslUser\s*\)\s*\)",
        )
        self.assertIn("QuoteWindowsArgument(distro)", run_wsl)
        self.assertIn("QuoteWindowsArgument(command)", run_wsl)
        self.assertIn('" --exec bash -lc "', run_wsl)
        self.assertNotIn(".Replace(", run_wsl)

    def test_windows_argument_quoting_semantics(self):
        powershell, executable_windows = self.compiled_windows_launcher()
        escaped_executable = executable_windows.replace("'", "''")
        command = (
            "$ErrorActionPreference='Stop'; "
            "$OutputEncoding=[Console]::OutputEncoding="
            "[Text.UTF8Encoding]::new($false); "
            f"$assembly=[Reflection.Assembly]::LoadFile('{escaped_executable}'); "
            "$type=$assembly.GetType('Nexo.WindowsLauncher.LauncherRuntime',$true); "
            "$flags=[Reflection.BindingFlags]'NonPublic,Static'; "
            "$method=$type.GetMethod('QuoteWindowsArgument',$flags); "
            "if($null -eq $method){throw 'QuoteWindowsArgument ausente'}; "
            "function Quote([string]$value){"
            "$arguments=[object[]]::new(1); $arguments[0]=$value; "
            "return [string]$method.Invoke($null,$arguments)}; "
            "[ordered]@{"
            "plain=(Quote 'nexo');"
            "spaces=(Quote 'duas palavras');"
            "embedded=(Quote 'diga \"oi\"');"
            "trailing=(Quote 'C:\\Program Files\\Nexo\\')"
            "} | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            timeout=30,
        )
        output = result.stdout.decode("utf-8", errors="replace")
        error = result.stderr.decode("utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, error)
        match = re.search(rb"\{.*\}\s*$", result.stdout, flags=re.S)
        self.assertIsNotNone(match, output)
        quoted = json.loads(match.group(0).decode("utf-8"))
        self.assertEqual(quoted["plain"], "nexo")
        self.assertEqual(quoted["spaces"], '"duas palavras"')
        self.assertEqual(quoted["embedded"], '"diga \\"oi\\""')
        self.assertEqual(
            quoted["trailing"],
            '"C:\\Program Files\\Nexo\\\\"',
        )

    def test_run_process_timeout_kills_drains_and_keeps_exit_sentinel(self):
        run_process = method_body(
            self.source,
            r"\bProcessResult\s+RunProcess\s*\(",
        )
        timed_out = run_process.find("result.TimedOut = true")
        killed = run_process.find("process.Kill()", timed_out)
        waited = run_process.find("process.WaitForExit(5000)", killed)
        output_joined = run_process.find("outputReader.Join", waited)
        error_joined = run_process.find("errorReader.Join", waited)
        decoded = run_process.find("DecodeProcessOutput", error_joined)
        self.assertGreaterEqual(timed_out, 0)
        self.assertGreater(killed, timed_out)
        self.assertGreater(waited, killed)
        self.assertGreater(output_joined, waited)
        self.assertGreater(error_joined, waited)
        self.assertGreater(decoded, error_joined)
        self.assertRegex(self.source, r"public\s+int\s+ExitCode\s*=\s*-1\s*;")

        powershell, executable_windows = self.compiled_windows_launcher()
        escaped_executable = executable_windows.replace("'", "''")
        command = (
            "$ErrorActionPreference='Stop'; "
            "$OutputEncoding=[Console]::OutputEncoding="
            "[Text.UTF8Encoding]::new($false); "
            f"$assembly=[Reflection.Assembly]::LoadFile('{escaped_executable}'); "
            "$type=$assembly.GetType('Nexo.WindowsLauncher.LauncherRuntime',$true); "
            "$flags=[Reflection.BindingFlags]'NonPublic,Static'; "
            "$method=$type.GetMethod('RunProcess',$flags); "
            "if($null -eq $method){throw 'RunProcess ausente'}; "
            "$ping=Join-Path ([Environment]::GetFolderPath("
            "[Environment+SpecialFolder]::System)) 'PING.EXE'; "
            "$arguments=[object[]]::new(4); "
            "$arguments[0]=[string]$ping; "
            "$arguments[1]=[string]'127.0.0.1 -t'; "
            "$arguments[2]=[int]350; $arguments[3]=[bool]$true; "
            "$timer=[Diagnostics.Stopwatch]::StartNew(); "
            "$processResult=$method.Invoke($null,$arguments); $timer.Stop(); "
            "[ordered]@{"
            "timed_out=[bool]$processResult.TimedOut;"
            "exit_code=[int]$processResult.ExitCode;"
            "elapsed_ms=[int64]$timer.ElapsedMilliseconds;"
            "output_length=([string]$processResult.Output).Length"
            "} | ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            timeout=15,
        )
        output = result.stdout.decode("utf-8", errors="replace")
        error = result.stderr.decode("utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, error)
        match = re.search(rb"\{.*\}\s*$", result.stdout, flags=re.S)
        self.assertIsNotNone(match, output)
        timeout_result = json.loads(match.group(0).decode("utf-8"))
        self.assertTrue(timeout_result["timed_out"])
        self.assertEqual(timeout_result["exit_code"], -1)
        self.assertLess(timeout_result["elapsed_ms"], 7000)
        self.assertGreater(timeout_result["output_length"], 0)


if __name__ == "__main__":
    unittest.main()
