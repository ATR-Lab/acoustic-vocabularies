using System;
using System.Collections.Generic;
using UnityEngine;

namespace AcousticVocab.Workcell
{
    // Public physical state only. Null means absent, not "keep the old value".
    public readonly struct PublicVisualState
    {
        public readonly int? cardFace;
        public readonly float? arrowAngleRad, lidOpenFraction;
        public readonly bool? tagAttached;
        public readonly string location;
        public PublicVisualState(int? cardFace = null, float? arrowAngleRad = null, float? lidOpenFraction = null, bool? tagAttached = null, string location = null)
        { this.cardFace = cardFace; this.arrowAngleRad = arrowAngleRad; this.lidOpenFraction = lidOpenFraction; this.tagAttached = tagAttached; this.location = location; }
    }

    [Serializable] public sealed class ObjectBinding
    {
        public string id, kind, label;
        public Transform root, visual;
        public float primaryPixelMetres, lidOpenAngleRad;
        public bool semanticEnabled;
        public Vector3 neutralPosition;
        public Quaternion neutralRotation;
        public bool neutralVisible, neutralEnabled;
        public bool hasCardFace, hasArrowAngle, hasLidFraction, hasTagAttached, hasLocation;
        public int neutralCardFace;
        public float neutralArrowAngle, neutralLidFraction;
        public bool neutralTagAttached;
        public string neutralLocation;
        [NonSerialized] public PublicVisualState currentState;
        public PublicVisualState NeutralState => new(hasCardFace ? neutralCardFace : null, hasArrowAngle ? neutralArrowAngle : null,
            hasLidFraction ? neutralLidFraction : null, hasTagAttached ? neutralTagAttached : null, hasLocation ? neutralLocation : null);
    }
    [Serializable] public sealed class RobotJointBinding
    {
        public string name;
        public Transform link;
        public Vector3 unityAxis;
        public float lowerRad, upperRad, neutralRad;
    }
    [Serializable] public sealed class RobotLinkBinding { public string name; public Transform link; }

    // Display-only geometry. No network, session, answer, audio or physics API.
    public sealed class WorkcellRegistry : MonoBehaviour
    {
        public TextAsset importedLayout;
        public string layoutSha256;
        public ObjectBinding[] objects = Array.Empty<ObjectBinding>();
        public RobotJointBinding[] joints = Array.Empty<RobotJointBinding>();
        public RobotLinkBinding[] links = Array.Empty<RobotLinkBinding>();
        public string[] anchorIds = Array.Empty<string>();
        Dictionary<string, ObjectBinding> byId;
        Dictionary<string, RobotJointBinding> byJoint;
        HashSet<string> anchors;
        IReadOnlyList<string> jointNames;
        public TextAsset ImportedLayout => importedLayout;
        public string LayoutSha256 => layoutSha256;
        public IReadOnlyList<string> CanonicalJointNames { get { EnsureLookup(); return jointNames; } }
        void EnsureLookup()
        {
            if (byId != null) return;
            byId = new(StringComparer.Ordinal); byJoint = new(StringComparer.Ordinal); anchors = new(anchorIds, StringComparer.Ordinal);
            foreach (var item in objects) byId.Add(item.id, item);
            var names = new string[joints.Length];
            for (int i = 0; i < joints.Length; i++) { byJoint.Add(joints[i].name, joints[i]); names[i] = joints[i].name; }
            jointNames = Array.AsReadOnly(names);
        }
        public bool ApplyJoint(string name, float radians)
        {
            EnsureLookup();
            if (name == null || !byJoint.TryGetValue(name, out var joint) || !SceneCoordinates.Finite(radians) ||
                radians < joint.lowerRad - .00001f || radians > joint.upperRad + .00001f) return false;
            joint.link.localRotation = Quaternion.AngleAxis(radians * Mathf.Rad2Deg, joint.unityAxis); return true;
        }
        public bool TryGetObject(string id, out ObjectBinding binding) { EnsureLookup(); binding = null; return id != null && byId.TryGetValue(id, out binding); }
        public bool ApplyObject(string id, Vector3 localPosition, Quaternion localRotation, bool visible, bool enabled, PublicVisualState state)
        {
            EnsureLookup();
            if (!TryGetObject(id, out var item) || !SceneCoordinates.Finite(localPosition) || !SceneCoordinates.Unit(localRotation) || !ValidState(item, state)) return false;
            // All checks precede mutation. Enabled is semantic and independent of visible.
            item.root.localPosition = localPosition; item.root.localRotation = localRotation;
            item.root.gameObject.SetActive(visible); item.semanticEnabled = enabled; item.currentState = state;
            if (state.cardFace.HasValue) item.visual.localRotation = SceneCoordinates.AxisRotation(Vector3.right, state.cardFace.Value * Mathf.PI);
            if (state.arrowAngleRad.HasValue) item.visual.localRotation = SceneCoordinates.AxisRotation(Vector3.forward, state.arrowAngleRad.Value);
            if (state.lidOpenFraction.HasValue) item.visual.localRotation = SceneCoordinates.AxisRotation(Vector3.up, item.lidOpenAngleRad * state.lidOpenFraction.Value);
            return true;
        }
        bool ValidState(ObjectBinding item, PublicVisualState state) =>
            item.hasCardFace == state.cardFace.HasValue && item.hasArrowAngle == state.arrowAngleRad.HasValue &&
            item.hasLidFraction == state.lidOpenFraction.HasValue && item.hasTagAttached == state.tagAttached.HasValue &&
            item.hasLocation == (state.location != null) &&
            (!state.cardFace.HasValue || state.cardFace.Value == 0 || state.cardFace.Value == 1) &&
            (!state.arrowAngleRad.HasValue || SceneCoordinates.Finite(state.arrowAngleRad.Value)) &&
            (!state.lidOpenFraction.HasValue || SceneCoordinates.Finite(state.lidOpenFraction.Value) && state.lidOpenFraction.Value >= 0 && state.lidOpenFraction.Value <= 1) &&
            (state.location == null || anchors.Contains(state.location));
        public void ResetToImportedNeutral()
        {
            foreach (var item in objects)
                if (!ApplyObject(item.id, item.neutralPosition, item.neutralRotation, item.neutralVisible, item.neutralEnabled, item.NeutralState))
                    throw new InvalidOperationException("Invalid imported neutral object state.");
            foreach (var joint in joints)
                if (!ApplyJoint(joint.name, joint.neutralRad)) throw new InvalidOperationException("Invalid imported neutral joint state.");
        }
    }
}
