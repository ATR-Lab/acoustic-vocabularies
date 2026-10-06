using UnityEngine;

namespace AcousticVocab.Workcell
{
    // Isaac right-handed Z-up metres to Unity left-handed Y-up metres.
    // The reflection changes axial vectors' sign; rotations are C R C^-1.
    public static class SceneCoordinates
    {
        public static Vector3 Position(Vector3 isaac) => new(-isaac.y, isaac.z, isaac.x);
        public static Vector3 InversePosition(Vector3 unity) => new(unity.z, -unity.x, unity.y);
        public static Quaternion Rotation(Quaternion isaacXyzw) => new(isaacXyzw.y, -isaacXyzw.z, -isaacXyzw.x, isaacXyzw.w);
        public static Quaternion AxisRotation(Vector3 isaacAxis, float radians) => Quaternion.AngleAxis(radians * Mathf.Rad2Deg, -Position(isaacAxis));
        public static bool Finite(float x) => !float.IsNaN(x) && !float.IsInfinity(x);
        public static bool Finite(Vector3 v) => Finite(v.x) && Finite(v.y) && Finite(v.z);
        public static bool Unit(Quaternion q) => Finite(q.x) && Finite(q.y) && Finite(q.z) && Finite(q.w) && Mathf.Abs(Quaternion.Dot(q, q) - 1) <= .0001f;
    }
}
