using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.StateSources
{
    public static class StateParser
    {
        public static bool IsHash(string text) => text != null && Regex.IsMatch(text, "^[0-9a-f]{64}$");
        internal static void Require(bool valid, string code) { if (!valid) throw new StateFault(code); }
        internal static void Keys(JObject obj, params string[] names)
        { Require(obj != null && new HashSet<string>(obj.Properties().Select(p => p.Name)).SetEquals(names), "STATE_FIELDS"); }
        internal static string Text(JToken value)
        { Require(value?.Type == JTokenType.String, "STATE_STRING"); return (string)value; }
        internal static double Number(JToken value)
        {
            Require(value != null && (value.Type == JTokenType.Float || value.Type == JTokenType.Integer), "STATE_NUMBER");
            double result = (double)value; Require(!double.IsNaN(result) && !double.IsInfinity(result), "STATE_NONFINITE"); return result;
        }
        static long Counter(JToken value)
        { Require(value?.Type == JTokenType.Integer, "STATE_COUNTER"); long number = (long)value; Require(number >= 0, "STATE_COUNTER"); return number; }
        static bool Boolean(JToken value) { Require(value?.Type == JTokenType.Boolean, "STATE_BOOLEAN"); return (bool)value; }
        internal static double[] Vector(JToken value, int length)
        {
            Require(value is JArray array && array.Count == length, "STATE_VECTOR");
            return ((JArray)value).Select(Number).ToArray();
        }
        public static ulong Nanoseconds(JToken value)
        {
            string text = Text(value);
            Require(Regex.IsMatch(text, "^(0|[1-9][0-9]{0,19})$") &&
                ulong.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out _), "STATE_CLOCK");
            return ulong.Parse(text, CultureInfo.InvariantCulture);
        }
        public static JObject Json(string raw)
        {
            try { return StationConfig.ParseStrict(raw); }
            catch (Exception) { throw new StateFault("STATE_JSON"); }
        }
        public static SceneFrame Parse(string raw, SceneRegistry registry, bool allowSynthetic = false)
        {
            try { return Parse(Json(raw), registry, allowSynthetic); }
            catch (StateFault) { throw; }
            catch (Exception) { throw new StateFault("STATE_MALFORMED"); }
        }
        public static SceneFrame Parse(JObject value, SceneRegistry registry, bool allowSynthetic = false)
        {
            Keys(value, "version", "kind", "source_kind", "station_id", "scene_sha256", "reset_snapshot_sha256",
                "session_id", "seq", "host_monotonic_ns", "sim_time", "sim_step", "joint_names", "joint_positions", "objects");
            Require(Counter(value["version"]) == 2 && Text(value["kind"]) == "state", "STATE_VERSION");
            Require(Text(value["station_id"]) == registry.StationId, "WRONG_STATION");
            Require(Text(value["scene_sha256"]) == registry.SceneHash && Text(value["reset_snapshot_sha256"]) == registry.SnapshotHash, "STATE_HASH_IDENTITY");
            string provenance = Text(value["source_kind"]);
            Require(provenance == "live" || (allowSynthetic && provenance == "synthetic"), "STATE_SOURCE_KIND");
            string session = Text(value["session_id"]);
            Require(Regex.IsMatch(session, "^[0-9a-f]{32}$"), "STATE_SESSION");
            Require(value["joint_names"] is JArray, "STATE_JOINT_ORDER");
            Require(((JArray)value["joint_names"]).Select(Text).SequenceEqual(registry.JointNames), "STATE_JOINT_ORDER");
            double simTime = Number(value["sim_time"]); Require(simTime >= 0, "STATE_SIM_TIME");
            Require(value["objects"] is JArray objects && objects.Count == registry.ObjectKeys.Count, "STATE_INVENTORY");
            var parsed = new List<SceneObject>(); int index = 0;
            foreach (var pair in registry.ObjectKeys)
                parsed.Add(ParseObject((JObject)value["objects"][index++], pair.Key, pair.Value, registry));
            return new SceneFrame(session, Counter(value["seq"]), Nanoseconds(value["host_monotonic_ns"]), simTime,
                Counter(value["sim_step"]), provenance, Vector(value["joint_positions"], 43), parsed);
        }
        internal static SceneObject ParseObject(JObject obj, string id, string[] stateKeys, SceneRegistry registry)
        {
            Keys(obj, "id", "position_m", "rotation_xyzw", "visible", "enabled", "state");
            Require(Text(obj["id"]) == id, "STATE_OBJECT_ORDER");
            var position = Vector(obj["position_m"], 3); var rotation = Vector(obj["rotation_xyzw"], 4);
            Require(position.All(x => Math.Abs(x) <= float.MaxValue), "STATE_FLOAT_OVERFLOW");
            Require(Math.Abs(rotation.Sum(x => x*x)-1) <= 1e-5, "STATE_QUATERNION");
            var state = obj["state"] as JObject; Keys(state, stateKeys);
            foreach (var property in state.Properties())
            {
                var value = property.Value;
                switch (property.Name)
                {
                    case "card_face": Require(value.Type == JTokenType.Integer && ((int)value == 0 || (int)value == 1), "STATE_CARD_FACE"); break;
                    case "arrow_angle_rad": Require(Math.Abs(Number(value)) <= Math.PI*2, "STATE_ARROW"); break;
                    case "lid_open_fraction": Require(Number(value) >= 0 && Number(value) <= 1, "STATE_LID"); break;
                    case "tag_attached": Boolean(value); break;
                    case "location": Require(registry.Anchors.Contains(Text(value)), "STATE_LOCATION"); break;
                    default: throw new StateFault("STATE_VISUAL_FIELD");
                }
            }
            return new SceneObject(id, new Vector3((float)position[0], (float)position[1], (float)position[2]),
                new Quaternion((float)rotation[0], (float)rotation[1], (float)rotation[2], (float)rotation[3]),
                Boolean(obj["visible"]), Boolean(obj["enabled"]), state);
        }
    }
}
