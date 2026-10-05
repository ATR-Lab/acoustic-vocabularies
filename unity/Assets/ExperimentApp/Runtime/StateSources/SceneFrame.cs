using System;
using System.Collections.Generic;
using System.Linq;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.StateSources
{
    public sealed class StateFault : Exception { public StateFault(string code) : base(code) { } }

    // Coordinates stay in Isaac world space until the renderer's single conversion.
    public sealed class SceneObject
    {
        public string Id { get; }
        public Vector3 Position { get; }
        public Quaternion Rotation { get; }
        public bool Visible { get; }
        public bool Enabled { get; }
        readonly JObject state;
        public JObject VisualState => (JObject)state.DeepClone();
        public SceneObject(string id, Vector3 position, Quaternion rotation, bool visible, bool enabled, JObject visual)
        { Id = id; Position = position; Rotation = rotation; Visible = visible; Enabled = enabled; state = (JObject)visual.DeepClone(); }
    }

    public sealed class SceneFrame
    {
        public string SessionId { get; }
        public long Sequence { get; }
        public ulong PublishedNs { get; }
        public double SimTime { get; }
        public long SimStep { get; }
        public string Provenance { get; }
        public IReadOnlyList<double> Joints { get; }
        public IReadOnlyList<SceneObject> Objects { get; }
        public SceneFrame(string session, long sequence, ulong published, double simTime, long step,
                          string provenance, IEnumerable<double> joints, IEnumerable<SceneObject> objects)
        {
            SessionId = session; Sequence = sequence; PublishedNs = published; SimTime = simTime;
            SimStep = step; Provenance = provenance;
            Joints = Array.AsReadOnly(joints.ToArray()); Objects = Array.AsReadOnly(objects.ToArray());
        }
        public static SceneFrame Interpolate(SceneFrame a, SceneFrame b, double fraction)
        {
            if (fraction <= 0) return a;
            if (fraction >= 1) return b;
            float t = (float)fraction;
            if (a.Joints.Count != b.Joints.Count || a.Objects.Count != b.Objects.Count) throw new StateFault("STATE_INVENTORY_CHANGED");
            var objects = new List<SceneObject>();
            for (int i = 0; i < a.Objects.Count; i++)
            {
                var first = a.Objects[i]; var last = b.Objects[i];
                if (first.Id != last.Id) throw new StateFault("STATE_INVENTORY_CHANGED");
                // Discrete state is left-continuous. Continuous public hinge
                // and arrow coordinates interpolate on the same host timeline.
                var visual=first.VisualState; var endVisual=last.VisualState;
                foreach(string key in new[]{"arrow_angle_rad","lid_open_fraction"})
                    if(visual[key]!=null && endVisual[key]!=null)
                        visual[key]=(double)visual[key]+((double)endVisual[key]-(double)visual[key])*fraction;
                Quaternion rotation=Quaternion.SlerpUnclamped(first.Rotation,last.Rotation,t);
                if(visual["card_face"]!=null && endVisual["card_face"]!=null)
                {
                    // At release Isaac can transfer a physical half-turn from
                    // the root into card_face without changing the visible card.
                    // Interpolate that effective pose, then retain the earlier
                    // discrete face so this representation change cannot spin it.
                    Quaternion faceA=Quaternion.AngleAxis((int)visual["card_face"]*180,Vector3.right);
                    Quaternion faceB=Quaternion.AngleAxis((int)endVisual["card_face"]*180,Vector3.right);
                    rotation=Quaternion.SlerpUnclamped(first.Rotation*faceA,last.Rotation*faceB,t)*Quaternion.Inverse(faceA);
                }
                objects.Add(new SceneObject(first.Id, Vector3.LerpUnclamped(first.Position, last.Position, t),
                    rotation,first.Visible, first.Enabled, visual));
            }
            // Joint coordinates are limited articulation coordinates, not circular headings.
            var joints = a.Joints.Select((value, i) => value + (b.Joints[i]-value)*fraction);
            return new SceneFrame(a.SessionId, a.Sequence, a.PublishedNs,
                a.SimTime+(b.SimTime-a.SimTime)*fraction, a.SimStep, a.Provenance, joints, objects);
        }
    }

    public sealed class SceneRegistry
    {
        public string StationId { get; }
        public string SceneHash { get; }
        public string SnapshotHash { get; }
        public IReadOnlyList<string> JointNames { get; }
        internal readonly SortedDictionary<string, string[]> ObjectKeys;
        internal readonly HashSet<string> Anchors;
        public SceneRegistry(string station, string sceneHash, string snapshotHash, IEnumerable<string> names,
                             IDictionary<string, string[]> objects, IEnumerable<string> anchors)
        {
            StationId = station; SceneHash = sceneHash; SnapshotHash = snapshotHash;
            JointNames = Array.AsReadOnly(names.ToArray());
            if (JointNames.Count != 43 || JointNames.Distinct().Count() != 43 ||
                !StateParser.IsHash(sceneHash) || !StateParser.IsHash(snapshotHash))
                throw new StateFault("REGISTRY_INVALID");
            ObjectKeys = new SortedDictionary<string, string[]>(StringComparer.Ordinal);
            foreach (var pair in objects) ObjectKeys.Add(pair.Key, pair.Value.OrderBy(x => x, StringComparer.Ordinal).ToArray());
            Anchors = new HashSet<string>(anchors, StringComparer.Ordinal);
            foreach (var key in ObjectKeys.Keys) Anchors.Add(key);
            if (ObjectKeys.Count == 0) throw new StateFault("REGISTRY_EMPTY");
        }
        public static string Hash(byte[] bytes)
        { using var sha = SHA256.Create(); return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant(); }
    }
}
