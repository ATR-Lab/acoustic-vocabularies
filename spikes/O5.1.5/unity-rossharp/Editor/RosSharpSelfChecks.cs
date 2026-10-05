using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using RosSharp.RosBridgeClient;
using RosSharp.RosBridgeClient.Protocols;
using UnityEditor;
using RosString = RosSharp.RosBridgeClient.MessageTypes.Std.String;

namespace AcousticVocab.Spikes.Bridge.Editor
{
    public static class RosSharpSelfChecks
    {
        sealed class TestProtocol : IProtocol
        {
            public readonly List<JObject> Sent = new List<JObject>();
            bool alive;
            public event EventHandler OnReceive, OnConnected, OnClosed;
            public void Connect() { alive = true; OnConnected?.Invoke(this, EventArgs.Empty); }
            public void Close() { alive = false; OnClosed?.Invoke(this, EventArgs.Empty); }
            public bool IsAlive() => alive;
            public void Send(byte[] bytes) => Sent.Add(JObject.Parse(Encoding.UTF8.GetString(bytes)));
            public void Receive(string topic, string data) => OnReceive?.Invoke(this, new MessageEventArgs(Encoding.UTF8.GetBytes(
                new JObject { ["op"] = "publish", ["topic"] = topic, ["msg"] = new JObject { ["data"] = data } }.ToString(Formatting.None))));
        }
        static void Check(bool value, string label) { if (!value) throw new Exception(label); }

        [MenuItem("Spikes/Bridge/Validate actual ROS# library")]
        public static void Run()
        {
            var protocol = new TestProtocol();
            var socket = new RosSocket(protocol, RosSocket.SerializerEnum.Newtonsoft_JSON);
            int stateCount = 0, echoCount = 0;
            socket.Subscribe<RosString>("/spike/state", message => { Check(message.data == "public-state", "typed state payload"); stateCount++; }, queue_length: 1);
            socket.Subscribe<RosString>("/spike/echo/reply", message => { Check(message.data == "public-echo", "typed echo payload"); echoCount++; }, queue_length: 1);
            var subscriptions = protocol.Sent.Where(x => x["op"].Value<string>() == "subscribe").ToArray();
            Check(subscriptions.Length == 2 && subscriptions.All(x => x["type"].Value<string>() == "std_msgs/msg/String" && x["queue_length"].Value<int>() == 1), "ROS2 type and bounded subscription queue");
            string publisher = socket.Advertise<RosString>("/spike/echo/request");
            socket.Publish(publisher, new RosString("public-request"));
            Check(protocol.Sent.Last()["msg"]["data"].Value<string>() == "public-request", "actual RosSocket serialization");
            protocol.Receive("/spike/state", "public-state"); protocol.Receive("/spike/echo/reply", "public-echo");
            Check(stateCount == 1 && echoCount == 1, "actual RosSocket dispatch/deserialization");
            socket.Close(); Check(!protocol.IsAlive(), "close lifecycle");
            UnityEngine.Debug.Log("PASS: ROS# 2.3.0 actual RosSocket/Newtonsoft ROS2 subscribe, publish, dispatch and close.");
        }

        static string Argument(string name, string fallback)
        { var args = Environment.GetCommandLineArgs(); int index = Array.IndexOf(args, name); return index >= 0 && index + 1 < args.Length ? args[index + 1] : fallback; }

        // Bounded Editor process smoke through the authorized loopback SSH path. No renderer/clock acceptance.
        public static void LiveSmoke()
        {
            string uri = Argument("-bridgeRosSharpUri", "ws://127.0.0.1:18765");
            string output = Argument("-bridgeOutput", "");
            string map = Argument("-bridgeJointMap", "");
            string expected = Argument("-bridgeExpectedSource", "synthetic");
            string[] names = string.IsNullOrEmpty(map) ? new[] { "synthetic_joint" } : JsonConvert.DeserializeObject<string[]>(File.ReadAllText(map));
            using var connected = new ManualResetEvent(false);
            using var received = new ManualResetEvent(false);
            long states = 0, echoes = 0, invalid = 0;
            var protocol = new WebSocketSharpProtocol(uri);
            protocol.OnConnected += (_, __) => connected.Set();
            var socket = new RosSocket(protocol, RosSocket.SerializerEnum.Newtonsoft_JSON);
            double started = (double)Stopwatch.GetTimestamp() / Stopwatch.Frequency;
            string c0 = started.ToString("R", System.Globalization.CultureInfo.InvariantCulture);
            try
            {
                Check(connected.WaitOne(10000), "ROS# connection timeout");
                socket.Subscribe<RosString>("/spike/state", message =>
                {
                    try { var frame = BridgeValidation.State(BridgeValidation.Parse(message.data), names); Check(frame.source_kind == expected, "unexpected source kind"); Interlocked.Increment(ref states); }
                    catch (Exception) { Interlocked.Increment(ref invalid); }
                    if (Interlocked.Read(ref states) >= 10 && Interlocked.Read(ref echoes) > 0) received.Set();
                }, queue_length: 1);
                socket.Subscribe<RosString>("/spike/echo/reply", message =>
                {
                    try { var echo = BridgeValidation.Echo(BridgeValidation.Parse(message.data)); Check(echo.c0_s == c0, "uncorrelated echo"); Interlocked.Increment(ref echoes); }
                    catch (Exception) { Interlocked.Increment(ref invalid); }
                    if (Interlocked.Read(ref states) >= 10 && Interlocked.Read(ref echoes) > 0) received.Set();
                }, queue_length: 1);
                string publisher = socket.Advertise<RosString>("/spike/echo/request");
                // DDS subscription discovery can precede publisher discovery; retry one correlated request.
                for (int attempt = 0; attempt < 10 && !received.WaitOne(0); attempt++)
                { socket.Publish(publisher, new RosString("{\"kind\":\"echo\",\"c0_s\":\"" + c0 + "\"}")); received.WaitOne(1000); }
                bool passed = states >= 10 && echoes > 0 && invalid == 0;
                var report = new JObject { ["ros_sharp"] = "2.3.0", ["source_kind"] = expected, ["typed_states"] = states, ["correlated_echoes"] = echoes,
                    ["invalid_messages"] = invalid, ["protocol_smoke_passed"] = passed, ["renderer_applied"] = false, ["source_clock_qualified"] = false, ["device_acceptance"] = false };
                if (!string.IsNullOrEmpty(output)) File.WriteAllText(output, report.ToString(Formatting.Indented) + "\n");
                Check(passed, "ROS# state/echo smoke incomplete; inspect private report");
                UnityEngine.Debug.Log("PASS: actual ROS# WebSocketSharp/RosSocket received strict public state and correlated echo. Diagnostic only.");
            }
            finally { socket.Close(); }
        }
    }
}
