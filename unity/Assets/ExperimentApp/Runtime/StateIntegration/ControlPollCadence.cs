using System;
namespace AcousticVocab.StateIntegration
{
    internal static class ControlPollCadence
    {
        // Target the complete sequential request cycle, not an extra sleep
        // after its network cost. A positive yield prevents a busy retry loop;
        // this never changes the independent 250 ms evidence freshness gate.
        internal static int DelayMilliseconds(double requestStartedMs,double responseFinishedMs)
        {
            if(!double.IsFinite(requestStartedMs)||!double.IsFinite(responseFinishedMs)||requestStartedMs<0||responseFinishedMs<requestStartedMs)
                throw new ControlFault("CONTROL_POLL_CLOCK");
            double elapsed=responseFinishedMs-requestStartedMs;
            return elapsed>=74?1:(int)Math.Ceiling(75-elapsed);
        }
    }
}
