using ESRI.ArcGIS;
using ESRI.ArcGIS.Framework;
using ESRI.ArcGIS.esriSystem;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using System.Runtime.InteropServices;

namespace GeoPilot.ArcMapBridgeExternal
{
    internal static class Program
    {
        private const string Host = "127.0.0.1";
        private const int FirstPort = 8766;
        private const int LastPort = 8789;
        private const int ArcMapIdleExitSeconds = 30;
        private const string BridgeCommandId = "openAssistantButton";
        private const string GatewayUrl = "http://127.0.0.1:8765";
        private const string SilentCommandFileName = "bridge_command.json";

        [STAThread]
        private static int Main(string[] args)
        {
            try
            {
                BindArcGisRuntime();
                using (var bridge = new BridgeServer())
                {
                    bridge.Start();
                    Console.WriteLine("ArcMapBridge listening on http://{0}:{1}", Host, bridge.Port);
                    bridge.Run();
                }
                return 0;
            }
            catch (Exception ex)
            {
                Log("bridge.fatal", ex.ToString());
                Console.Error.WriteLine(ex);
                return 1;
            }
        }

        private static void BindArcGisRuntime()
        {
            if (!RuntimeManager.Bind(ProductCode.Desktop))
            {
                throw new InvalidOperationException("ArcGIS Desktop runtime bind failed.");
            }
        }

        private sealed class BridgeServer : IDisposable
        {
            private readonly AutoResetEvent _workAvailable = new AutoResetEvent(false);
            private readonly object _queueGate = new object();
            private readonly Queue<BridgeRequest> _queue = new Queue<BridgeRequest>();
            private readonly DateTime _startedAt = DateTime.Now;
            private DateTime _lastArcMapSeenAt = DateTime.MinValue;
            private TcpListener _listener;
            private Thread _listenerThread;
            private bool _running;

            public int Port { get; private set; }

            public void Start()
            {
                _listener = BindListener();
                RefreshArcMapPresence();
                _running = true;
                _listenerThread = new Thread(ListenLoop);
                _listenerThread.IsBackground = true;
                _listenerThread.Start();
                RegisterWithGateway();
                Log("bridge.started", "pid=" + CurrentProcessId() + " port=" + Port);
            }

            public void Run()
            {
                while (_running)
                {
                    _workAvailable.WaitOne(TimeSpan.FromSeconds(5));
                    DrainQueue();
                    StopIfArcMapClosed();
                }
            }

            public void Dispose()
            {
                _running = false;
                _workAvailable.Set();
                if (_listener != null)
                {
                    try { _listener.Stop(); } catch { }
                    _listener = null;
                }
            }

            private TcpListener BindListener()
            {
                for (int port = FirstPort; port <= LastPort; port++)
                {
                    var candidate = new TcpListener(IPAddress.Parse(Host), port);
                    try
                    {
                        candidate.Start();
                        Port = port;
                        return candidate;
                    }
                    catch
                    {
                        try { candidate.Stop(); } catch { }
                    }
                }
                throw new InvalidOperationException("No free ArcMapBridge port.");
            }

            private void ListenLoop()
            {
                while (_running)
                {
                    try
                    {
                        TcpClient client = _listener.AcceptTcpClient();
                        ThreadPool.QueueUserWorkItem(HandleClient, client);
                    }
                    catch (SocketException)
                    {
                        return;
                    }
                    catch (ObjectDisposedException)
                    {
                        return;
                    }
                    catch (Exception ex)
                    {
                        Log("bridge.listen_failed", ex.ToString());
                    }
                }
            }

            private void HandleClient(object state)
            {
                var client = (TcpClient)state;
                try
                {
                    HttpRequest request = ReadHttpRequest(client);
                    if (request == null)
                    {
                        return;
                    }
                    if (request.Method == "GET" && request.Path == "/health")
                    {
                        WriteJson(client, HealthJson());
                    }
                    else if (request.Method == "POST" && request.Path == "/sync-context")
                    {
                        WriteJson(client, EnqueueAndWait("sync", request.Body));
                    }
                    else if (request.Method == "POST" && IsRunExecutePath(request.Path))
                    {
                        WriteJson(client, EnqueueAndWait("execute", request.Body, RunIdFromExecutePath(request.Path)));
                    }
                    else
                    {
                        WriteJson(client, ErrorJson("Not found."), 404);
                    }
                }
                catch (Exception ex)
                {
                    Log("bridge.request_failed", ex.ToString());
                    SafeWriteJson(client, ErrorJson(ex.Message), 500);
                }
                finally
                {
                    try { client.Close(); } catch { }
                }
            }

