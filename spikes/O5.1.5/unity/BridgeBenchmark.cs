using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using UnityEngine;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Spikes.Bridge
{
    public sealed class BridgeBenchmark : MonoBehaviour
    {
        [Serializable] public sealed class PoseFrame { public string id; public float[] position_m, rotation_xyzw; }
        [Serializable] public sealed class StateFrame
        {
            public int version; public string kind, source_kind, session_id, host_monotonic_ns;
            public long seq, sim_step; public double sim_time;
            public string[] joint_names; public double[] joint_positions; public PoseFrame[] objects;
        }
        [Serializable] public sealed class Echo { public string kind = "echo", c0_s, s1_ns, s2_ns; }
        [Serializable] sealed class Message { public string data; }
        [Serializable] sealed class RosEnvelope
        { public string op, topic, type; public int queue_length = 1; public Message msg; }
        [Serializable] public sealed class Settings
        {
            public string uri = "ws://127.0.0.1:8765";
            public string candidate = "custom";
            public string topology = "UNSET"; // standalone-wifi or link-wired
            public string run_kind = "steady-state"; // server-restart or wifi-drop for separate fault runs
            public int rate_hz = 30;
            public double duration_s = 1800;
            public string[] canonical_joint_names;
            public double clock_drift_bound_ppm = -1;
            public string clock_bound_evidence = "";
            public double max_echo_age_s = 2;
            public double warmup_timeout_s = 30;
        }
        struct Arrival { public string raw; public double received; public int epoch; }
        public Settings settings = new Settings();
        // Bind the #46 named FK renderer; never apply transforms on a network thread.
        public event Action<StateFrame> FrameReceived;
        readonly ConcurrentQueue<Arrival> incoming = new ConcurrentQueue<Arrival>();
        readonly ConcurrentQueue<string> records = new ConcurrentQueue<string>();
        readonly ConcurrentDictionary<string, double> pendingEchoes = new ConcurrentDictionary<string, double>();
        BridgeFreshnessGate freshness;
        CancellationTokenSource lifetime;
        StreamWriter writer;
        double start, captureStart, lastState, lastAgeLog;
        double lastNetworkReceive;
        int queued, queueDrops, transportEpoch, processedEpoch = -1;
        bool finished, stale, measurementStarted;
        public bool StateIsStale => stale;
        public bool TransportIsStale => Now - Volatile.Read(ref lastNetworkReceive) > .25;
        double lastApplied = double.NegativeInfinity;
        public bool AppliedStateIsStale => Now - lastApplied > .25 || StateIsStale;
        static double Now => (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;
        static string N(double value) => value.ToString("R", CultureInfo.InvariantCulture);

        void Start()
        {
            string config = Path.Combine(Application.persistentDataPath, "bridge.local.json");
            if (File.Exists(config)) settings = JsonUtility.FromJson<Settings>(File.ReadAllText(config));
            if ((settings.candidate != "custom" && settings.candidate != "rosbridge") ||
                (settings.topology != "standalone-wifi" && settings.topology != "link-wired") ||
                (settings.rate_hz != 30 && settings.rate_hz != 60) || settings.duration_s <= 0 ||
                settings.canonical_joint_names == null || settings.canonical_joint_names.Length == 0)
                throw new InvalidOperationException("Complete bridge.local.json with verified canonical joint order");
            var names = new HashSet<string>(settings.canonical_joint_names);
            if (names.Count != settings.canonical_joint_names.Length) throw new InvalidOperationException("Duplicate canonical names");
            if (settings.max_echo_age_s <= 0 || settings.max_echo_age_s > 5) throw new InvalidOperationException("Echo freshness must be between 0 and 5 seconds");
            freshness = new BridgeFreshnessGate(settings.clock_drift_bound_ppm, settings.max_echo_age_s, !string.IsNullOrWhiteSpace(settings.clock_bound_evidence));
            string directory = Path.Combine(Application.persistentDataPath, "bridge-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            // Endpoint is station-private; keep this file private with raw capture.
            File.WriteAllText(Path.Combine(directory, "settings.local.json"), JsonUtility.ToJson(settings, true));
            writer = new StreamWriter(Path.Combine(directory, "messages.csv"));
            writer.WriteLine("event,session_id,seq,publish_host_ns,recv_client_s,sim_time,sim_step,c0_s,s1_ns,s2_ns,c3_s,apply_ms,queue_drops,source_kind,source_fresh,applied");
            start = captureStart = lastState = Now;
            Record("capture_start", start);
            lifetime = new CancellationTokenSource();
            _ = Task.Run(() => ConnectionLoop(lifetime.Token));
            UnityEngine.Debug.Log("Bridge benchmark started; public state only. Output: " + directory);
        }

        async Task Send(ClientWebSocket socket, string text, SemaphoreSlim gate, CancellationToken token)
        {
            await gate.WaitAsync(token);
            try { await socket.SendAsync(new ArraySegment<byte>(Encoding.UTF8.GetBytes(text)), WebSocketMessageType.Text, true, token); }
            finally { gate.Release(); }
        }

        async Task ConnectionLoop(CancellationToken stop)
        {
            int failures = 0;
            double[] backoff = { .25, .5, 1, 2, 5 };
            while (!stop.IsCancellationRequested)
            {
                using var socket = new ClientWebSocket();
                using var connection = CancellationTokenSource.CreateLinkedTokenSource(stop);
                using var gate = new SemaphoreSlim(1, 1);
                Task echoes = null;
                try
                {
                    using (var timeout = CancellationTokenSource.CreateLinkedTokenSource(stop))
                    { timeout.CancelAfter(5000); await socket.ConnectAsync(new Uri(settings.uri), timeout.Token); }
                    Record("connected", Now);
                    int epoch = Interlocked.Increment(ref transportEpoch);
                    while (incoming.TryDequeue(out _)) Interlocked.Decrement(ref queued);
                    pendingEchoes.Clear();
                    Interlocked.Exchange(ref lastNetworkReceive, Now);
                    if (settings.candidate == "rosbridge")
                    {
                        foreach (string topic in new[] { "/spike/state", "/spike/echo/reply" })
                            await Send(socket, JsonUtility.ToJson(new RosEnvelope { op = "subscribe", topic = topic, type = "std_msgs/msg/String" }), gate, connection.Token);
                        await Send(socket, JsonUtility.ToJson(new RosEnvelope { op = "advertise", topic = "/spike/echo/request", type = "std_msgs/msg/String" }), gate, connection.Token);
                    }
                    echoes = EchoLoop(socket, gate, connection.Token);
                    var buffer = new byte[16384];
                    while (!connection.IsCancellationRequested && socket.State == WebSocketState.Open)
                    {
                        using var payload = new MemoryStream();
                        WebSocketReceiveResult result;
                        do
                        {
                            result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), connection.Token);
                            if (result.MessageType == WebSocketMessageType.Close) throw new IOException("Peer closed");
                            if (result.MessageType != WebSocketMessageType.Text || payload.Length + result.Count > 262144) throw new IOException("Unsupported/oversized frame");
                            payload.Write(buffer, 0, result.Count);
                        } while (!result.EndOfMessage);
                        double received = Now; // before parsing and before Unity main-thread queue
                        Interlocked.Exchange(ref lastNetworkReceive, received);
                        failures = 0;
                        if (Interlocked.Increment(ref queued) > 4096)
                        { Interlocked.Decrement(ref queued); Interlocked.Increment(ref queueDrops); Record("queue_overflow", received); continue; }
                        incoming.Enqueue(new Arrival { raw = Encoding.UTF8.GetString(payload.ToArray()), received = received, epoch = epoch });
                    }
                }
                catch (OperationCanceledException) { }
                catch (Exception) { Record("transport_error", Now); } // no endpoint/credentials in evidence
                finally
                {
                    connection.Cancel(); socket.Abort();
                    if (echoes != null) { try { await echoes; } catch (Exception) { } }
                    Record("disconnected", Now);
                }
                double delay = backoff[Math.Min(failures++, backoff.Length - 1)];
                try { await Task.Delay(TimeSpan.FromSeconds(delay), stop); } catch (OperationCanceledException) { }
            }
        }

        async Task EchoLoop(ClientWebSocket socket, SemaphoreSlim gate, CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                var echo = new Echo { c0_s = N(Now) };
                pendingEchoes[echo.c0_s] = double.Parse(echo.c0_s, CultureInfo.InvariantCulture);
                foreach (var pending in pendingEchoes) if (Now - pending.Value > 5) pendingEchoes.TryRemove(pending.Key, out _);
                if (Now - Volatile.Read(ref lastNetworkReceive) > 2)
                { socket.Abort(); throw new IOException("Receive watchdog expired"); }
                string payload = "{\"kind\":\"echo\",\"c0_s\":\"" + echo.c0_s + "\"}";
                if (settings.candidate == "rosbridge")
                    payload = JsonUtility.ToJson(new RosEnvelope { op = "publish", topic = "/spike/echo/request", msg = new Message { data = payload } });
                await Send(socket, payload, gate, token);
                await Task.Delay(1000, token);
            }
        }

        void Update()
        {
            if (writer == null || finished) return;
            int epoch = Volatile.Read(ref transportEpoch);
            if (processedEpoch != epoch)
            { processedEpoch = epoch; freshness.Reset(); lastApplied = double.NegativeInfinity; Record("session_buffers_reset", Now); }
            // Process bounded batches; overflow is explicit and invalidates clean benchmark claims.
            int budget = 512;
            while (budget-- > 0 && incoming.TryDequeue(out Arrival item))
            {
                Interlocked.Decrement(ref queued);
                if (item.epoch != epoch) { Record("old_connection_frame", item.received); continue; }
                double before = Now;
                try
                {
                    JObject value = settings.candidate == "rosbridge" ? BridgeValidation.UnwrapRos(item.raw) : BridgeValidation.Parse(item.raw);
                    if (value == null) continue;
                    if (value["kind"]?.Value<string>() == "echo")
                    {
                        var echo = BridgeValidation.Echo(value);
                        if (!pendingEchoes.TryRemove(echo.c0_s, out double c0) || !freshness.Echo(c0, BridgeValidation.Nanoseconds(echo.s1_ns), BridgeValidation.Nanoseconds(echo.s2_ns), item.received))
                        { Record("invalid_echo", item.received); continue; }
                        records.Enqueue(string.Join(",", "echo", "", "", "", N(item.received), "", "", echo.c0_s, echo.s1_ns, echo.s2_ns, N(item.received), "", queueDrops, "", "", ""));
                        continue;
                    }
                    var frame = BridgeValidation.State(value, settings.canonical_joint_names);
                    bool fresh = freshness.Accept(frame, item.received, Now, out string reason);
                    bool applied = false;
                    if (fresh && FrameReceived != null) { FrameReceived.Invoke(frame); lastApplied = Now; applied = true; }
                    if (!measurementStarted && fresh && applied)
                    { measurementStarted = true; start = item.received; Record("run_start", start); }
                    if (!fresh) Record(measurementStarted ? reason : "warmup_" + reason, item.received);
                    double cost = (Now - before) * 1000;
                    lastState = item.received;
                    records.Enqueue(string.Join(",", measurementStarted ? "state" : "warmup_state", frame.session_id, frame.seq, frame.host_monotonic_ns, N(item.received), N(frame.sim_time), frame.sim_step,
                        "", "", "", "", N(cost), queueDrops, frame.source_kind, fresh ? "true" : "false", applied ? "true" : "false"));
                }
                catch (Exception) { Record("invalid_frame", item.received); }
            }
            bool fault = freshness.IsStale(Now);
            if (fault != stale) { stale = fault; Record(stale ? "stale_begin" : "stale_end", Now); }
            if (Now - lastAgeLog >= 1) { lastAgeLog = Now; Record("heartbeat", Now); }
            Flush();
            if (measurementStarted && Now - start >= settings.duration_s)
            { Record("run_end", Now); Flush(); finished = true; lifetime.Cancel(); }
            else if (!measurementStarted && Now - captureStart >= settings.warmup_timeout_s)
            { Record("warmup_failed", Now); Flush(); finished = true; lifetime.Cancel(); }
        }

        void Record(string name, double received) => records.Enqueue(string.Join(",", name, "", "", "", N(received), "", "", "", "", "", "", "", queueDrops, "", "", ""));
        void Flush() { while (records.TryDequeue(out string record)) writer.WriteLine(record); writer.Flush(); }
        void OnDisable() { lifetime?.Cancel(); if (writer != null) { Record("component_disabled", Now); Flush(); writer.Dispose(); writer = null; } }
    }
}
