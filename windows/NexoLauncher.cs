using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Nexo")]
[assembly: AssemblyDescription("Assistente pessoal de voz para Windows")]
[assembly: AssemblyCompany("Nexo")]
[assembly: AssemblyProduct("Nexo")]
[assembly: AssemblyCopyright("Copyright © 2026")]
[assembly: AssemblyVersion("2.0.1.0")]
[assembly: AssemblyFileVersion("2.0.1.0")]
[assembly: AssemblyInformationalVersion("2.0.1")]

namespace Nexo.WindowsLauncher
{
    internal static class Program
    {
        private static Mutex instanceMutex;

        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            LauncherLog.Write("Nexo launcher started (version " +
                              Assembly.GetExecutingAssembly().GetName().Version + ").");

            bool firstInstance;
            instanceMutex = new Mutex(true, @"Local\Nexo.WindowsLauncher", out firstInstance);
            if (!firstInstance)
            {
                LauncherRuntime.OpenUi("/");
                return;
            }

            try
            {
                Application.Run(new LauncherContext());
            }
            finally
            {
                instanceMutex.ReleaseMutex();
                instanceMutex.Dispose();
            }
        }
    }

    internal sealed class LauncherContext : ApplicationContext
    {
        private readonly StartupForm splash;
        private readonly NotifyIcon tray;
        private int operationRunning;

        public LauncherContext()
        {
            splash = new StartupForm();
            splash.RetryRequested += delegate { StartNexo(true); };
            splash.InstallRequested += delegate { LauncherRuntime.OpenInstaller(); };
            splash.OpenLogRequested += delegate { LauncherRuntime.OpenLog(); };
            splash.ExitRequested += delegate { ExitThread(); };

            ContextMenuStrip menu = new ContextMenuStrip();
            menu.BackColor = Color.FromArgb(12, 16, 22);
            menu.ForeColor = Color.FromArgb(222, 250, 255);
            menu.Items.Add("Abrir Nexo", null, delegate { LauncherRuntime.OpenUi("/"); });
            menu.Items.Add("Configurações ADM", null, delegate { LauncherRuntime.OpenUi("/admin"); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Reaplicar microfone", null, delegate { RepairMicrophone(); });
            menu.Items.Add("Reiniciar serviços", null, delegate { RestartServices(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Sair do inicializador", null, delegate { ExitThread(); });

            Icon appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            tray = new NotifyIcon();
            tray.Icon = appIcon == null ? SystemIcons.Application : appIcon;
            tray.Text = "Nexo 2.0 — assistente pessoal";
            tray.ContextMenuStrip = menu;
            tray.Visible = true;
            tray.DoubleClick += delegate { LauncherRuntime.OpenUi("/"); };

            splash.Show();
            StartNexo(true);
        }

        private void StartNexo(bool openUi)
        {
            if (Interlocked.CompareExchange(ref operationRunning, 1, 0) != 0)
            {
                return;
            }
            Ui(delegate { splash.ShowWorking("INICIALIZANDO O NEXO", "Preparando o ambiente local"); });

            ThreadPool.QueueUserWorkItem(delegate
            {
                StartupResult result = LauncherRuntime.EnsureStarted(UpdateStatus);
                Ui(delegate
                {
                    Interlocked.Exchange(ref operationRunning, 0);
                    if (!result.Success)
                    {
                        splash.ShowFailure(result.Message, result.Details, result.NeedsInstall);
                        tray.ShowBalloonTip(5000, "Nexo não iniciou", result.Message, ToolTipIcon.Error);
                        return;
                    }

                    splash.ShowReady(result.Message);
                    tray.ShowBalloonTip(
                        2500,
                        "Nexo disponível",
                        string.IsNullOrEmpty(result.Warning)
                            ? "Escuta, interface e ações estão prontas."
                            : result.Warning,
                        string.IsNullOrEmpty(result.Warning) ? ToolTipIcon.Info : ToolTipIcon.Warning
                    );
                    System.Windows.Forms.Timer finish = new System.Windows.Forms.Timer();
                    finish.Interval = 650;
                    finish.Tick += delegate
                    {
                        finish.Stop();
                        finish.Dispose();
                        if (openUi) LauncherRuntime.OpenUi("/");
                        splash.Hide();
                    };
                    finish.Start();
                });
            });
        }

        private void RepairMicrophone()
        {
            RunTrayOperation(
                "Reaplicando microfone",
                "A cadeia WSLg, o cancelamento de eco e o listener serão verificados.",
                delegate { return LauncherRuntime.RepairMicrophone(UpdateStatus); },
                "Microfone reaplicado"
            );
        }

        private void RestartServices()
        {
            RunTrayOperation(
                "Reiniciando o Nexo",
                "Os serviços locais serão recarregados com segurança.",
                delegate { return LauncherRuntime.RestartNexo(UpdateStatus); },
                "Serviços reiniciados"
            );
        }

        private void RunTrayOperation(
            string title,
            string subtitle,
            Func<StartupResult> operation,
            string successTitle)
        {
            if (Interlocked.CompareExchange(ref operationRunning, 1, 0) != 0)
            {
                tray.ShowBalloonTip(1800, "Nexo ocupado", "Já existe uma operação em andamento.", ToolTipIcon.Info);
                return;
            }
            Ui(delegate
            {
                splash.ShowWorking(title.ToUpperInvariant(), subtitle);
                splash.Show();
                splash.Activate();
            });
            ThreadPool.QueueUserWorkItem(delegate
            {
                StartupResult result = operation();
                Ui(delegate
                {
                    Interlocked.Exchange(ref operationRunning, 0);
                    if (result.Success)
                    {
                        splash.ShowReady(result.Message);
                        tray.ShowBalloonTip(2500, successTitle, result.Message, ToolTipIcon.Info);
                        System.Windows.Forms.Timer hide = new System.Windows.Forms.Timer();
                        hide.Interval = 900;
                        hide.Tick += delegate { hide.Stop(); hide.Dispose(); splash.Hide(); };
                        hide.Start();
                    }
                    else
                    {
                        splash.ShowFailure(result.Message, result.Details, result.NeedsInstall);
                        tray.ShowBalloonTip(5000, "Operação não concluída", result.Message, ToolTipIcon.Error);
                    }
                });
            });
        }

        private void UpdateStatus(string title, string detail)
        {
            Ui(delegate { splash.UpdateStatus(title, detail); });
        }

        private void Ui(Action action)
        {
            if (splash.IsDisposed) return;
            if (splash.InvokeRequired)
            {
                try { splash.BeginInvoke(action); }
                catch (InvalidOperationException) { }
            }
            else
            {
                action();
            }
        }

        protected override void ExitThreadCore()
        {
            tray.Visible = false;
            tray.Dispose();
            splash.Close();
            splash.Dispose();
            base.ExitThreadCore();
        }
    }

    internal sealed class StartupForm : Form
    {
        private readonly HologramControl hologram;
        private readonly Label title;
        private readonly Label detail;
        private readonly Label brand;
        private readonly Button retry;
        private readonly Button openLog;
        private readonly Button close;
        private Point dragOrigin;
        private bool installAction;

        public event EventHandler RetryRequested;
        public event EventHandler InstallRequested;
        public event EventHandler OpenLogRequested;
        public event EventHandler ExitRequested;

        public StartupForm()
        {
            Text = "Nexo";
            ClientSize = new Size(520, 458);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.None;
            BackColor = Color.FromArgb(2, 5, 10);
            ForeColor = Color.White;
            ShowInTaskbar = true;
            TopMost = false;
            DoubleBuffered = true;

            brand = MakeLabel("N E X O  2.0  //  CORE", 11, FontStyle.Bold, Color.FromArgb(105, 220, 255));
            brand.Location = new Point(26, 20);
            brand.Size = new Size(360, 22);

            close = new Button();
            close.Text = "×";
            close.Font = new Font("Segoe UI Light", 18, FontStyle.Regular);
            close.FlatStyle = FlatStyle.Flat;
            close.FlatAppearance.BorderSize = 0;
            close.ForeColor = Color.FromArgb(120, 190, 205);
            close.BackColor = BackColor;
            close.Location = new Point(470, 8);
            close.Size = new Size(40, 38);
            close.Cursor = Cursors.Hand;
            close.Click += delegate
            {
                EventHandler handler = ExitRequested;
                if (handler != null) handler(this, EventArgs.Empty);
            };

            hologram = new HologramControl();
            hologram.Location = new Point(115, 50);
            hologram.Size = new Size(290, 245);

            title = MakeLabel("INICIALIZANDO O NEXO", 14, FontStyle.Bold, Color.FromArgb(230, 252, 255));
            title.TextAlign = ContentAlignment.MiddleCenter;
            title.Location = new Point(30, 297);
            title.Size = new Size(460, 30);

            detail = MakeLabel("Preparando o ambiente local", 9, FontStyle.Regular, Color.FromArgb(105, 166, 184));
            detail.TextAlign = ContentAlignment.TopCenter;
            detail.Location = new Point(36, 331);
            detail.Size = new Size(448, 76);

            retry = new Button();
            retry.Text = "TENTAR NOVAMENTE";
            retry.Font = new Font("Segoe UI", 8, FontStyle.Bold);
            retry.FlatStyle = FlatStyle.Flat;
            retry.FlatAppearance.BorderColor = Color.FromArgb(40, 205, 235);
            retry.FlatAppearance.BorderSize = 1;
            retry.ForeColor = Color.FromArgb(185, 245, 255);
            retry.BackColor = Color.FromArgb(7, 29, 38);
            retry.Location = new Point(94, 414);
            retry.Size = new Size(156, 30);
            retry.Cursor = Cursors.Hand;
            retry.Visible = false;
            retry.Click += delegate
            {
                EventHandler handler = installAction ? InstallRequested : RetryRequested;
                if (handler != null) handler(this, EventArgs.Empty);
            };

            openLog = new Button();
            openLog.Text = "ABRIR DIAGNÓSTICO";
            openLog.Font = new Font("Segoe UI", 8, FontStyle.Bold);
            openLog.FlatStyle = FlatStyle.Flat;
            openLog.FlatAppearance.BorderColor = Color.FromArgb(73, 99, 112);
            openLog.FlatAppearance.BorderSize = 1;
            openLog.ForeColor = Color.FromArgb(166, 205, 216);
            openLog.BackColor = Color.FromArgb(7, 16, 23);
            openLog.Location = new Point(270, 414);
            openLog.Size = new Size(156, 30);
            openLog.Cursor = Cursors.Hand;
            openLog.Visible = false;
            openLog.Click += delegate
            {
                EventHandler handler = OpenLogRequested;
                if (handler != null) handler(this, EventArgs.Empty);
            };

            Controls.Add(brand);
            Controls.Add(close);
            Controls.Add(hologram);
            Controls.Add(title);
            Controls.Add(detail);
            Controls.Add(retry);
            Controls.Add(openLog);

            MouseDown += BeginDrag;
            brand.MouseDown += BeginDrag;
            title.MouseDown += BeginDrag;
            MouseMove += ContinueDrag;
            brand.MouseMove += ContinueDrag;
            title.MouseMove += ContinueDrag;
            MouseUp += EndDrag;
            brand.MouseUp += EndDrag;
            title.MouseUp += EndDrag;
        }

        private static Label MakeLabel(string text, float size, FontStyle style, Color color)
        {
            Label label = new Label();
            label.Text = text;
            label.Font = new Font("Segoe UI", size, style);
            label.ForeColor = color;
            label.BackColor = Color.Transparent;
            return label;
        }

        public void ShowWorking(string heading, string message)
        {
            title.Text = heading;
            title.ForeColor = Color.FromArgb(230, 252, 255);
            detail.Text = message;
            installAction = false;
            retry.Text = "TENTAR NOVAMENTE";
            retry.Visible = false;
            openLog.Visible = false;
            hologram.Mode = HologramMode.Working;
        }

        public void UpdateStatus(string heading, string message)
        {
            title.Text = heading.ToUpperInvariant();
            detail.Text = message;
        }

        public void ShowReady(string message)
        {
            title.Text = "NEXO DISPONÍVEL";
            title.ForeColor = Color.FromArgb(128, 255, 228);
            detail.Text = message;
            installAction = false;
            retry.Text = "TENTAR NOVAMENTE";
            retry.Visible = false;
            openLog.Visible = false;
            hologram.Mode = HologramMode.Ready;
        }

        public void ShowFailure(string message, string diagnostics, bool needsInstall)
        {
            title.Text = "INICIALIZAÇÃO INTERROMPIDA";
            title.ForeColor = Color.FromArgb(255, 135, 160);
            detail.Text = message + (string.IsNullOrEmpty(diagnostics) ? "" : "\n" + diagnostics);
            installAction = needsInstall;
            retry.Text = needsInstall ? "INSTALAR / REPARAR" : "TENTAR NOVAMENTE";
            retry.Visible = true;
            openLog.Visible = true;
            hologram.Mode = HologramMode.Error;
        }

        private void BeginDrag(object sender, MouseEventArgs e)
        {
            if (e.Button == MouseButtons.Left) dragOrigin = e.Location;
        }

        private void ContinueDrag(object sender, MouseEventArgs e)
        {
            if (e.Button != MouseButtons.Left) return;
            Point screen = ((Control)sender).PointToScreen(e.Location);
            Location = new Point(screen.X - dragOrigin.X, screen.Y - dragOrigin.Y);
        }

        private void EndDrag(object sender, MouseEventArgs e) { }
    }

    internal enum HologramMode { Working, Ready, Error }

    internal sealed class HologramControl : Control
    {
        private readonly System.Windows.Forms.Timer timer;
        private readonly List<PointF> particles = new List<PointF>();
        private float phase;
        public HologramMode Mode { get; set; }

        public HologramControl()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.ResizeRedraw | ControlStyles.UserPaint | ControlStyles.SupportsTransparentBackColor, true);
            BackColor = Color.Transparent;
            Mode = HologramMode.Working;
            Random random = new Random(4070);
            for (int i = 0; i < 38; i++)
            {
                particles.Add(new PointF((float)random.NextDouble(), (float)random.NextDouble()));
            }
            timer = new System.Windows.Forms.Timer();
            timer.Interval = 33;
            timer.Tick += delegate { phase += 0.045f; Invalidate(); };
            timer.Start();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            float cx = Width / 2f + (float)Math.Sin(phase * 0.7) * 4f;
            float cy = Height / 2f + (float)Math.Cos(phase * 0.9) * 3f;
            Color core = Mode == HologramMode.Error
                ? Color.FromArgb(255, 78, 125)
                : (Mode == HologramMode.Ready ? Color.FromArgb(77, 255, 212) : Color.FromArgb(46, 220, 255));

            for (int i = 5; i >= 1; i--)
            {
                float pulse = (float)Math.Sin(phase * 1.3 + i) * 3f;
                float radius = 48f + i * 12f + pulse;
                using (Pen pen = new Pen(Color.FromArgb(12 + i * 7, core), 1.1f))
                {
                    g.DrawEllipse(pen, cx - radius, cy - radius, radius * 2f, radius * 2f);
                }
            }

            GraphicsPath path = new GraphicsPath();
            const int points = 96;
            PointF[] membrane = new PointF[points];
            for (int i = 0; i < points; i++)
            {
                double angle = Math.PI * 2.0 * i / points;
                double wave = Math.Sin(angle * 5 + phase * 2.2) * 5.0 +
                              Math.Cos(angle * 3 - phase * 1.4) * 3.2;
                float radius = 58f + (float)wave;
                membrane[i] = new PointF(cx + (float)Math.Cos(angle) * radius,
                                         cy + (float)Math.Sin(angle) * radius);
            }
            path.AddClosedCurve(membrane, 0.24f);
            using (PathGradientBrush glow = new PathGradientBrush(path))
            {
                glow.CenterColor = Color.FromArgb(150, core);
                glow.SurroundColors = new Color[] { Color.FromArgb(18, core) };
                g.FillPath(glow, path);
            }
            using (Pen edge = new Pen(Color.FromArgb(175, core), 1.4f)) g.DrawPath(edge, path);
            path.Dispose();

            for (int i = 0; i < particles.Count; i++)
            {
                PointF particle = particles[i];
                float angle = particle.X * 6.28318f + phase * (0.18f + particle.Y * 0.22f);
                float radius = 72f + particle.Y * 38f;
                float x = cx + (float)Math.Cos(angle) * radius;
                float y = cy + (float)Math.Sin(angle) * radius * 0.75f;
                float size = 1.2f + particle.Y * 1.8f;
                using (Brush dot = new SolidBrush(Color.FromArgb(55 + (int)(particle.Y * 100), core)))
                {
                    g.FillEllipse(dot, x - size / 2f, y - size / 2f, size, size);
                }
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) timer.Dispose();
            base.Dispose(disposing);
        }
    }

    internal sealed class StartupResult
    {
        public bool Success;
        public string Message;
        public string Details;
        public string Warning;
        public bool NeedsInstall;

        public static StartupResult Ok(string message, string warning)
        {
            return new StartupResult { Success = true, Message = message, Warning = warning };
        }

        public static StartupResult Fail(string message, string details)
        {
            return new StartupResult { Success = false, Message = message, Details = details };
        }

        public static StartupResult SetupRequired(string message, string details)
        {
            return new StartupResult
            {
                Success = false,
                Message = message,
                Details = details,
                NeedsInstall = true
            };
        }
    }

    internal sealed class ProcessResult
    {
        public int ExitCode = -1;
        public string Output = "";
        public string Error = "";
        public bool TimedOut;
    }

    internal static class LauncherLog
    {
        private static readonly object Sync = new object();
        private static readonly string LogDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            @"Nexo\logs");
        public static readonly string FilePath = Path.Combine(LogDirectory, "launcher.log");

        public static void Write(string message)
        {
            try
            {
                lock (Sync)
                {
                    Directory.CreateDirectory(LogDirectory);
                    if (File.Exists(FilePath) && new FileInfo(FilePath).Length > 1024 * 1024)
                    {
                        string previous = FilePath + ".1";
                        if (File.Exists(previous)) File.Delete(previous);
                        File.Move(FilePath, previous);
                    }
                    string safe = Sanitize(message);
                    File.AppendAllText(
                        FilePath,
                        DateTimeOffset.Now.ToString("yyyy-MM-dd HH:mm:ss.fff zzz") +
                        " | " + safe + Environment.NewLine,
                        Encoding.UTF8);
                }
            }
            catch { }
        }

        private static string Sanitize(string value)
        {
            if (string.IsNullOrEmpty(value)) return "(empty)";
            string safe = value.Replace("\0", "").Replace("\r", " ").Replace("\n", " ");
            int bearer = safe.IndexOf("Bearer ", StringComparison.OrdinalIgnoreCase);
            if (bearer >= 0) safe = safe.Substring(0, bearer) + "Bearer [redacted]";
            return safe.Length > 1800 ? safe.Substring(0, 1800) + "..." : safe;
        }
    }

    internal sealed class RuntimeSettings
    {
        public string Distro = "Ubuntu";
        public string WslUser = "";
        public string WslProject = "";
        public string WslPython = "";
        public string WindowsPython = "";
        public string UiBase = "http://127.0.0.1:8787";
        public string ConfigPath = "";
        public string ConfigError = "";
        public bool ConfigPresent;

        public static RuntimeSettings Load()
        {
            RuntimeSettings settings = new RuntimeSettings();
            string root = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Nexo");
            string path = Path.Combine(root, "runtime.conf");
            settings.ConfigPath = path;
            settings.ConfigPresent = File.Exists(path);
            if (!settings.ConfigPresent) return settings;
            try
            {
                foreach (string raw in File.ReadAllLines(path, Encoding.UTF8))
                {
                    string line = raw.Trim();
                    if (line.Length == 0 || line.StartsWith("#")) continue;
                    int separator = line.IndexOf('=');
                    if (separator < 1) continue;
                    string key = line.Substring(0, separator).Trim();
                    string value = line.Substring(separator + 1).Trim();
                    if (value.Length == 0 || value.Contains("\r") || value.Contains("\n")) continue;
                    if (key == "distro") settings.Distro = value;
                    else if (key == "wsl_user") settings.WslUser = value;
                    else if (key == "wsl_project") settings.WslProject = value;
                    else if (key == "wsl_python") settings.WslPython = value;
                    else if (key == "windows_python") settings.WindowsPython = value;
                    else if (key == "ui_base" && value.TrimEnd('/') == "http://127.0.0.1:8787")
                        settings.UiBase = "http://127.0.0.1:8787";
                }
            }
            catch (Exception error)
            {
                settings.ConfigError = error.Message;
            }
            return settings;
        }
    }

    internal static class LauncherRuntime
    {
        private static RuntimeSettings Settings = RuntimeSettings.Load();
        private const string EssentialServices =
            "ovos-messagebus.service nexo-pulse-aec.service ovos-audio.service " +
            "ovos-listener.service ovos-core.service nexo-audio-watchdog.service nexo-ui.service";

        private static readonly string WslExe = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.System), "wsl.exe");

        public static StartupResult EnsureStarted(Action<string, string> status)
        {
            try
            {
                Settings = RuntimeSettings.Load();
                LauncherLog.Write("EnsureStarted: beginning startup validation.");
                if (!Settings.ConfigPresent)
                {
                    LauncherLog.Write("EnsureStarted: runtime.conf is missing at " + Settings.ConfigPath);
                    return StartupResult.SetupRequired(
                        "O Nexo ainda não foi instalado neste Windows.",
                        "Execute o NexoSetup.exe. Ele preserva seus dados e configura o WSL automaticamente.");
                }
                if (!string.IsNullOrEmpty(Settings.ConfigError))
                {
                    LauncherLog.Write("EnsureStarted: runtime.conf could not be read: " + Settings.ConfigError);
                    return StartupResult.SetupRequired(
                        "A configuração local do Nexo precisa ser reparada.",
                        "Execute o NexoSetup.exe e escolha Instalar/Atualizar.");
                }
                if (!File.Exists(WslExe))
                    return StartupResult.SetupRequired(
                        "O Subsistema do Windows para Linux não está disponível.",
                        "Execute o NexoSetup.exe para concluir os pré-requisitos.");

                status("Conectando ao núcleo", "Aguardando o Ubuntu e o systemd do usuário");
                ProcessResult warm = WaitForUserSystemd(55);
                LogResult("wsl-user-systemd", warm);
                if (warm.TimedOut || warm.ExitCode != 0)
                {
                    return StartupResult.Fail(
                        "Não consegui preparar o ambiente WSL do Nexo.",
                        Compact(warm.Error + " " + warm.Output));
                }

                ProcessResult units = RunWsl(
                    "systemctl --user cat ovos-core.service nexo-ui.service >/dev/null 2>&1",
                    20000);
                LogResult("runtime-units", units);
                if (units.TimedOut || units.ExitCode != 0)
                {
                    return StartupResult.SetupRequired(
                        "A instalação do Nexo está incompleta.",
                        "Execute o NexoSetup.exe para instalar ou reparar os serviços.");
                }

                ProcessResult start = StartServicesWithRetry(status, 3);
                LogResult("essential-services-start", start);

                status("Validando os módulos", "Escuta, voz, inteligência e interface");
                ProcessResult active = CheckEssentialServices();
                LogResult("essential-services-final", active);
                if (active.TimedOut || active.ExitCode != 0)
                {
                    return StartupResult.Fail(
                        "Um dos módulos essenciais não ficou ativo.",
                        Compact(active.Output + " " + active.Error));
                }

                RunWsl(
                    "for unit in ovos-phal.service nexo-neural-tts.service; do " +
                    "systemctl --user cat \"$unit\" >/dev/null 2>&1 && " +
                    "systemctl --user start --no-block \"$unit\" || true; done",
                    15000);

                status("Conectando ao Windows", "Verificando a ponte segura de ações");
                string warning = EnsureBridge()
                    ? ""
                    : "A interface abriu, mas a ponte de ações do Windows precisa ser revisada.";

                status("Sincronizando a interface", "Aguardando o holograma responder");
                if (!WaitForUi(60))
                {
                    ProcessResult logs = RunWsl(
                        "journalctl --user -u nexo-ui.service -n 12 --no-pager",
                        12000);
                    return StartupResult.Fail(
                        "A interface holográfica não respondeu.",
                        Compact(logs.Output + " " + logs.Error));
                }

                LauncherLog.Write("EnsureStarted: startup completed successfully.");
                return StartupResult.Ok("Todos os sistemas estão operacionais.", warning);
            }
            catch (Exception error)
            {
                LauncherLog.Write("EnsureStarted: unexpected exception: " + error.Message);
                return StartupResult.Fail("Falha inesperada ao iniciar o Nexo.", Compact(error.Message));
            }
        }

        private static ProcessResult WaitForUserSystemd(int seconds)
        {
            Stopwatch deadline = Stopwatch.StartNew();
            TimeSpan limit = TimeSpan.FromSeconds(seconds);
            ProcessResult last = new ProcessResult { Error = "O systemd do usuário não respondeu." };
            int attempt = 0;
            while (deadline.Elapsed < limit)
            {
                attempt++;
                int remaining = (int)(limit - deadline.Elapsed).TotalMilliseconds;
                if (remaining <= 0) break;
                last = RunWsl(
                    "systemctl --user show-environment >/dev/null",
                    Math.Max(250, Math.Min(12000, remaining)));
                if (!last.TimedOut && last.ExitCode == 0) return last;
                LogResult("wsl-user-systemd-attempt-" + attempt, last);
                remaining = (int)(limit - deadline.Elapsed).TotalMilliseconds;
                if (remaining <= 0) break;
                Thread.Sleep(Math.Min(1250, remaining));
            }
            last.TimedOut = true;
            return last;
        }

        private static ProcessResult StartServicesWithRetry(
            Action<string, string> status,
            int attempts)
        {
            ProcessResult last = new ProcessResult { Error = "Os serviços não responderam." };
            for (int attempt = 1; attempt <= attempts; attempt++)
            {
                status(
                    "Inicializando os módulos",
                    attempt == 1
                        ? "Ativando escuta, voz, inteligência e interface"
                        : "Confirmando os módulos (tentativa " + attempt + " de " + attempts + ")");

                ProcessResult start = RunWsl(
                    "systemctl --user start --no-block " + EssentialServices,
                    30000);
                LogResult("service-start-attempt-" + attempt, start);

                Stopwatch settle = Stopwatch.StartNew();
                TimeSpan settleLimit = TimeSpan.FromSeconds(18);
                while (settle.Elapsed < settleLimit)
                {
                    int remaining = (int)(settleLimit - settle.Elapsed).TotalMilliseconds;
                    if (remaining <= 0) break;
                    last = CheckEssentialServices(Math.Max(250, Math.Min(15000, remaining)));
                    if (!last.TimedOut && last.ExitCode == 0) return last;
                    remaining = (int)(settleLimit - settle.Elapsed).TotalMilliseconds;
                    if (remaining > 0) Thread.Sleep(Math.Min(750, remaining));
                }

                if (attempt < attempts)
                {
                    RunWsl("systemctl --user reset-failed >/dev/null 2>&1 || true", 10000);
                    Thread.Sleep(900 * attempt);
                }
            }
            return last;
        }

        private static ProcessResult CheckEssentialServices()
        {
            return CheckEssentialServices(15000);
        }

        private static ProcessResult CheckEssentialServices(int timeout)
        {
            return RunWsl(
                "failed=0; for unit in " + EssentialServices + "; do " +
                "state=$(systemctl --user is-active \"$unit\" 2>&1); " +
                "printf '%s=%s\\n' \"$unit\" \"$state\"; " +
                "[ \"$state\" = active ] || failed=1; done; exit $failed",
                timeout);
        }

        private static void LogResult(string stage, ProcessResult result)
        {
            if (result == null)
            {
                LauncherLog.Write(stage + ": no process result.");
                return;
            }
            LauncherLog.Write(
                stage + ": exit=" + result.ExitCode +
                " timedOut=" + result.TimedOut +
                " output=" + LogValue(result.Output) +
                " error=" + LogValue(result.Error));
        }

        private static string LogValue(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? "(none)" : Compact(value);
        }

        public static StartupResult RepairMicrophone(Action<string, string> status)
        {
            status("Verificando a entrada", "Aguardando a placa de captura no Windows");
            ProcessResult result = RunWsl(
                "cd " + ShellQuote(ProjectPath()) + " && " + ShellQuote(PythonPath()) +
                " -m nexo_actions.audio_recovery repair --reason launcher-windows",
                120000);
            if (result.TimedOut || result.ExitCode != 0)
            {
                return StartupResult.Fail("Não consegui reaplicar o microfone.", Compact(result.Error + " " + result.Output));
            }
            return StartupResult.Ok("Microfone, AEC e listener estão operacionais.", "");
        }

        public static StartupResult RestartNexo(Action<string, string> status)
        {
            status("Reiniciando os módulos", "Preservando configurações e dados locais");
            ProcessResult result = RunWsl(
                "systemctl --user restart nexo-pulse-aec.service ovos-audio.service " +
                "ovos-listener.service ovos-core.service nexo-audio-watchdog.service nexo-ui.service && " +
                "(systemctl --user list-unit-files nexo-neural-tts.service --no-legend 2>/dev/null | " +
                "grep -q nexo-neural-tts && systemctl --user restart nexo-neural-tts.service || true)",
                120000);
            if (result.TimedOut || result.ExitCode != 0)
            {
                return StartupResult.Fail("Nem todos os serviços reiniciaram.", Compact(result.Error + " " + result.Output));
            }
            if (!WaitForUi(45))
            {
                return StartupResult.Fail("Os serviços voltaram, mas a interface ainda não respondeu.", "");
            }
            return StartupResult.Ok("O Nexo foi recarregado e está disponível.", "");
        }

        public static void OpenInstaller()
        {
            string executableDirectory = Path.GetDirectoryName(Application.ExecutablePath);
            string candidate = Path.Combine(executableDirectory, "NexoSetup.exe");
            if (File.Exists(candidate))
            {
                try
                {
                    LauncherLog.Write("Opening installer: " + candidate);
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = candidate,
                        WorkingDirectory = Path.GetDirectoryName(candidate),
                        UseShellExecute = true
                    });
                    return;
                }
                catch (Exception error)
                {
                    LauncherLog.Write("Could not open installer: " + error.Message);
                }
            }
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "https://github.com/SamTecDev7/nexo-actions/releases/latest",
                    UseShellExecute = true
                });
            }
            catch (Exception error)
            {
                LauncherLog.Write("Could not open installer download page: " + error.Message);
            }
        }

        public static void OpenLog()
        {
            LauncherLog.Write("Opening launcher diagnostic log.");
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = LauncherLog.FilePath,
                    UseShellExecute = true
                });
            }
            catch
            {
                try
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = "notepad.exe",
                        Arguments = QuoteWindowsArgument(LauncherLog.FilePath),
                        UseShellExecute = false
                    });
                }
                catch { }
            }
        }

        public static void OpenUi(string path)
        {
            string url = Settings.UiBase + path;
            string edge86 = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                @"Microsoft\Edge\Application\msedge.exe");
            string edge64 = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                @"Microsoft\Edge\Application\msedge.exe");
            string chrome = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                @"Google\Chrome\Application\chrome.exe");

            string browser = File.Exists(edge86) ? edge86 : (File.Exists(edge64) ? edge64 : chrome);
            if (File.Exists(browser))
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = browser,
                    Arguments = "--app=\"" + url + "\" --start-maximized",
                    UseShellExecute = true
                });
            }
            else
            {
                Process.Start(new ProcessStartInfo { FileName = url, UseShellExecute = true });
            }
        }

        private static bool EnsureBridge()
        {
            if (BridgeHealthy()) return true;

            string scheduledTasks = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System), "schtasks.exe");
            RunProcess(scheduledTasks, "/Run /TN \"Nexo Windows Bridge\"", 10000, true);
            for (int i = 0; i < 12; i++)
            {
                Thread.Sleep(250);
                if (BridgeHealthy()) return true;
            }

            string root = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Nexo");
            string server = Path.Combine(root, @"bridge\server.py");
            string token = Path.Combine(root, "bridge-token");
            string pyw = "";
            bool pythonLauncher = false;
            if (!string.IsNullOrEmpty(Settings.WindowsPython) && File.Exists(Settings.WindowsPython))
            {
                string configuredPythonw = Path.Combine(
                    Path.GetDirectoryName(Settings.WindowsPython), "pythonw.exe");
                pyw = File.Exists(configuredPythonw) ? configuredPythonw : Settings.WindowsPython;
            }
            if (string.IsNullOrEmpty(pyw))
            {
                string launcher = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Windows), "pyw.exe");
                if (File.Exists(launcher))
                {
                    pyw = launcher;
                    pythonLauncher = true;
                }
            }
            if (string.IsNullOrEmpty(pyw))
            {
                string programs = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    @"Programs\Python");
                if (Directory.Exists(programs))
                {
                    string[] candidates = Directory.GetFiles(
                        programs, "pythonw.exe", SearchOption.AllDirectories);
                    Array.Sort(candidates, StringComparer.OrdinalIgnoreCase);
                    if (candidates.Length > 0) pyw = candidates[candidates.Length - 1];
                }
            }
            if (!File.Exists(server) || !File.Exists(token) || !File.Exists(pyw)) return false;

            string arguments = (pythonLauncher ? "-3 " : "") + "\"" + server +
                               "\" --host 127.0.0.1 --port 8765 " +
                               "--token-file \"" + token + "\" --log-file \"" +
                               Path.Combine(root, "bridge.log") + "\" --pid-file \"" +
                               Path.Combine(root, "bridge.pid") + "\"";
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = pyw,
                    Arguments = arguments,
                    WorkingDirectory = Path.Combine(root, "bridge"),
                    UseShellExecute = false,
                    CreateNoWindow = true
                });
            }
            catch { return false; }

            for (int i = 0; i < 20; i++)
            {
                Thread.Sleep(250);
                if (BridgeHealthy()) return true;
            }
            return false;
        }

        private static bool BridgeHealthy()
        {
            string root = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Nexo");
            string tokenPath = Path.Combine(root, "bridge-token");
            if (!File.Exists(tokenPath)) return false;
            try
            {
                string token = File.ReadAllText(tokenPath, Encoding.UTF8).Trim();
                if (token.Length < 32) return false;
                byte[] payload = Encoding.UTF8.GetBytes("{\"method\":\"health\",\"params\":{}}");
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(
                    "http://127.0.0.1:8765/rpc");
                request.Method = "POST";
                request.ContentType = "application/json";
                request.ContentLength = payload.Length;
                request.Timeout = 1200;
                request.ReadWriteTimeout = 1200;
                request.Proxy = null;
                request.Headers[HttpRequestHeader.Authorization] = "Bearer " + token;
                using (Stream stream = request.GetRequestStream())
                    stream.Write(payload, 0, payload.Length);
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream(), Encoding.UTF8))
                {
                    string body = reader.ReadToEnd().Replace(" ", "");
                    return (int)response.StatusCode == 200 && body.Contains("\"ok\":true");
                }
            }
            catch { return false; }
        }

        private static bool WaitForUi(int seconds)
        {
            Stopwatch deadline = Stopwatch.StartNew();
            TimeSpan limit = TimeSpan.FromSeconds(seconds);
            while (deadline.Elapsed < limit)
            {
                try
                {
                    int remaining = (int)(limit - deadline.Elapsed).TotalMilliseconds;
                    if (remaining <= 0) break;
                    HttpWebRequest request = (HttpWebRequest)WebRequest.Create(Settings.UiBase + "/api/state");
                    request.Timeout = Math.Max(100, Math.Min(1200, remaining));
                    request.ReadWriteTimeout = request.Timeout;
                    request.Proxy = null;
                    using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                    {
                        if ((int)response.StatusCode >= 200 && (int)response.StatusCode < 300) return true;
                    }
                }
                catch { }
                int sleep = (int)(limit - deadline.Elapsed).TotalMilliseconds;
                if (sleep > 0) Thread.Sleep(Math.Min(500, sleep));
            }
            return false;
        }

        private static bool PortOpen(int port, int timeout)
        {
            TcpClient client = new TcpClient();
            try
            {
                IAsyncResult pending = client.BeginConnect(IPAddress.Loopback, port, null, null);
                if (!pending.AsyncWaitHandle.WaitOne(timeout)) return false;
                client.EndConnect(pending);
                return true;
            }
            catch { return false; }
            finally { client.Close(); }
        }

        private static ProcessResult RunWsl(string command, int timeout)
        {
            string distro = ValidateDistro(Settings.Distro);
            string userArgument = string.IsNullOrEmpty(Settings.WslUser)
                ? ""
                : " -u " + QuoteWindowsArgument(ValidateWslUser(Settings.WslUser));
            return RunProcess(
                WslExe,
                "-d " + QuoteWindowsArgument(distro) + userArgument +
                " --exec bash -lc " + QuoteWindowsArgument(command),
                timeout,
                true);
        }

        private static ProcessResult RunProcess(string file, string arguments, int timeout, bool hidden)
        {
            ProcessResult result = new ProcessResult();
            using (Process process = new Process())
            using (MemoryStream output = new MemoryStream())
            using (MemoryStream error = new MemoryStream())
            {
                process.StartInfo = new ProcessStartInfo
                {
                    FileName = file,
                    Arguments = arguments,
                    UseShellExecute = false,
                    CreateNoWindow = hidden,
                    WindowStyle = hidden ? ProcessWindowStyle.Hidden : ProcessWindowStyle.Normal,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                process.Start();
                Thread outputReader = StartStreamReader(process.StandardOutput.BaseStream, output);
                Thread errorReader = StartStreamReader(process.StandardError.BaseStream, error);
                if (!process.WaitForExit(timeout))
                {
                    result.TimedOut = true;
                    try { process.Kill(); }
                    catch { }
                    try { process.WaitForExit(5000); }
                    catch { }
                }
                else
                {
                    process.WaitForExit();
                    result.ExitCode = process.ExitCode;
                }
                if (!outputReader.Join(3000))
                {
                    try { process.StandardOutput.Close(); }
                    catch { }
                    outputReader.Join(1000);
                }
                if (!errorReader.Join(3000))
                {
                    try { process.StandardError.Close(); }
                    catch { }
                    errorReader.Join(1000);
                }
                result.Output = DecodeProcessOutput(Snapshot(output));
                result.Error = DecodeProcessOutput(Snapshot(error));
            }
            return result;
        }

        private static Thread StartStreamReader(Stream source, MemoryStream destination)
        {
            Thread reader = new Thread(delegate()
            {
                byte[] buffer = new byte[4096];
                try
                {
                    int count;
                    while ((count = source.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        lock (destination) destination.Write(buffer, 0, count);
                    }
                }
                catch { }
            });
            reader.IsBackground = true;
            reader.Start();
            return reader;
        }

        private static byte[] Snapshot(MemoryStream stream)
        {
            lock (stream) return stream.ToArray();
        }

        private static string DecodeProcessOutput(byte[] bytes)
        {
            if (bytes == null || bytes.Length == 0) return "";
            int offset = 0;
            Encoding encoding = new UTF8Encoding(false, false);
            if (bytes.Length >= 2 && bytes[0] == 0xFF && bytes[1] == 0xFE)
            {
                encoding = Encoding.Unicode;
                offset = 2;
            }
            else if (bytes.Length >= 2 && bytes[0] == 0xFE && bytes[1] == 0xFF)
            {
                encoding = Encoding.BigEndianUnicode;
                offset = 2;
            }
            else if (bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF)
            {
                offset = 3;
            }
            else
            {
                int sample = Math.Min(bytes.Length, 1024);
                int pairs = sample / 2;
                int oddZeros = 0;
                int evenZeros = 0;
                for (int i = 0; i + 1 < sample; i += 2)
                {
                    if (bytes[i] == 0) evenZeros++;
                    if (bytes[i + 1] == 0) oddZeros++;
                }
                if (pairs >= 2 && oddZeros * 100 / pairs >= 30 && oddZeros > evenZeros * 2)
                    encoding = Encoding.Unicode;
                else if (pairs >= 2 && evenZeros * 100 / pairs >= 30 && evenZeros > oddZeros * 2)
                    encoding = Encoding.BigEndianUnicode;
            }
            return encoding.GetString(bytes, offset, bytes.Length - offset)
                .Replace("\0", "").TrimStart('\uFEFF');
        }

        private static string ValidateDistro(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) throw new InvalidOperationException("Distribuição WSL não configurada.");
            foreach (char character in value)
            {
                if (!(char.IsLetterOrDigit(character) || character == ' ' || character == '-' ||
                      character == '_' || character == '.'))
                    throw new InvalidOperationException("Nome de distribuição WSL inválido.");
            }
            return value;
        }

        private static string ValidateWslUser(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return "";
            foreach (char character in value)
            {
                if (!(char.IsLetterOrDigit(character) || character == '-' ||
                      character == '_' || character == '.'))
                    throw new InvalidOperationException("Usuário WSL inválido.");
            }
            return value;
        }

        private static string QuoteWindowsArgument(string value)
        {
            if (value == null) return "\"\"";
            bool needsQuotes = value.Length == 0;
            foreach (char character in value)
                if (char.IsWhiteSpace(character) || character == '\"') needsQuotes = true;
            if (!needsQuotes) return value;

            StringBuilder quoted = new StringBuilder("\"");
            int backslashes = 0;
            foreach (char character in value)
            {
                if (character == '\\')
                {
                    backslashes++;
                    continue;
                }
                if (character == '\"')
                {
                    quoted.Append('\\', backslashes * 2 + 1);
                    quoted.Append('\"');
                    backslashes = 0;
                    continue;
                }
                quoted.Append('\\', backslashes);
                backslashes = 0;
                quoted.Append(character);
            }
            quoted.Append('\\', backslashes * 2);
            quoted.Append('\"');
            return quoted.ToString();
        }

        private static string Compact(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return "Abra o diagnóstico do launcher para mais detalhes.";
            string compact = value.Replace("\0", "").Replace("\r", " ").Replace("\n", " ").Trim();
            while (compact.Contains("  ")) compact = compact.Replace("  ", " ");
            return compact.Length > 260 ? compact.Substring(0, 257) + "..." : compact;
        }

        private static string ProjectPath()
        {
            return string.IsNullOrEmpty(Settings.WslProject)
                ? Environment.ExpandEnvironmentVariables("$HOME/nexo-actions")
                : Settings.WslProject;
        }

        private static string PythonPath()
        {
            return string.IsNullOrEmpty(Settings.WslPython)
                ? "$HOME/.venvs/ovos/bin/python"
                : Settings.WslPython;
        }

        private static string ShellQuote(string value)
        {
            if (value.StartsWith("$HOME/"))
                return "\"$HOME/" + value.Substring(6).Replace("\"", "") + "\"";
            return "'" + value.Replace("'", "'\\''") + "'";
        }
    }
}
