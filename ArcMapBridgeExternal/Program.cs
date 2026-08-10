using ESRI.ArcGIS;
using ESRI.ArcGIS.Framework;
using ESRI.ArcGIS.esriSystem;
using Newtonsoft.Json.Linq;
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
        private const int BridgePort = 8766;
        private const int ArcMapIdleExitSeconds = 30;
        private const string BridgeCommandId = "openAssistantButton";
        private const string GatewayUrl = "http://127.0.0.1:8765";
        private const string SilentCommandFileName = "bridge_command.json";
        private const string DeploymentIdentityFileName = "deployment_identity.json";
        private const string ReadyFileName = "bridge.ready";
        private static readonly Encoding Utf8NoBom = new UTF8Encoding(false, true);

        [STAThread]
        private static int Main(string[] args)
        {
            try
            {
                BindArcGisRuntime();
                using (var bridge = new BridgeServer(ReadDeploymentIdentity()))
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
            private readonly string _deploymentHash;
            private DateTime _lastArcMapSeenAt = DateTime.MinValue;
            private TcpListener _listener;
            private Thread _listenerThread;
            private bool _running;
            private int _activeExecutionCount;

            public int Port { get; private set; }

            public BridgeServer(string sourceSha256)
            {
                _deploymentHash = sourceSha256;
            }

            public void Start()
            {
                _listener = BindListener();
                _running = true;
                _listenerThread = new Thread(ListenLoop);
                _listenerThread.IsBackground = true;
                _listenerThread.Start();
                WriteReadyFile();
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
                DeleteReadyFile();
            }

            private TcpListener BindListener()
            {
                var listener = new TcpListener(IPAddress.Parse(Host), BridgePort);
                listener.Start();
                Port = BridgePort;
                return listener;
            }

            private static string ReadyFilePath()
            {
                string baseDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "ArcMapAIAssistant");
                return Path.Combine(baseDir, ReadyFileName);
            }

            private void WriteReadyFile()
            {
                string path = ReadyFilePath();
                Directory.CreateDirectory(Path.GetDirectoryName(path));
                var json = new JObject(
                    new JProperty("pid", CurrentProcessId()),
                    new JProperty("port", Port),
                    new JProperty("started_at", DateTime.UtcNow.ToString("o")));
                string tmp = path + ".tmp";
                File.WriteAllText(tmp, json.ToString(), Utf8NoBom);
                File.Delete(path);
                File.Move(tmp, path);
            }

            private static void DeleteReadyFile()
            {
                try
                {
                    string path = ReadyFilePath();
                    if (!File.Exists(path))
                    {
                        return;
                    }
                    JObject ready = JObject.Parse(File.ReadAllText(path, Utf8NoBom));
                    JToken pid = ready["pid"];
                    if (pid != null && pid.Value<int>() == CurrentProcessId())
                    {
                        File.Delete(path);
                    }
                }
                catch (Exception ex) { Log("bridge.ready_delete_failed", ex.ToString()); }
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
                    else if (request.Method == "GET" && request.Path == "/targets")
                    {
                        WriteJson(client, TargetsJson());
                    }
                    else if (request.Method == "POST" && request.Path == "/dispatch")
                    {
                        WriteJson(client, EnqueueAndWait("execute", request.Body));
                    }
                    else if (request.Method == "POST" && request.Path == "/capture-context")
                    {
                        WriteJson(client, EnqueueAndWait("sync", request.Body));
                    }
                    else if (request.Method == "POST" && request.Path == "/acceptance-probe")
                    {
                        WriteJson(client, EnqueueAndWait("acceptance_probe", request.Body));
                    }
                    else if (request.Method == "POST" && request.Path == "/reconcile")
                    {
                        WriteJson(client, ReconcileExecution(request.Body));
                    }
                    else if (request.Method == "POST" && request.Path == "/sample-values")
                    {
                        WriteJson(client, SampleValues(request.Body));
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

            private string EnqueueAndWait(string action, string body)
            {
                var request = new BridgeRequest(action, body);
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
                string leaseId = ExtractString(request.Body, "lease_id");
                int epoch = ExtractInt(request.Body, "epoch");
                string planHash = ExtractString(request.Body, "plan_hash");
                string runId = ExtractString(request.Body, "run_id");
                if (request.Action == "sync")
                {
                    string phase = ExtractString(request.Body, "phase");
                    if (phase != "before_planning" && phase != "after_execution")
                    {
                        return ErrorJson("phase is required.");
                    }
                    // Both phases require a real lease triple: the Gateway must
                    // acquire a context lease before capturing context, so
                    // before_planning is fenced the same way as after_execution.
                    // For before_planning the plan_hash is the empty string
                    // (the plan is not sealed yet), which is still a concrete
                    // value the caller committed to — never a synthesized one.
                    if (!IsCanonicalGuid(leaseId) || epoch <= 0 || planHash == null)
                    {
                        return ErrorJson("lease_id, epoch and plan_hash are required for sync (before_planning uses empty plan_hash).");
                    }
                    ExecuteArcMapCommand(hwnd, "sync", false, leaseId, epoch, planHash ?? "", runId, null, phase);
                    return "{\"ok\":true}";
                }
                if (!IsCanonicalGuid(leaseId) || epoch <= 0 || string.IsNullOrWhiteSpace(planHash))
                {
                    return ErrorJson("lease_id, epoch and plan_hash are required (lease fencing).");
                }
                if (request.Action == "execute")
                {
                    if (!IsCanonicalGuid(runId))
                    {
                        return ErrorJson("canonical run_id is required.");
                    }
                    string contextJson = ExtractObjectJson(request.Body, "context_snapshot");
                    ExecuteArcMapCommand(hwnd, "execute", allowEdits, leaseId, epoch, planHash, runId, contextJson, null);
                    return "{\"ok\":true,\"run_id\":\"" + JsonEscape(runId) + "\"}";
                }
                if (request.Action == "acceptance_probe")
                {
                    string outputId = ExtractString(request.Body, "output_id");
                    string kind = ExtractString(request.Body, "kind");
                    string stagedPath = ExtractString(request.Body, "staged_path");
                    string sourceUnitPath = ExtractString(request.Body, "source_publish_unit_path");
                    string deploymentHash = ExtractString(request.Body, "deployment_hash");
                    bool unitProbe = !string.IsNullOrWhiteSpace(sourceUnitPath);
                    if (!IsCanonicalGuid(runId) || (!unitProbe && (string.IsNullOrWhiteSpace(outputId) ||
                        string.IsNullOrWhiteSpace(kind) || string.IsNullOrWhiteSpace(stagedPath))) ||
                        string.IsNullOrWhiteSpace(deploymentHash))
                    {
                        return ErrorJson("run_id, deployment_hash and either source_publish_unit_path or output_id/kind/staged_path are required.");
                    }
                    string probeJson = unitProbe
                        ? "{\"source_publish_unit_path\":\"" + JsonEscape(sourceUnitPath) + "\",\"deployment_hash\":\"" + JsonEscape(deploymentHash) + "\"}"
                        : "{\"output_id\":\"" + JsonEscape(outputId) + "\",\"kind\":\"" + JsonEscape(kind) +
                          "\",\"staged_path\":\"" + JsonEscape(stagedPath) + "\",\"deployment_hash\":\"" + JsonEscape(deploymentHash) + "\"}";
                    ExecuteArcMapCommand(hwnd, "acceptance_probe", false, leaseId, epoch, planHash, runId, probeJson, null);
                    return "{\"ok\":true,\"run_id\":\"" + JsonEscape(runId) + "\"}";
                }
                if (request.Action == "reconcile" || request.Action == "sample")
                {
                    if (!IsCanonicalGuid(runId))
                    {
                        return ErrorJson("canonical run_id is required.");
                    }
                    string payload = null;
                    if (request.Action == "sample")
                    {
                        string layerRef = ExtractString(request.Body, "layer_ref");
                        int maxRows = ExtractInt(request.Body, "max_rows");
                        int maxSamples = ExtractInt(request.Body, "max_samples");
                        if (string.IsNullOrWhiteSpace(layerRef) || maxRows <= 0 || maxSamples <= 0)
                        {
                            return ErrorJson("sample layer_ref, max_rows and max_samples are required.");
                        }
                        payload = request.Body;
                    }
                    ExecuteArcMapCommand(hwnd, request.Action, false, leaseId, epoch, planHash, runId, payload, null);
                    return "{\"ok\":true,\"run_id\":\"" + JsonEscape(runId) + "\"}";
                }
                return ErrorJson("Unknown request.");
            }

            private string ReconcileExecution(string body)
            {
                string leaseId = ExtractString(body, "lease_id");
                int epoch = ExtractInt(body, "epoch");
                string planHash = ExtractString(body, "plan_hash");
                string runId = ExtractString(body, "run_id");
                int hwnd = ExtractInt(body, "hwnd");
                if (!IsCanonicalGuid(leaseId) || epoch <= 0 || string.IsNullOrWhiteSpace(planHash) ||
                    !IsCanonicalGuid(runId) || hwnd <= 0)
                {
                    return ErrorJson("lease_id, epoch, plan_hash, run_id and hwnd are required.");
                }
                return EnqueueAndWait("reconcile", body);
            }

            private string SampleValues(string body)
            {
                string leaseId = ExtractString(body, "lease_id");
                int epoch = ExtractInt(body, "epoch");
                string planHash = ExtractString(body, "plan_hash");
                string runId = ExtractString(body, "run_id");
                int hwnd = ExtractInt(body, "hwnd");
                string layerRef = ExtractString(body, "layer_ref");
                int maxRows = ExtractInt(body, "max_rows");
                int maxSamples = ExtractInt(body, "max_samples");
                if (!IsCanonicalGuid(leaseId) || epoch <= 0 || string.IsNullOrWhiteSpace(planHash) ||
                    !IsCanonicalGuid(runId) || hwnd <= 0 || string.IsNullOrWhiteSpace(layerRef) ||
                    maxRows <= 0 || maxSamples <= 0)
                {
                    return ErrorJson("fully fenced sample request with positive limits is required.");
                }
                return EnqueueAndWait("sample", body);
            }

            private void ExecuteArcMapCommand(int hwnd, string silentAction, bool allowEdits,
                string leaseId, int epoch, string planHash, string runId, string contextJson, string phase)
            {
                IApplication app = ResolveArcMap(hwnd);
                IDocument document = app.Document;
                ICommandBars commandBars = document.CommandBars;
                ICommandItem item = commandBars.Find(BridgeCommandId, false, false);
                if (item == null)
                {
                    throw new InvalidOperationException("ArcMap command not found: " + BridgeCommandId);
                }
                int arcMapPid = ArcMapProcessId(hwnd);
                WriteSilentCommand(silentAction, allowEdits, leaseId, epoch, planHash, runId, contextJson, phase, hwnd, Port, arcMapPid);
                if (silentAction == "execute")
                {
                    StartExecutionHeartbeat(leaseId, epoch, planHash, runId, arcMapPid);
                }
                item.Execute();
            }

            private void StartExecutionHeartbeat(string leaseId, int epoch, string planHash,
                string runId, int arcMapPid)
            {
                Interlocked.Increment(ref _activeExecutionCount);
                var heartbeat = new GatewayExecutionHeartbeat(
                    leaseId,
                    epoch,
                    planHash,
                    runId,
                    arcMapPid,
                    delegate { Interlocked.Decrement(ref _activeExecutionCount); }
                );
                try
                {
                    heartbeat.Start();
                }
                catch
                {
                    Interlocked.Decrement(ref _activeExecutionCount);
                    throw;
                }
            }

            private IApplication ResolveArcMap(int hwnd)
            {
                List<ArcMapTarget> targets = ListArcMapTargets();
                if (targets.Count == 0)
                {
                    throw new InvalidOperationException("没有找到已打开的 ArcMap。");
                }
                if (hwnd <= 0)
                {
                    throw new InvalidOperationException("ArcMap target hwnd is required.");
                }
                foreach (ArcMapTarget target in targets)
                {
                    if (target.Hwnd == hwnd)
                    {
                        return target.Application;
                    }
                }
                throw new InvalidOperationException("没有找到指定 ArcMap 窗口：" + hwnd);
            }

            private string HealthJson()
            {
                return "{\"ok\":true,\"bridge\":\"arcmap-external\",\"bridge_pid\":" + CurrentProcessId() +
                    ",\"bridge_port\":" + Port +
                    ",\"deployment_hash\":\"" + _deploymentHash + "\"}";
            }

            private string TargetsJson()
            {
                List<ArcMapTarget> targets = ListArcMapTargets();
                int foregroundHwnd = GetForegroundWindow().ToInt32();
                var parts = new List<string>();
                foreach (ArcMapTarget target in targets)
                {
                    parts.Add("{\"arcmap_pid\":" + target.ArcMapPid + ",\"hwnd\":" + target.Hwnd +
                        ",\"active\":" + (target.Hwnd == foregroundHwnd ? "true" : "false") +
                        ",\"title\":\"" + JsonEscape(target.Title) +
                        "\",\"name\":\"" + JsonEscape(target.Name) + "\"}");
                }
                return "{\"ok\":true,\"targets\":[" + string.Join(",", parts.ToArray()) + "]}";
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
                // ArcMap's ROT entry may disappear while its STA is busy in a
                // synchronous GIS operation.  The execution heartbeat owns the
                // Bridge lifetime until the gateway reaches a terminal state or
                // the exact ArcMap process exits.
                if (Interlocked.CompareExchange(ref _activeExecutionCount, 0, 0) > 0)
                {
                    return;
                }
                RefreshArcMapPresence();
                DateTime reference = _lastArcMapSeenAt == DateTime.MinValue ? _startedAt : _lastArcMapSeenAt;
                if ((DateTime.Now - reference).TotalSeconds >= ArcMapIdleExitSeconds)
                {
                    Log("bridge.stopped", "reason=no_arcmap pid=" + CurrentProcessId() + " port=" + Port);
                    Dispose();
                }
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
            public readonly ManualResetEvent Done = new ManualResetEvent(false);
            public string ResponseJson = "{\"ok\":false,\"error\":\"Request did not complete.\"}";

            public BridgeRequest(string action, string body)
            {
                Action = action;
                Body = body ?? "";
            }
        }

        private sealed class GatewayExecutionHeartbeat
        {
            private readonly string _leaseId;
            private readonly int _epoch;
            private readonly string _planHash;
            private readonly string _runId;
            private readonly int _arcMapPid;
            private readonly Action _onFinished;
            private Thread _thread;

            public GatewayExecutionHeartbeat(string leaseId, int epoch, string planHash,
                string runId, int arcMapPid, Action onFinished)
            {
                _leaseId = leaseId;
                _epoch = epoch;
                _planHash = planHash;
                _runId = runId;
                _arcMapPid = arcMapPid;
                _onFinished = onFinished;
            }

            public void Start()
            {
                _thread = new Thread(Run);
                _thread.IsBackground = true;
                _thread.Name = "geopilot-execution-heartbeat-" + _runId;
                _thread.Start();
            }

            private void Run()
            {
                try
                {
                    while (IsArcMapProcessAlive())
                    {
                        HeartbeatPostResult result = TryPostGatewayHeartbeat();
                        if (result == HeartbeatPostResult.Terminal)
                        {
                            return;
                        }
                        Thread.Sleep(TimeSpan.FromSeconds(5));
                    }
                }
                finally
                {
                    _onFinished();
                }
            }

            private bool IsArcMapProcessAlive()
            {
                try
                {
                    using (Process process = Process.GetProcessById(_arcMapPid))
                    {
                        return !process.HasExited;
                    }
                }
                catch (ArgumentException)
                {
                    return false;
                }
            }

            private HeartbeatPostResult TryPostGatewayHeartbeat()
            {
                try
                {
                    string payload = "{\"lease_id\":\"" + JsonEscape(_leaseId) +
                        "\",\"epoch\":" + _epoch +
                        ",\"plan_hash\":\"" + JsonEscape(_planHash) + "\"}";
                    byte[] body = Encoding.UTF8.GetBytes(payload);
                    HttpWebRequest request = (HttpWebRequest)WebRequest.Create(
                        GatewayUrl + "/runs/" + _runId + "/heartbeat"
                    );
                    request.Method = "POST";
                    request.Timeout = 5000;
                    request.ReadWriteTimeout = 5000;
                    request.ContentType = "application/json; charset=utf-8";
                    request.ContentLength = body.Length;
                    using (Stream stream = request.GetRequestStream())
                    {
                        stream.Write(body, 0, body.Length);
                    }
                    using (request.GetResponse()) { }
                    return HeartbeatPostResult.Accepted;
                }
                catch (WebException ex)
                {
                    var response = ex.Response as HttpWebResponse;
                    if (response != null)
                    {
                        int statusCode = (int)response.StatusCode;
                        response.Close();
                        if (statusCode >= 400 && statusCode < 500)
                        {
                            return HeartbeatPostResult.Terminal;
                        }
                    }
                    Log("bridge.heartbeat_failed", ex.ToString());
                    return HeartbeatPostResult.Retry;
                }
                catch (Exception ex)
                {
                    Log("bridge.heartbeat_failed", ex.ToString());
                    return HeartbeatPostResult.Retry;
                }
            }

            private enum HeartbeatPostResult
            {
                Accepted,
                Terminal,
                Retry
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

        private static void WriteSilentCommand(string action, bool allowEdits, string leaseId,
            int epoch, string planHash, string runId, string contextJson, string phase,
            int hwnd, int bridgePort, int arcMapPid)
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
                    (string.IsNullOrWhiteSpace(leaseId) ? "" : ",\"lease_id\":\"" + JsonEscape(leaseId) + "\"") +
                    (epoch > 0 ? ",\"epoch\":" + epoch : "") +
                    (string.IsNullOrWhiteSpace(planHash) ? "" : ",\"plan_hash\":\"" + JsonEscape(planHash) + "\"") +
                    (string.IsNullOrWhiteSpace(runId) ? "" : ",\"run_id\":\"" + JsonEscape(runId) + "\"") +
                    (string.IsNullOrWhiteSpace(contextJson) ? "" : ",\"context\":" + contextJson) +
                    (string.IsNullOrWhiteSpace(phase) ? "" : ",\"phase\":\"" + JsonEscape(phase) + "\"") +
                    ",\"target\":{\"bridge_pid\":" + CurrentProcessId() +
                    ",\"bridge_port\":" + bridgePort.ToString(System.Globalization.CultureInfo.InvariantCulture) +
                    ",\"arcmap_pid\":" + arcMapPid.ToString(System.Globalization.CultureInfo.InvariantCulture) +
                    ",\"hwnd\":" + hwnd.ToString(System.Globalization.CultureInfo.InvariantCulture) + "}}";
                string commandPath = Path.Combine(dir, SilentCommandFileName);
                temporaryPath = Path.Combine(dir, SilentCommandFileName + "." + Guid.NewGuid().ToString("N") + ".tmp");
                File.WriteAllText(temporaryPath, json, Utf8NoBom);
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

        private static string ReadDeploymentIdentity()
        {
            string executablePath = Process.GetCurrentProcess().MainModule.FileName;
            string identityPath = Path.Combine(Path.GetDirectoryName(executablePath), DeploymentIdentityFileName);
            if (!File.Exists(identityPath))
            {
                throw new InvalidOperationException("ArcMap Bridge deployment identity is missing: " + identityPath);
            }
            JObject document;
            try
            {
                document = JObject.Parse(File.ReadAllText(identityPath, Encoding.UTF8));
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException("ArcMap Bridge deployment identity is invalid: " + ex.Message);
            }
            if (document.Count != 1 || document["deployment_hash"] == null ||
                document["deployment_hash"].Type != JTokenType.String)
            {
                throw new InvalidOperationException("ArcMap Bridge deployment identity has an invalid schema.");
            }
            string value = (string)document["deployment_hash"];
            if (!IsLowerHexSha256(value))
            {
                throw new InvalidOperationException("ArcMap Bridge deployment identity must be lowercase sha256.");
            }
            return value;
        }

        private static bool IsLowerHexSha256(string value)
        {
            if (value == null || value.Length != 64)
            {
                return false;
            }
            foreach (char item in value)
            {
                if (!((item >= '0' && item <= '9') || (item >= 'a' && item <= 'f')))
                {
                    return false;
                }
            }
            return true;
        }

        private static void PostGatewayJson(string path, string payload, string failureKind = "bridge.gateway_post_failed")
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
                Log(failureKind, ex.ToString());
            }
        }

        private static string ErrorJson(string message)
        {
            return "{\"ok\":false,\"error\":\"" + JsonEscape(message) + "\"}";
        }

        private static bool IsCanonicalGuid(string value)
        {
            Guid parsed;
            return !string.IsNullOrWhiteSpace(value) &&
                Guid.TryParseExact(value, "D", out parsed) &&
                string.Equals(parsed.ToString("D"), value, StringComparison.Ordinal);
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
            try
            {
                JObject obj = JObject.Parse(json);
                JToken token = obj[key];
                return token != null ? (int)token : 0;
            }
            catch { return 0; }
        }

        private static string ExtractString(string json, string key)
        {
            try
            {
                JObject obj = JObject.Parse(json);
                JToken token = obj[key];
                return token != null ? (string)token : "";
            }
            catch { return ""; }
        }

        private static string ExtractObjectJson(string json, string key)
        {
            JObject obj = JObject.Parse(json);
            JToken token = obj[key];
            if (token == null || token.Type != JTokenType.Object)
            {
                throw new InvalidOperationException(key + " must be a JSON object.");
            }
            return token.ToString(Newtonsoft.Json.Formatting.None);
        }

        private static bool ExtractBool(string json, string key)
        {
            try
            {
                JObject obj = JObject.Parse(json);
                JToken token = obj[key];
                if (token == null) return false;
                if (token.Type == JTokenType.Boolean) return (bool)token;
                if (token.Type == JTokenType.Integer) return (int)token != 0;
                return false;
            }
            catch { return false; }
        }

        private static string SafeString(Func<string> getter)
        {
            try { return getter() ?? ""; } catch (Exception ex) { Log("bridge.safe_string_failed", ex.ToString()); return ""; }
        }

        private static int CurrentProcessId()
        {
            return Process.GetCurrentProcess().Id;
        }

        [DllImport("user32.dll", SetLastError = true)]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

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
