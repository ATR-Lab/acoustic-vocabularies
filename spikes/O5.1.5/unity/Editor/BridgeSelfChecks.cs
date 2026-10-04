using System;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace AcousticVocab.Spikes.Bridge.Editor
{
    public static class BridgeSelfChecks
    {
        static readonly string[] Names = { "public_joint" };
        static JObject Example() => BridgeValidation.Parse("{\"version\":1,\"kind\":\"state\",\"source_kind\":\"synthetic\",\"session_id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"seq\":0,\"host_monotonic_ns\":\"11020000000\",\"sim_time\":0,\"sim_step\":0,\"joint_names\":[\"public_joint\"],\"joint_positions\":[0],\"objects\":[]}");
        static void Assert(bool value, string message) { if (!value) throw new Exception(message); }
        static void Reject(Action action, string label)
        { bool rejected = false; try { action(); } catch (Exception) { rejected = true; } Assert(rejected, "Expected rejection: " + label); }
        static BridgeFreshnessGate Synced()
        { var gate = new BridgeFreshnessGate(100, 2, true); Assert(gate.Echo(1, 11010000000, 11011000000, 1.021), "valid echo"); return gate; }

        [MenuItem("Spikes/Bridge/Validate strict parsing and freshness")]
        public static void Run()
        {
            var valid = BridgeValidation.State(Example(), Names);
            var top = Example(); top["trial_id"] = "forbidden";
            Reject(() => BridgeValidation.State(top, Names), "top-level private field");
            Reject(() => BridgeValidation.Parse("{\"kind\":\"state\",\"kind\":\"echo\"}"), "duplicate keys");
            Reject(() => BridgeValidation.Parse("{} {}"), "trailing JSON");
            var pose = Example(); pose["objects"] = JArray.Parse("[{\"id\":\"placeholder_0\",\"position_m\":[0,0,0],\"rotation_xyzw\":[0,0,0,1],\"target\":\"forbidden\"}]");
            Reject(() => BridgeValidation.State(pose, Names), "nested private field");
            ((JObject)pose["objects"][0]).Remove("target");
            pose["objects"][0]["rotation_xyzw"] = new JArray(0, 0, 0, 0);
            Reject(() => BridgeValidation.State(pose, Names), "invalid quaternion");
            var badSession = Example(); badSession["session_id"] = new string('z', 32);
            Reject(() => BridgeValidation.State(badSession, Names), "nonhex session");
            var nonfinite = Example(); nonfinite["joint_positions"] = new JArray(double.NaN);
            Reject(() => BridgeValidation.State(nonfinite, Names), "nonfinite joint");
            Reject(() => BridgeValidation.State(Example(), new[] { "wrong_joint" }), "canonical mismatch");
            var gate = Synced();
            Assert(gate.Accept(valid, 1.03, 1.03, out _), "fresh initial state");
            Assert(!gate.Accept(valid, 1.2, 1.2, out var reason) && reason == "nonprogressing_state", "replay rejected");
            Assert(gate.IsStale(1.4), "replays cannot extend source liveness");
            var stalled = BridgeValidation.State(Example(), Names); stalled.seq = 1; stalled.host_monotonic_ns = "11040000000";
            Assert(!gate.Accept(stalled, 1.05, 1.05, out reason) && reason == "nonprogressing_state", "sim stall rejected");
            var backlog = BridgeValidation.State(Example(), Names); backlog.host_monotonic_ns = "10500000000";
            Assert(!Synced().Accept(backlog, 1.03, 1.03, out reason) && reason == "stale_source", "upstream backlog rejected");
            Assert(!Synced().Accept(valid, 1.03, 1.4, out reason) && reason == "stale_queued_frame", "local backlog rejected");
            var unknown = new BridgeFreshnessGate(-1, 2, false);
            Assert(!unknown.Accept(valid, 1.03, 1.03, out reason) && reason == "unknown_source_clock", "unknown clocks fail closed");
            gate.Reset();
            Assert(!gate.Accept(valid, 1.03, 1.03, out reason) && reason == "unknown_source_clock", "reconnect resets offset");
            Assert(!Synced().Echo(2, 11010000000, 11011000000, 1), "negative RTT rejected");
            Debug.Log("PASS: strict keys/nested poses, canonical names, duplicate JSON, replay/stall/source/queue age, unknown clock and reconnect reset.");
        }
    }
}