            private string EnqueueAndWait(string action, string body, string runId = null)
            {
                var request = new BridgeRequest(action, body, runId);
                lock (_queueGate)
                {
                    _queue.Enqueue(request);
                }
                _workAvailable.Set();
                if (!request.Done.WaitOne(TimeSpan.FromSeconds(30)))
                {
                    return ErrorJson("Bridge request wait expired; ArcMap execution state must be recovered through the run lease.");
                }
                return request.ResponseJson;
            }

            private void DrainQueue()
            {
                while (true)
                {
                    BridgeRequest request = null;
                    lock (_queueGate)
                    {
                        if (_queue.Count == 0)
                        {
                            return;
                        }
                        request = _queue.Dequeue();
                    }

                    try
                    {
                        request.ResponseJson = ExecuteRequest(request);
                    }
                    catch (Exception ex)
                    {
                        request.ResponseJson = ErrorJson(ex.Message);
                        Log("bridge.arcmap_failed", ex.ToString());
                    }
                    finally
                    {
                        request.Done.Set();
                    }
                }
            }

            private string ExecuteRequest(BridgeRequest request)
            {
                int hwnd = ExtractInt(request.Body, "hwnd");
                bool allowEdits = ExtractBool(request.Body, "allow_edits");
                if (request.Action == "sync")
                {
                    string runId = ExtractString(request.Body, "run_id");
                    string syncToken = ExtractString(request.Body, "sync_token");
                    string phase = ExtractString(request.Body, "phase");
                    if (string.IsNullOrWhiteSpace(runId))
                    {
                        return ErrorJson("run_id is required.");
                    }
                    if (string.IsNullOrWhiteSpace(syncToken) ||
                        (phase != "before_planning" && phase != "after_execution"))
                    {
                        return ErrorJson("sync_token and phase are required.");
                    }
                    ExecuteArcMapCommand(hwnd, "sync", false, runId, syncToken, phase);
                    return "{\"ok\":true}";
                }
                if (request.Action == "execute")
                {
                    string runId = request.RunId;
                    Guid parsedRunId;
                    if (string.IsNullOrWhiteSpace(runId) ||
                        !Guid.TryParseExact(runId, "D", out parsedRunId) ||
                        !string.Equals(parsedRunId.ToString("D"), runId, StringComparison.Ordinal))
                    {
                        return ErrorJson("canonical run_id is required.");
                    }
                    ExecuteArcMapCommand(hwnd, "execute", allowEdits, runId, null, null);
                    return "{\"ok\":true,\"run_id\":\"" + JsonEscape(runId) + "\"}";
                }
                return ErrorJson("Unknown request.");
            }

            private void ExecuteArcMapCommand(int hwnd, string silentAction, bool allowEdits, string runId, string syncToken, string phase)
            {
                IApplication app = ResolveArcMap(hwnd);
                IDocument document = app.Document;
                ICommandBars commandBars = document.CommandBars;
                ICommandItem item = commandBars.Find(BridgeCommandId, false, false);
                if (item == null)
                {
                    throw new InvalidOperationException("ArcMap command not found: " + BridgeCommandId);
                }
                WriteSilentCommand(silentAction, allowEdits, runId, syncToken, phase, hwnd, Port, ArcMapProcessId(hwnd));
                item.Execute();
            }

            private IApplication ResolveArcMap(int hwnd)
            {
                List<ArcMapTarget> targets = ListArcMapTargets();
                if (targets.Count == 0)
                {
                    throw new InvalidOperationException("没有找到已打开的 ArcMap。");
                }
                if (hwnd > 0)
                {
                    foreach (ArcMapTarget target in targets)
                    {
                        if (target.Hwnd == hwnd)
                        {
                            return target.Application;
                        }
                    }
                    throw new InvalidOperationException("没有找到指定 ArcMap 窗口：" + hwnd);
                }
                if (targets.Count > 1)
                {
                    throw new InvalidOperationException("检测到多个 ArcMap，请先选择目标窗口。");
                }
                return targets[0].Application;
            }

            private string HealthJson()
            {
                List<ArcMapTarget> targets = ListArcMapTargets();
                var parts = new List<string>();
                foreach (ArcMapTarget target in targets)
                {
                    parts.Add("{\"arcmap_pid\":" + target.ArcMapPid + ",\"hwnd\":" + target.Hwnd +
                        ",\"title\":\"" + JsonEscape(target.Title) +
                        "\",\"name\":\"" + JsonEscape(target.Name) + "\"}");
                }
                return "{\"ok\":true,\"bridge\":\"arcmap-external\",\"bridge_pid\":" + CurrentProcessId() +
                    ",\"bridge_port\":" + Port +
                    ",\"summary\":{\"bridge\":\"external\",\"arcmap_count\":" + targets.Count +
                    ",\"targets\":[" + string.Join(",", parts.ToArray()) + "]}}";
            }

