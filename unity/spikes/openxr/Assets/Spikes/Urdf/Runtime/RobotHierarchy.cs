using System;
using System.Collections.Generic;
using UnityEngine;

namespace AcousticVocab.Spikes.Urdf
{
    [Serializable] public sealed class Origin { public float[] xyz; public float[] rpy; }
    [Serializable] public sealed class Visual { public Origin origin; public string mesh_file; public float[] scale; public float[] color; }
    [Serializable] public sealed class Link { public string name; public Visual[] visuals; }
    [Serializable] public sealed class Joint
    {
        public string name, type, parent, child;
        public Origin origin;
        public float[] axis;
        public double lower, upper;
    }
    [Serializable] public sealed class RobotDescription
    {
        public int schema_version;
        public string robot, source_sha256, root_link;
        public Link[] links;
        public Joint[] joints;
    }
    [Serializable] public sealed class JointBinding
    {
        public string name;
        public Transform link;
        public Vector3 unityAxis;
        public double lower, upper;
    }
    [Serializable] public sealed class LinkBinding { public string name; public Transform link; }

    // A display-only URDF FK model. There are deliberately no physics bodies.
    // This identity-name API applies URDF coordinates, not a validated Isaac map.
    public sealed class RobotHierarchy : MonoBehaviour
    {
        public JointBinding[] joints = Array.Empty<JointBinding>();
        public LinkBinding[] links = Array.Empty<LinkBinding>();
        Dictionary<string, JointBinding> lookup;
        void EnsureLookup()
        {
            if (lookup != null) return;
            lookup = new Dictionary<string, JointBinding>(StringComparer.Ordinal);
            foreach (var joint in joints) lookup.Add(joint.name, joint);
        }
        public bool ApplyJoint(string name, double radians)
        {
            EnsureLookup();
            if (!lookup.TryGetValue(name, out var joint) || double.IsNaN(radians) || double.IsInfinity(radians)) return false;
            // Do not clamp: limits differ across USD/URDF sources and must remain
            // visible during this comparison. The exporter reports requested q.
            joint.link.localRotation = Quaternion.AngleAxis((float)(radians * 180 / Math.PI), joint.unityAxis);
            return true;
        }
        public void ResetJoints()
        {
            foreach (var joint in joints) joint.link.localRotation = Quaternion.identity;
        }
        public static Vector3 ToUnity(float[] value) => new(-value[1], value[2], value[0]);
        public static float[] ToRos(Vector3 value) => new[] { value.z, -value.x, value.y };
        public static Quaternion OriginRotation(float[] rpy)
        {
            // URDF fixed-axis RPY is Rz(yaw)*Ry(pitch)*Rx(roll).
            var ros = Quaternion.AngleAxis(rpy[2] * Mathf.Rad2Deg, Vector3.forward) *
                Quaternion.AngleAxis(rpy[1] * Mathf.Rad2Deg, Vector3.up) *
                Quaternion.AngleAxis(rpy[0] * Mathf.Rad2Deg, Vector3.right);
            return new Quaternion(ros.y, -ros.z, -ros.x, ros.w);
        }
        public static Quaternion ToUnityQuaternion(float[] wxyz) => new(wxyz[2], -wxyz[3], -wxyz[1], wxyz[0]);
        public static float[] ToRosQuaternion(Quaternion value) => new[] { value.w, -value.z, value.x, -value.y };
    }
}
