using System;

namespace AcousticVocab.Foundation
{
    // Engineering startup stabilization, not a tracking-loss grace period.
    // Runtime origin notifications may trail GetTrackingOriginMode by one or more frames.
    // Once presentation starts, the existing recenter fault latch owns all later events.
    public sealed class StartupOriginGate
    {
        public const double DefaultStableSeconds = .250;
        readonly double stableSeconds;
        double? stableSince;
        public bool Settled { get; private set; }
        public StartupOriginGate(double stableSeconds = DefaultStableSeconds)
        {
            if (double.IsNaN(stableSeconds) || double.IsInfinity(stableSeconds) || stableSeconds <= 0)
                throw new ArgumentOutOfRangeException(nameof(stableSeconds));
            this.stableSeconds = stableSeconds;
        }
        public void Reset() { stableSince = null; Settled = false; }
        public bool Observe(bool eligible, double monotonicSeconds)
        {
            if (double.IsNaN(monotonicSeconds) || double.IsInfinity(monotonicSeconds) || monotonicSeconds < 0)
                throw new ArgumentOutOfRangeException(nameof(monotonicSeconds));
            if (!eligible) { Reset(); return false; }
            if (!stableSince.HasValue || monotonicSeconds < stableSince.Value) stableSince = monotonicSeconds;
            Settled = monotonicSeconds - stableSince.Value >= stableSeconds;
            return Settled;
        }
    }
}
