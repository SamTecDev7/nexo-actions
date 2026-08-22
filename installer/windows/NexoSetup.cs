using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;

[assembly: AssemblyTitle("Nexo Setup")]
[assembly: AssemblyDescription("Instalador ponta a ponta do Nexo para Windows e WSL")]
[assembly: AssemblyCompany("Nexo")]
[assembly: AssemblyProduct("Nexo Setup")]
[assembly: AssemblyVersion("2.0.0.0")]
[assembly: AssemblyFileVersion("2.0.0.0")]

namespace Nexo.WindowsSetup
{
    internal static class Program
    {
        private static readonly string[] Resources =
        {
            "Nexo.Install-Nexo.ps1",
            "Nexo.bootstrap-root.sh",
            "Nexo.install-nexo.sh"
        };

        [STAThread]
        private static int Main(string[] args)
        {
            Mutex setupMutex = null;
            bool ownsSetupMutex = false;
            string temporaryRoot = Path.Combine(
                Path.GetTempPath(), "NexoSetup-" + Guid.NewGuid().ToString("N"));
            try
            {
                setupMutex = new Mutex(false, @"Local\Nexo.WindowsSetup");
                try
                {
                    ownsSetupMutex = setupMutex.WaitOne(0, false);
                }
                catch (AbandonedMutexException)
                {
                    ownsSetupMutex = true;
                }
                if (!ownsSetupMutex)
                {
                    Console.Error.WriteLine("Outra instalação do Nexo já está em andamento.");
                    return 4;
                }
                Directory.CreateDirectory(temporaryRoot);
                Dictionary<string, string> extracted = ExtractResources(temporaryRoot);
                string powerShell = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.System),
                    @"WindowsPowerShell\v1.0\powershell.exe");
                if (!File.Exists(powerShell))
                {
                    Console.Error.WriteLine("Windows PowerShell não foi encontrado.");
                    return 2;
                }

                StringBuilder command = new StringBuilder();
                command.Append("-NoLogo -NoProfile -ExecutionPolicy Bypass -File ");
                command.Append(Quote(extracted["Nexo.Install-Nexo.ps1"]));
                command.Append(" -BootstrapRootScript ");
                command.Append(Quote(extracted["Nexo.bootstrap-root.sh"]));
                command.Append(" -InstallWslScript ");
                command.Append(Quote(extracted["Nexo.install-nexo.sh"]));
                command.Append(" -ParentLockHeld");
                foreach (string argument in args)
                {
                    command.Append(' ');
                    command.Append(Quote(argument));
                }

                Process process = Process.Start(new ProcessStartInfo
                {
                    FileName = powerShell,
                    Arguments = command.ToString(),
                    WorkingDirectory = temporaryRoot,
                    UseShellExecute = false
                });
                if (process == null) return 3;
                process.WaitForExit();
                return process.ExitCode;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine("O instalador do Nexo falhou: " + error.Message);
                Console.Error.WriteLine("Nenhuma chave de API foi gravada pelo bootstrap.");
                return 1;
            }
            finally
            {
                try { Directory.Delete(temporaryRoot, true); }
                catch { }
                if (ownsSetupMutex && setupMutex != null)
                {
                    try { setupMutex.ReleaseMutex(); }
                    catch { }
                }
                if (setupMutex != null) setupMutex.Dispose();
            }
        }

        private static Dictionary<string, string> ExtractResources(string targetRoot)
        {
            Assembly assembly = Assembly.GetExecutingAssembly();
            Dictionary<string, string> result = new Dictionary<string, string>();
            foreach (string resourceName in Resources)
            {
                string filename = resourceName.Substring("Nexo.".Length);
                string target = Path.Combine(targetRoot, filename);
                using (Stream input = assembly.GetManifestResourceStream(resourceName))
                {
                    if (input == null) throw new InvalidOperationException("Recurso ausente: " + resourceName);
                    using (FileStream output = File.Create(target)) input.CopyTo(output);
                }
                result[resourceName] = target;
            }
            return result;
        }

        private static string Quote(string value)
        {
            if (value == null) return "\"\"";
            if (value.Contains("\""))
                throw new ArgumentException("Argumentos do instalador não podem conter aspas.");
            return "\"" + value + "\"";
        }
    }
}
