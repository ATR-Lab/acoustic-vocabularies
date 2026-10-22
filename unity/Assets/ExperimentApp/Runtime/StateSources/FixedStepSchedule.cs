using System;
using System.Collections.Generic;

namespace AcousticVocab.StateSources
{
    // #56 fixed sim-step recording contract (isaac/demos/recording.py, runtime.py).
    // Every recorded demo is 300 samples, each taken after exactly 2 physics steps
    // at 1/60 s, so it spans 600 steps (10 s) and frame i carries sim_step 2*(i+1).
    // Playback is paced on the host monotonic clock by sample index: sample i is
    // shown at i/30 s after start and the last sample is held to 10 s. Neither
    // sim_time nor the retained capture host stamps (unpaced provenance) is ever
    // a playback clock. Python's recording.playback_index is the reference.
    public static class FixedStepSchedule
    {
        public const string Kind = "fixed_sim_step";
        public const string PlaybackClock = "host_monotonic_fixed_sample_period";
        public const int SampleCount = 300;
        public const int SampleHz = 30;
        public const int PhysicsStepsPerSample = 2;
        public const int TotalPhysicsSteps = SampleCount*PhysicsStepsPerSample;
        public const double PhysicsDtSeconds = 1.0/60;
        public const double DurationSeconds = 10;
        // The integer step count is the authority; float sim_time is only an
        // independent per-sample cross-check, as in the recorder.
        public const double SimTimeToleranceSeconds = 1e-5;

        public static double OffsetSeconds(int index)
        {
            if (index < 0 || index >= SampleCount) throw new StateFault("TRAJECTORY_SAMPLE_INDEX");
            return (double)index/SampleHz;
        }

        // Sample to show after elapsedHostSeconds of host monotonic time, or null
        // once the nominal 10 s boundary is reached. Takes no frame input.
        public static int? PlaybackIndex(double elapsedHostSeconds)
        {
            if (double.IsNaN(elapsedHostSeconds) || double.IsInfinity(elapsedHostSeconds) || elapsedHostSeconds < 0)
                throw new StateFault("HOST_CLOCK_REGRESSED");
            if (elapsedHostSeconds >= DurationSeconds) return null;
            return Math.Min(SampleCount-1, (int)Math.Floor(elapsedHostSeconds*SampleHz+1e-9));
        }

        // Refuses any trajectory off the schedule, with an explicit code for the
        // pre-fixed-step recordings (host-paced, sim_step = sample + 1).
        public static void CheckFrames(IReadOnlyList<SceneFrame> frames)
        {
            StateParser.Require(frames != null && frames.Count > 0, "TRAJECTORY_SAMPLE_COUNT");
            bool legacy = true;
            for (int i = 0; i < frames.Count && legacy; i++) legacy = frames[i].SimStep == i+1;
            StateParser.Require(!legacy, "TRAJECTORY_LEGACY_SCHEDULE");
            StateParser.Require(frames.Count == SampleCount, "TRAJECTORY_SAMPLE_COUNT");
            for (int i = 0; i < frames.Count; i++)
            {
                StateParser.Require(frames[i].SimStep == (long)PhysicsStepsPerSample*(i+1), "TRAJECTORY_SIM_STEP_SCHEDULE");
                if (i > 0)
                    StateParser.Require(Math.Abs(frames[i].SimTime-frames[i-1].SimTime-PhysicsStepsPerSample*PhysicsDtSeconds) <= SimTimeToleranceSeconds,
                        "TRAJECTORY_SIM_TIME_SCHEDULE");
            }
        }
    }
}
