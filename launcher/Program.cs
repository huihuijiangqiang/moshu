using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Windows.Forms;

namespace Moshu.Launcher;

internal static class Program
{
    private const string FrontendUrl = "http://127.0.0.1:5180/";
    private const string ReadyUrl = "http://127.0.0.1:8000/health/ready";
    private const string DockerDesktopUrl = "https://www.docker.com/products/docker-desktop/";

    [STAThread]
    private static void Main(string[] args)
    {
        try
        {
            RunAsync(args).GetAwaiter().GetResult();
        }
        catch (Exception exception)
        {
            ShowError(exception.Message);
        }
    }

    private static async Task RunAsync(string[] args)
    {
        var options = LauncherOptions.Parse(args);
        var bundleRoot = FindBundleRoot();
        var dataRoot = options.DataDirectory ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Moshu", "data");
        Directory.CreateDirectory(dataRoot);

        if (!File.Exists(Path.Combine(bundleRoot, "docker-compose.yml")))
        {
            throw new InvalidOperationException(
                "没有找到 docker-compose.yml。请将 Moshu.exe 放在 Windows 发布包根目录中。\n\n" +
                "Windows 发布包包含当前版本源码和 Compose 配置，不能只复制单独的 EXE。");
        }

        if (options.Stop)
        {
            await RunDockerCompose(bundleRoot, dataRoot, "down", options).ConfigureAwait(false);
            MessageBox.Show("墨枢服务已停止。", "墨枢", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }

        await EnsureDockerDesktop().ConfigureAwait(false);
        EnsureLocalEnvironment(bundleRoot);
        await RunDockerCompose(bundleRoot, dataRoot, "up -d --build", options).ConfigureAwait(false);
        await WaitForReady(options.Timeout).ConfigureAwait(false);

        if (options.OpenBrowser)
        {
            Process.Start(new ProcessStartInfo(FrontendUrl) { UseShellExecute = true });
        }

        MessageBox.Show(
            $"墨枢已启动。\n\n写作台：{FrontendUrl}\n数据目录：{dataRoot}",
            "墨枢",
            MessageBoxButtons.OK,
            MessageBoxIcon.Information);
    }

    private static string FindBundleRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        for (var depth = 0; depth < 4 && directory is not null; depth++, directory = directory.Parent)
        {
            if (File.Exists(Path.Combine(directory.FullName, "docker-compose.yml")))
            {
                return directory.FullName;
            }
        }

        return AppContext.BaseDirectory;
    }

    private static async Task EnsureDockerDesktop()
    {
        if (await CanRunDocker("info --format {{.ServerVersion}}").ConfigureAwait(false))
        {
            return;
        }

        var candidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Docker", "Docker", "Docker Desktop.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Docker", "Docker Desktop.exe"),
        };
        var executable = candidates.FirstOrDefault(File.Exists);
        if (executable is null)
        {
            Process.Start(new ProcessStartInfo(DockerDesktopUrl) { UseShellExecute = true });
            throw new InvalidOperationException(
                "未检测到 Docker Desktop。请先安装 Docker Desktop，安装完成后重新双击 Moshu.exe。\n\n" +
                $"官方下载：{DockerDesktopUrl}");
        }

        Process.Start(new ProcessStartInfo(executable) { UseShellExecute = true });
        var deadline = DateTime.UtcNow.AddSeconds(180);
        while (DateTime.UtcNow < deadline)
        {
            if (await CanRunDocker("info --format {{.ServerVersion}}").ConfigureAwait(false))
            {
                return;
            }
            await Task.Delay(TimeSpan.FromSeconds(3)).ConfigureAwait(false);
        }