            private List<ArcMapTarget> ListArcMapTargets()
            {
                var targets = new List<ArcMapTarget>();
                IAppROT rot = new AppROTClass();
                for (int i = 0; i < rot.Count; i++)
                {
                    AppRef appRef = rot.get_Item(i);
                    if (appRef == null)
                    {
                        continue;
                    }
                    IApplication app = (IApplication)appRef;
                    string name = SafeString(delegate { return app.Name; });
                    if (!string.Equals(name, "ArcMap", StringComparison.OrdinalIgnoreCase))
                    {
                        continue;
                    }
                    targets.Add(new ArcMapTarget {
                        Hwnd = appRef.hWnd,
                        ArcMapPid = ArcMapProcessId(appRef.hWnd),
                        Name = name,
                        Title = SafeString(delegate { return app.Caption; }),
                        Application = app
                    });
                }
                return targets;
            }

            private void RefreshArcMapPresence()
            {
                if (ListArcMapTargets().Count > 0)
                {
                    _lastArcMapSeenAt = DateTime.Now;
                }
            }

            private void StopIfArcMapClosed()
            {
                RefreshArcMapPresence();
                DateTime reference = _lastArcMapSeenAt == DateTime.MinValue ? _startedAt : _lastArcMapSeenAt;
                if ((DateTime.Now - reference).TotalSeconds >= ArcMapIdleExitSeconds)
                {
                    Log("bridge.stopped", "reason=no_arcmap pid=" + CurrentProcessId() + " port=" + Port);
                    Dispose();
                }
            }

            private void RegisterWithGateway()
            {
                string payload = "{\"bridge_pid\":" + CurrentProcessId() +
                    ",\"bridge_port\":" + Port +
                    ",\"summary\":{\"bridge\":\"external\"}}";
                PostGatewayJson("/arcmap/register", payload);
            }
        }

        private sealed class ArcMapTarget
        {
            public int Hwnd;
            public int ArcMapPid;
            public string Name;
            public string Title;
            public IApplication Application;
        }

        private sealed class BridgeRequest
        {
            public readonly string Action;
            public readonly string Body;
            public readonly string RunId;
            public readonly ManualResetEvent Done = new ManualResetEvent(false);
            public string ResponseJson = "{\"ok\":false,\"error\":\"Request did not complete.\"}";

            public BridgeRequest(string action, string body, string runId)
            {
                Action = action;
                Body = body ?? "";
                RunId = runId ?? "";
            }
        }

        private sealed class HttpRequest
        {
            public readonly string Method;
            public readonly string Path;
            public readonly string Body;

            public HttpRequest(string method, string path, string body)
            {
                Method = method;
                Path = path;
                Body = body;
            }
        }

        private static HttpRequest ReadHttpRequest(TcpClient client)
        {
            client.ReceiveTimeout = 30000;
            NetworkStream stream = client.GetStream();
            var bytes = new List<byte>();
            byte[] chunk = new byte[4096];
            while (true)
            {
                int count = stream.Read(chunk, 0, chunk.Length);
                if (count <= 0)
                {
                    break;
                }
                for (int i = 0; i < count; i++)
                {
                    bytes.Add(chunk[i]);
                }
                if (IndexOf(bytes, Encoding.ASCII.GetBytes("\r\n\r\n")) >= 0)
                {
                    break;
                }
                if (bytes.Count > 65536)
                {
                    throw new InvalidOperationException("HTTP header is too large.");
                }
            }

            int headerEnd = IndexOf(bytes, Encoding.ASCII.GetBytes("\r\n\r\n"));
            if (headerEnd < 0)
            {
                if (bytes.Count == 0)
                {
                    return null;
                }
                throw new InvalidOperationException("Invalid HTTP request.");
            }
            string header = Encoding.ASCII.GetString(bytes.ToArray(), 0, headerEnd);
            string[] lines = header.Split(new[] { "\r\n" }, StringSplitOptions.None);
            string[] first = lines[0].Split(' ');
            if (first.Length < 2)
            {
                throw new InvalidOperationException("Invalid HTTP request line.");
            }
            int contentLength = 0;
            foreach (string line in lines)
            {
                if (line.StartsWith("Content-Length:", StringComparison.OrdinalIgnoreCase))
                {
                    int.TryParse(line.Substring("Content-Length:".Length).Trim(), out contentLength);
                }
            }
            int bodyStart = headerEnd + 4;
            while (bytes.Count - bodyStart < contentLength)
            {
                int count = stream.Read(chunk, 0, chunk.Length);
                if (count <= 0)
                {
                    break;
                }
                for (int i = 0; i < count; i++)
                {
                    bytes.Add(chunk[i]);
                }
            }
            string body = contentLength > 0 && bytes.Count >= bodyStart
                ? Encoding.UTF8.GetString(bytes.ToArray(), bodyStart, Math.Min(contentLength, bytes.Count - bodyStart))
                : "";
            Uri uri = new Uri("http://" + Host + first[1]);
            return new HttpRequest(first[0].ToUpperInvariant(), uri.AbsolutePath, body);
        }

