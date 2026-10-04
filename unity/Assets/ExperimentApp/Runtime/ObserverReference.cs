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
            var rotation = desiredObserverWorld.rotation * Quaternion.Inverse(currentHeadInOrigin.rotation);
            return new Pose(desiredObserverWorld.position - rotation * currentHeadInOrigin.position, rotation);
        }
    }
}
