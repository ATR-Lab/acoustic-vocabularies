using UnityEngine;
using AcousticVocab.Spikes.Urdf;

namespace AcousticVocab.Spikes.Bridge
{
    // Copy only after #46's custom FK renderer is present. No URDF-Importer dependency.
    public sealed class BridgeRobotAdapter : MonoBehaviour
    {
        public BridgeBenchmark benchmark;
        public RobotHierarchy robot;
        void OnEnable() { if (benchmark != null) benchmark.FrameReceived += Apply; }
        void OnDisable() { if (benchmark != null) benchmark.FrameReceived -= Apply; }
        void Apply(BridgeBenchmark.StateFrame frame)
        {
            if (robot == null) throw new System.InvalidOperationException("Assign the verified #46 robot renderer");
            for (int index = 0; index < frame.joint_names.Length; index++)
                if (!robot.ApplyJoint(frame.joint_names[index], frame.joint_positions[index]))
                    throw new System.InvalidOperationException("Unknown canonical joint; reject bridge mapping");
        }
    }
}