        private static int IndexOf(List<byte> source, byte[] pattern)
        {
            for (int i = 0; i <= source.Count - pattern.Length; i++)
            {
                bool match = true;
                for (int j = 0; j < pattern.Length; j++)
                {
                    if (source[i + j] != pattern[j])
                    {
                        match = false;
                        break;
                    }
                }
                if (match)
                {
                    return i;
                }
            }
            return -1;
        }

        private static void WriteJson(TcpClient client, string json)
        {
            WriteJson(client, json, 200);
        }

        private static void SafeWriteJson(TcpClient client, string json, int status)
        {
            try { WriteJson(client, json, status); } catch { }
        }

        private static void WriteJson(TcpClient client, string json, int status)
        {
            byte[] data = Encoding.UTF8.GetBytes(json);
            string reason = status == 200 ? "OK" : "Error";
            string header = "HTTP/1.0 " + status + " " + reason + "\r\n" +
                "Content-Type: application/json; charset=utf-8\r\n" +
                "Content-Length: " + data.Length + "\r\n" +
                "Connection: close\r\n\r\n";
            NetworkStream stream = client.GetStream();
            byte[] headerBytes = Encoding.ASCII.GetBytes(header);
            stream.Write(headerBytes, 0, headerBytes.Length);
            stream.Write(data, 0, data.Length);
        }

        private static void WriteSilentCommand(string action, bool allowEdits, string runId, string syncToken, string phase, int hwnd, int bridgePort, int arcMapPid)
        {
            string temporaryPath = null;
            try
            {
                string root = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string dir = Path.Combine(root, "ArcMapAIAssistant");
                if (!Directory.Exists(dir))
                {
                    Directory.CreateDirectory(dir);
                }
                double expiresAt = (DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds + 60;
                string json = "{\"action\":\"" + JsonEscape(action) + "\",\"expires_at\":" +
                    expiresAt.ToString(System.Globalization.CultureInfo.InvariantCulture) +
                    ",\"allow_edits\":" + (allowEdits ? "true" : "false") +
                    (string.IsNullOrWhiteSpace(runId) ? "" : ",\"run_id\":\"" + JsonEscape(runId) + "\"") +
                    (string.IsNullOrWhiteSpace(syncToken) ? "" : ",\"sync_token\":\"" + JsonEscape(syncToken) + "\"") +
                    (string.IsNullOrWhiteSpace(phase) ? "" : ",\"phase\":\"" + JsonEscape(phase) + "\"") +
                    ",\"target\":{\"bridge_pid\":" + CurrentProcessId() +
                    ",\"bridge_port\":" + bridgePort.ToString(System.Globalization.CultureInfo.InvariantCulture) +
                    ",\"arcmap_pid\":" + arcMapPid.ToString(System.Globalization.CultureInfo.InvariantCulture) +
                    ",\"hwnd\":" + hwnd.ToString(System.Globalization.CultureInfo.InvariantCulture) + "}}";
                string commandPath = Path.Combine(dir, SilentCommandFileName);
                temporaryPath = Path.Combine(dir, SilentCommandFileName + "." + Guid.NewGuid().ToString("N") + ".tmp");
                File.WriteAllText(temporaryPath, json, Encoding.UTF8);
                if (File.Exists(commandPath))
                {
                    File.Replace(temporaryPath, commandPath, null);
                }
                else
                {
                    File.Move(temporaryPath, commandPath);
                }
            }
            catch
            {
                if (!string.IsNullOrWhiteSpace(temporaryPath) && File.Exists(temporaryPath))
                {
                    try { File.Delete(temporaryPath); } catch { }
                }
                throw;
            }
        }

        private static void PostGatewayJson(string path, string payload)
        {
            try
            {
                byte[] body = Encoding.UTF8.GetBytes(payload);
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(GatewayUrl + path);
                request.Method = "POST";
                request.Timeout = 5000;
                request.ContentType = "application/json; charset=utf-8";
                request.ContentLength = body.Length;
                using (Stream stream = request.GetRequestStream())
                {
                    stream.Write(body, 0, body.Length);
                }
                using (request.GetResponse()) { }
            }
            catch (Exception ex)
            {
                Log("bridge.register_failed", ex.ToString());
            }
        }

        private static string ErrorJson(string message)
        {
            return "{\"ok\":false,\"error\":\"" + JsonEscape(message) + "\"}";
        }

        private static bool IsRunExecutePath(string path)
        {
            return !string.IsNullOrWhiteSpace(path) &&
                path.StartsWith("/runs/", StringComparison.Ordinal) &&
                path.EndsWith("/execute", StringComparison.Ordinal) &&
                path.Length > "/runs//execute".Length;
        }

        private static string RunIdFromExecutePath(string path)
        {
            return path.Substring("/runs/".Length, path.Length - "/runs/".Length - "/execute".Length);
        }

        private static string JsonEscape(string value)
        {
            if (value == null)
            {
                return "";
            }
            return value.Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\r", "\\r")
                .Replace("\n", "\\n");
        }

        private static int ExtractInt(string json, string key)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return 0;
            }
            string marker = "\"" + key + "\"";
            int pos = json.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
            if (pos < 0)
            {
                return 0;
            }
            pos = json.IndexOf(':', pos);
            if (pos < 0)
            {
                return 0;
            }
            pos++;
            while (pos < json.Length && char.IsWhiteSpace(json[pos]))
            {
                pos++;
            }
            int start = pos;
            while (pos < json.Length && (char.IsDigit(json[pos]) || json[pos] == '-'))
            {
                pos++;
            }
            int value;
            return int.TryParse(json.Substring(start, pos - start), out value) ? value : 0;
        }