        throw new TimeoutException(
            "Docker Desktop 启动超时。请确认 Docker Desktop 已完成启动，再重新运行 Moshu.exe。\n\n" +
            $"官方下载：{DockerDesktopUrl}");
    }

    private static async Task<bool> CanRunDocker(string arguments)
    {
        try
        {
            var result = await RunProcess("docker", arguments, Directory.GetCurrentDirectory(), null).ConfigureAwait(false);
            return result.ExitCode == 0;
        }
        catch
        {
            return false;
        }
    }

    private static void EnsureLocalEnvironment(string bundleRoot)
    {
        var serverDirectory = Path.Combine(bundleRoot, "server");
        var envPath = Path.Combine(serverDirectory, ".env");
        var examplePath = Path.Combine(serverDirectory, ".env.example");
        if (File.Exists(envPath) || !File.Exists(examplePath))
        {
            return;
        }

        var content = File.ReadAllText(examplePath, Encoding.UTF8);
        content = ReplaceOrAppend(content, "JWT_SECRET_KEY", Convert.ToHexString(RandomNumberGenerator.GetBytes(32)));
        content = ReplaceOrAppend(content, "CREDENTIAL_ENCRYPTION_KEY", Convert.ToHexString(RandomNumberGenerator.GetBytes(32)));
        File.WriteAllText(envPath, content, new UTF8Encoding(false));
    }

    private static string ReplaceOrAppend(string content, string key, string value)
    {
        var pattern = $"(?m)^{System.Text.RegularExpressions.Regex.Escape(key)}=.*$";
        var replacement = $"{key}={value}";
        if (System.Text.RegularExpressions.Regex.IsMatch(content, pattern))
        {
            return System.Text.RegularExpressions.Regex.Replace(content, pattern, replacement);
        }
        return content.TrimEnd() + Environment.NewLine + replacement + Environment.NewLine;
    }

    private static async Task RunDockerCompose(string bundleRoot, string dataRoot, string command, LauncherOptions options)
    {
        var environment = new Dictionary<string, string?>
        {
            ["MOSHU_POSTGRES_DATA_DIR"] = Path.Combine(dataRoot, "postgres"),
            ["MOSHU_REDIS_DATA_DIR"] = Path.Combine(dataRoot, "redis"),
            ["MOSHU_BUILD_REVISION"] = options.BuildRevision,
        };
        Directory.CreateDirectory(environment["MOSHU_POSTGRES_DATA_DIR"]!);
        Directory.CreateDirectory(environment["MOSHU_REDIS_DATA_DIR"]!);
        var result = await RunProcess("docker", $"compose {command}", bundleRoot, environment).ConfigureAwait(false);
        if (result.ExitCode != 0)
        {
            throw new InvalidOperationException(
                $"Docker Compose 执行失败（退出码 {result.ExitCode}）。\n\n{result.Output}");
        }
    }

    private static async Task WaitForReady(TimeSpan timeout)
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
        var deadline = DateTime.UtcNow.Add(timeout);
        while (DateTime.UtcNow < deadline)
        {
            try
            {
                using var response = await client.GetAsync(ReadyUrl).ConfigureAwait(false);
                if (response.IsSuccessStatusCode)
                {
                    return;
                }
            }
            catch (HttpRequestException)
            {
                // API containers need time to build, migrate, and become healthy.
            }
            catch (TaskCanceledException)
            {
                // The short request timeout is expected while the API is starting.
            }
            await Task.Delay(TimeSpan.FromSeconds(2)).ConfigureAwait(false);
        }

        throw new TimeoutException(
            $"墨枢 API 在 {timeout.TotalSeconds:0} 秒内没有就绪。请运行 `docker compose ps` 检查服务状态。 ");
    }

    private static async Task<ProcessResult> RunProcess(
        string fileName,
        string arguments,
        string workingDirectory,
        IReadOnlyDictionary<string, string?>? environment)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            Arguments = arguments,
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        if (environment is not null)
        {
            foreach (var item in environment)
            {
                startInfo.Environment[item.Key] = item.Value;
            }
        }

        using var process = new Process { StartInfo = startInfo };
        process.Start();
        var outputTask = process.StandardOutput.ReadToEndAsync();
        var errorTask = process.StandardError.ReadToEndAsync();
        await process.WaitForExitAsync().ConfigureAwait(false);
        var output = (await outputTask.ConfigureAwait(false)) + (await errorTask.ConfigureAwait(false));
        return new ProcessResult(process.ExitCode, output.Trim());
    }

    private static void ShowError(string message)
    {
        MessageBox.Show(message, "墨枢启动失败", MessageBoxButtons.OK, MessageBoxIcon.Error);
    }

    private readonly record struct ProcessResult(int ExitCode, string Output);

    private sealed class LauncherOptions
    {
        public bool Stop { get; private set; }
        public bool OpenBrowser { get; private set; } = true;
        public string? DataDirectory { get; private set; }
        public TimeSpan Timeout { get; private set; } = TimeSpan.FromMinutes(5);
        public string BuildRevision { get; private set; } = "launcher";

        public static LauncherOptions Parse(string[] args)
        {
            var options = new LauncherOptions();
            for (var index = 0; index < args.Length; index++)
            {
                switch (args[index].ToLowerInvariant())
                {
                    case "--stop":
                        options.Stop = true;
                        break;
                    case "--no-browser":
                        options.OpenBrowser = false;
                        break;
                    case "--data-dir" when index + 1 < args.Length:
                        options.DataDirectory = Path.GetFullPath(args[++index]);
                        break;
                    case "--timeout" when index + 1 < args.Length && int.TryParse(args[index + 1], out var seconds):
                        options.Timeout = TimeSpan.FromSeconds(Math.Clamp(seconds, 30, 1800));
                        index++;
                        break;
                    case "--revision" when index + 1 < args.Length:
                        options.BuildRevision = args[++index];
                        break;
                    case "--help":
                    case "-h":
                        MessageBox.Show(
                            "Moshu.exe\n\n" +
                            "双击：启动 Docker Compose 并打开写作台\n" +
                            "--stop：停止服务\n" +
                            "--data-dir <目录>：指定数据目录\n" +
                            "--no-browser：启动后不打开浏览器\n" +
                            "--timeout <秒>：健康检查超时（30-1800）",
                            "墨枢",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Information);
                        Environment.Exit(0);
                        break;
                    default:
                        throw new ArgumentException($"未知参数：{args[index]}");
                }
            }
            return options;
        }
    }
}
