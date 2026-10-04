using UnityEngine;

namespace AcousticVocab.Foundation
{
    public sealed class ObserverReference
    {
        public bool RestorePending { get; private set; } = true;
        public bool MarkRecenter()
        {
            bool first = !RestorePending;
            RestorePending = true;
            return first;
        }
        public void Restored() => RestorePending = false;
        // One rigid origin change at a safe boundary, never continuous head locking.
        public static Pose ResolveOrigin(Pose desiredObserverWorld, Pose currentHeadInOrigin)
        {
            Vector3 desiredForward = desiredObserverWorld.rotation * Vector3.forward;
            Vector3 currentForward = currentHeadInOrigin.rotation * Vector3.forward;
            desiredForward.y = 0; currentForward.y = 0;
            if (currentForward.sqrMagnitude < .0001f) throw new ConfigurationFault("head_heading_undefined");
            var desiredYaw = Quaternion.LookRotation(desiredForward, Vector3.up);
            var currentYaw = Quaternion.LookRotation(currentForward, Vector3.up);
            var rotation = desiredYaw * Quaternion.Inverse(currentYaw);
            return new Pose(desiredObserverWorld.position - rotation * currentHeadInOrigin.position, rotation);
        }
    }
}