        private static string ExtractString(string json, string key)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return "";
            }
            string marker = "\"" + key + "\"";
            int pos = json.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
            if (pos < 0)
            {
                return "";
            }
            pos = json.IndexOf(':', pos);
            if (pos < 0)
            {
                return "";
            }
            pos++;
            while (pos < json.Length && char.IsWhiteSpace(json[pos]))
            {
                pos++;
            }
            if (pos >= json.Length || json[pos] != '\"')
            {
                return "";
            }
            int end = json.IndexOf('\"', pos + 1);
            return end > pos ? json.Substring(pos + 1, end - pos - 1) : "";
        }

        private static bool ExtractBool(string json, string key)
        {
            if (string.IsNullOrWhiteSpace(json))
            {
                return false;
            }
            string marker = "\"" + key + "\"";
            int pos = json.IndexOf(marker, StringComparison.OrdinalIgnoreCase);
            if (pos < 0)
            {
                return false;
            }
            pos = json.IndexOf(':', pos);
            if (pos < 0)
            {
                return false;
            }
            pos++;
            while (pos < json.Length && char.IsWhiteSpace(json[pos]))
            {
                pos++;
            }
            if (pos >= json.Length)
            {
                return false;
            }
            if (string.Compare(json, pos, "true", 0, 4, true, System.Globalization.CultureInfo.InvariantCulture) == 0)
            {
                return true;
            }
            if (json[pos] == '1')
            {
                return true;
            }
            return false;
        }

        private static string SafeString(Func<string> getter)
        {
            try { return getter() ?? ""; } catch { return ""; }
        }

        private static int CurrentProcessId()
        {
            return Process.GetCurrentProcess().Id;
        }

        [DllImport("user32.dll", SetLastError = true)]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

        private static int ArcMapProcessId(int hwnd)
        {
            uint processId;
            GetWindowThreadProcessId(new IntPtr(hwnd), out processId);
            if (processId == 0)
            {
                throw new InvalidOperationException("ArcMap window process identity is unavailable.");
            }
            return unchecked((int)processId);
        }

        private static void Log(string kind, string detail)
        {
            try
            {
                string root = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string dir = Path.Combine(root, "ArcMapAIAssistant", "logs");
                if (!Directory.Exists(dir))
                {
                    Directory.CreateDirectory(dir);
                }
                string path = Path.Combine(dir, "arcmap_bridge.log");
                File.AppendAllText(path,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\t" + kind + "\t" + detail + Environment.NewLine,
                    Encoding.UTF8);
            }
            catch
            {
            }
        }
    }
}
