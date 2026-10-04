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
        }
        struct Arrival { public string raw; public double received; }
        public Settings settings = new Settings();
        // Bind the #46 named FK renderer; never apply transforms on a network thread.
        public event Action<StateFrame> FrameReceived;
        readonly ConcurrentQueue<Arrival> incoming = new ConcurrentQueue<Arrival>();
        readonly ConcurrentQueue<string> records = new ConcurrentQueue<string>();
        CancellationTokenSource lifetime;
        StreamWriter writer;
        double start, lastState, lastAgeLog;
        double lastNetworkReceive;
        int queued, queueDrops;
        bool finished, stale;
        public bool StateIsStale => stale;
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
            string directory = Path.Combine(Application.persistentDataPath, "bridge-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            // Endpoint is station-private; keep this file private with raw capture.
            File.WriteAllText(Path.Combine(directory, "settings.local.json"), JsonUtility.ToJson(settings, true));
            writer = new StreamWriter(Path.Combine(directory, "messages.csv"));
            writer.WriteLine("event,session_id,seq,publish_host_ns,recv_client_s,sim_time,sim_step,c0_s,s1_ns,s2_ns,c3_s,apply_ms,queue_drops,source_kind");
            start = lastState = Now;
            Record("run_start", start);
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
                        if (Interlocked.Increment(ref queued) > 4096)
                        { Interlocked.Decrement(ref queued); Interlocked.Increment(ref queueDrops); Record("queue_overflow", received); continue; }
                        incoming.Enqueue(new Arrival { raw = Encoding.UTF8.GetString(payload.ToArray()), received = received });
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
                try { await Task.Delay(500, stop); } catch (OperationCanceledException) { }
            }
        }

        async Task EchoLoop(ClientWebSocket socket, SemaphoreSlim gate, CancellationToken token)
        {
            while (!token.IsCancellationRequested)
            {
                var echo = new Echo { c0_s = N(Now) };
                if (Now - Volatile.Read(ref lastNetworkReceive) > 2)
                { socket.Abort(); throw new IOException("Receive watchdog expired"); }
                string payload = "{\"kind\":\"echo\",\"c0_s\":\"" + echo.c0_s + "\"}";
                if (settings.candidate == "rosbridge")
                    payload = JsonUtility.ToJson(new RosEnvelope { op = "publish", topic = "/spike/echo/request", msg = new Message { data = payload } });
                await Send(socket, payload, gate, token);
                await Task.Delay(1000, token);
            }
        }

        bool Validate(StateFrame frame)
        {
            if (frame == null || frame.version != 1 || frame.kind != "state" ||
                (frame.source_kind != "live" && frame.source_kind != "synthetic") ||
                frame.session_id == null || frame.session_id.Length != 32 || frame.seq < 0 || frame.sim_step < 0 ||
                !ulong.TryParse(frame.host_monotonic_ns, out _) || double.IsNaN(frame.sim_time) || double.IsInfinity(frame.sim_time) ||
                frame.joint_names == null || frame.joint_positions == null ||
                frame.joint_names.Length != settings.canonical_joint_names.Length || frame.joint_positions.Length != frame.joint_names.Length)
                return false;
            for (int i = 0; i < frame.joint_names.Length; i++)
                if (frame.joint_names[i] != settings.canonical_joint_names[i] || double.IsNaN(frame.joint_positions[i]) || double.IsInfinity(frame.joint_positions[i])) return false;
            return true;
        }

        void Update()
        {
            if (writer == null || finished) return;
            // Process bounded batches; overflow is explicit and invalidates clean benchmark claims.
            int budget = 512;
            while (budget-- > 0 && incoming.TryDequeue(out Arrival item))
            {
                Interlocked.Decrement(ref queued);
                double before = Now;
                try
                {
                    string raw = item.raw;
                    if (settings.candidate == "rosbridge")
                    {
                        var wrapper = JsonUtility.FromJson<RosEnvelope>(raw);
                        if (wrapper.op != "publish" || (wrapper.topic != "/spike/state" && wrapper.topic != "/spike/echo/reply")) continue;
                        raw = wrapper.msg.data;
                    }
                    var type = JsonUtility.FromJson<Echo>(raw);
                    if (type.kind == "echo")
                    {
                        records.Enqueue(string.Join(",", "echo", "", "", "", N(item.received), "", "", type.c0_s, type.s1_ns, type.s2_ns, N(item.received), "", queueDrops, ""));
                        continue;
                    }
                    var frame = JsonUtility.FromJson<StateFrame>(raw);
                    if (!Validate(frame)) { Record("invalid_frame", item.received); continue; }
                    // Stale receive queues must never animate an apparently fresh robot.
                    if (Now - item.received <= .25) FrameReceived?.Invoke(frame);
                    else Record("stale_queued_frame", item.received);
                    double cost = (Now - before) * 1000;
                    lastState = item.received;
                    records.Enqueue(string.Join(",", "state", frame.session_id, frame.seq, frame.host_monotonic_ns, N(item.received), N(frame.sim_time), frame.sim_step,
                        "", "", "", "", N(cost), queueDrops, frame.source_kind));
                }
                catch (Exception) { Record("invalid_frame", item.received); }
            }
            bool fault = Now - lastState > .25;
            if (fault != stale) { stale = fault; Record(stale ? "stale_begin" : "stale_end", Now); }
            if (Now - lastAgeLog >= 1) { lastAgeLog = Now; Record("heartbeat", Now); }
            Flush();
            if (Now - start >= settings.duration_s)
            { Record("run_end", Now); Flush(); finished = true; lifetime.Cancel(); }
        }

        void Record(string name, double received) => records.Enqueue(string.Join(",", name, "", "", "", N(received), "", "", "", "", "", "", "", queueDrops, ""));
        void Flush() { while (records.TryDequeue(out string record)) writer.WriteLine(record); writer.Flush(); }
        void OnDisable() { lifetime?.Cancel(); if (writer != null) { Record("component_disabled", Now); Flush(); writer.Dispose(); writer = null; } }
    }
}
