using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Spikes.Bridge
{
    public static class BridgeValidation
    {
        static readonly string[] StateKeys = { "version", "kind", "source_kind", "session_id", "seq", "host_monotonic_ns", "sim_time", "sim_step", "joint_names", "joint_positions", "objects" };
        static void Require(bool condition, string reason) { if (!condition) throw new FormatException(reason); }
        public static JObject Parse(string raw)
        {
            Require(raw != null && raw.Length <= 262144, "oversized_json");
            using var text = new StringReader(raw);
            using var reader = new JsonTextReader(text) { MaxDepth = 12, DateParseHandling = DateParseHandling.None, FloatParseHandling = FloatParseHandling.Double };
            var value = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            Require(!reader.Read(), "trailing_json");
            return value;
        }
        static void Keys(JObject value, params string[] allowed) => Require(new HashSet<string>(value.Properties().Select(p => p.Name)).SetEquals(allowed), "unknown_or_missing_field");
        static string Text(JToken value) { Require(value?.Type == JTokenType.String, "string_required"); return value.Value<string>(); }
        static long Integer(JToken value) { Require(value?.Type == JTokenType.Integer, "integer_required"); long number = value.Value<long>(); Require(number >= 0, "negative_counter"); return number; }
        static double Number(JToken value) { Require(value != null && (value.Type == JTokenType.Float || value.Type == JTokenType.Integer), "number_required"); double number = value.Value<double>(); Require(!double.IsNaN(number) && !double.IsInfinity(number), "nonfinite_number"); return number; }
        static double[] Vector(JToken token, int length)
        { Require(token is JArray array && array.Count == length, "vector_shape"); return ((JArray)token).Select(Number).ToArray(); }
        public static ulong Nanoseconds(string value)
        { Require(value != null && Regex.IsMatch(value, "^[0-9]+$") && ulong.TryParse(value, out _), "invalid_nanoseconds"); return ulong.Parse(value, CultureInfo.InvariantCulture); }

        public static JObject UnwrapRos(string raw)
        {
            JObject wrapper = Parse(raw);
            string operation = Text(wrapper["op"]);
            if (operation != "publish") return null; // transport status is not scene state
            var names = wrapper.Properties().Select(p => p.Name).ToHashSet();
            Require(names.SetEquals(new[] { "op", "topic", "msg" }) || names.SetEquals(new[] { "op", "topic", "msg", "id" }), "ros_wrapper_fields");
            string topic = Text(wrapper["topic"]);
            Require(topic == "/spike/state" || topic == "/spike/echo/reply", "unexpected_topic");
            Require(wrapper["msg"] is JObject, "ros_message_object");
            var message = (JObject)wrapper["msg"]; Keys(message, "data");
            JObject inner = Parse(Text(message["data"]));
            Require(Text(inner["kind"]) == (topic == "/spike/state" ? "state" : "echo"), "topic_kind_mismatch");
            return inner;
        }

        public static BridgeBenchmark.StateFrame State(JObject value, string[] canonical)
        {
            Keys(value, StateKeys);
            Require(Integer(value["version"]) == 1 && Text(value["kind"]) == "state", "unsupported_envelope");
            string source = Text(value["source_kind"]), session = Text(value["session_id"]);
            Require(source == "live" || source == "synthetic", "source_kind");
            Require(Regex.IsMatch(session, "^[a-f0-9]{32}$"), "session_id");
            string published = Text(value["host_monotonic_ns"]); Nanoseconds(published);
            Require(value["joint_names"] is JArray, "joint_names_array");
            string[] names = ((JArray)value["joint_names"]).Select(Text).ToArray();
            Require(canonical != null && names.Length > 0 && names.SequenceEqual(canonical) && names.Distinct().Count() == names.Length, "canonical_order");
            double[] positions = Vector(value["joint_positions"], names.Length);
            Require(value["objects"] is JArray, "object_array");
            var objects = new List<BridgeBenchmark.PoseFrame>(); var ids = new HashSet<string>();
            foreach (JToken token in (JArray)value["objects"])
            {
                Require(token is JObject, "pose_object"); var pose = (JObject)token;
                Keys(pose, "id", "position_m", "rotation_xyzw");
                string id = Text(pose["id"]);
                Require(Regex.IsMatch(id, "^placeholder_[A-Za-z0-9_-]+$") && ids.Add(id), "pose_id");
                double[] position = Vector(pose["position_m"], 3), rotation = Vector(pose["rotation_xyzw"], 4);
                Require(Math.Abs(rotation.Sum(x => x * x) - 1) <= .01, "quaternion_norm");
                Require(position.All(x => Math.Abs(x) <= float.MaxValue) && rotation.All(x => Math.Abs(x) <= float.MaxValue), "float_overflow");
                objects.Add(new BridgeBenchmark.PoseFrame { id = id, position_m = position.Select(x => (float)x).ToArray(), rotation_xyzw = rotation.Select(x => (float)x).ToArray() });
            }
            double simTime = Number(value["sim_time"]); Require(simTime >= 0, "negative_sim_time");
            return new BridgeBenchmark.StateFrame { version = 1, kind = "state", source_kind = source, session_id = session,
                seq = Integer(value["seq"]), host_monotonic_ns = published, sim_time = simTime, sim_step = Integer(value["sim_step"]),
                joint_names = names, joint_positions = positions, objects = objects.ToArray() };
        }

        public static BridgeBenchmark.Echo Echo(JObject value)
        {
            Keys(value, "kind", "c0_s", "s1_ns", "s2_ns");
            Require(Text(value["kind"]) == "echo", "echo_kind");
            string c0 = Text(value["c0_s"]), s1 = Text(value["s1_ns"]), s2 = Text(value["s2_ns"]);
            Require(double.TryParse(c0, NumberStyles.Float, CultureInfo.InvariantCulture, out double start) && !double.IsNaN(start) && !double.IsInfinity(start), "echo_client_time");
            Nanoseconds(s1); Nanoseconds(s2);
            return new BridgeBenchmark.Echo { c0_s = c0, s1_ns = s1, s2_ns = s2 };
        }
    }

    public sealed class BridgeFreshnessGate
    {
        readonly double driftRate, maxEchoAge;
        readonly bool clockBoundQualified;
        readonly HashSet<string> retiredSessions = new HashSet<string>();
        string session;
        long lastSequence = -1, lastStep = -1;
        ulong lastPublished;
        double lastSimTime = -1, offsetLow, offsetHigh, echoAt, expires = double.NegativeInfinity;
        bool hasOffset;
        public BridgeFreshnessGate(double driftBoundPpm, double echoMaxAge, bool evidenceProvided)
        {
            if (double.IsNaN(driftBoundPpm) || double.IsInfinity(driftBoundPpm) ||
                double.IsNaN(echoMaxAge) || double.IsInfinity(echoMaxAge) || echoMaxAge <= 0)
                throw new ArgumentException("Finite drift bound and positive echo age required");
            driftRate = Math.Max(0, driftBoundPpm) / 1e6; maxEchoAge = echoMaxAge; clockBoundQualified = driftBoundPpm >= 0 && evidenceProvided;
        }
        public void Reset()
        { session = null; lastSequence = lastStep = -1; lastPublished = 0; lastSimTime = -1; retiredSessions.Clear(); hasOffset = false; expires = double.NegativeInfinity; }
        public bool Echo(double c0, ulong s1ns, ulong s2ns, double c3)
        {
            double s1 = s1ns / 1e9, s2 = s2ns / 1e9;
            if (s2 < s1 || c3 < c0 || c3 - c0 > maxEchoAge || s2 - s1 > c3 - c0) { hasOffset = false; expires = double.NegativeInfinity; return false; }
            // Offset interval needs no symmetry assumption: both network delays are nonnegative.
            double low = s2 - c3, high = s1 - c0;
            if (hasOffset)
            {
                double drift = Math.Abs(c3 - echoAt) * driftRate;
                if (low > offsetHigh + drift || high < offsetLow - drift)
                { hasOffset = false; expires = double.NegativeInfinity; return false; }
            }
            offsetLow = low; offsetHigh = high; echoAt = c3; hasOffset = true; return true;
        }
        public bool Accept(BridgeBenchmark.StateFrame frame, double received, double now, out string reason)
        {
            ulong published = BridgeValidation.Nanoseconds(frame.host_monotonic_ns);
            bool changed = session != frame.session_id;
            if ((changed && retiredSessions.Contains(frame.session_id)) || published <= lastPublished ||
                (!changed && (frame.seq <= lastSequence || frame.sim_step <= lastStep || frame.sim_time <= lastSimTime)))
            { reason = "nonprogressing_state"; return false; }
            if (changed) { if (session != null) retiredSessions.Add(session); session = frame.session_id; }
            lastSequence = frame.seq; lastStep = frame.sim_step; lastSimTime = frame.sim_time; lastPublished = published;
            if (now - received > .25) { reason = "stale_queued_frame"; return false; }
            if (!hasOffset || !clockBoundQualified || now - echoAt > maxEchoAge)
            { reason = "unknown_source_clock"; return false; }
            double sourceExpiry = (published / 1e9 + .25 - offsetHigh + driftRate * echoAt) / (1 + driftRate);
            double lowerAge = now + offsetLow - driftRate * Math.Abs(now - echoAt) - published / 1e9;
            if (lowerAge < -.001 && now + offsetHigh + driftRate * Math.Abs(now - echoAt) < published / 1e9)
            { reason = "future_source_timestamp"; return false; }
            double deadline = Math.Min(Math.Min(sourceExpiry, received + .25), echoAt + maxEchoAge);
            if (now > deadline) { reason = "stale_source"; return false; }
            expires = deadline; reason = "source_fresh"; return true;
        }
        public bool IsStale(double now) => !hasOffset || !clockBoundQualified || now > expires || now - echoAt > maxEchoAge;
    }
}
