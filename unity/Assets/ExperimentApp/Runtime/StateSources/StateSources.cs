using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.StateSources
{
    public sealed class SourceEvent
    {
        public string Code { get; }
        public double StartMonoSeconds { get; }
        public double ObservedMonoSeconds { get; }
        public double? DurationSeconds { get; }
        public SourceEvent(string code, double start, double observed, double? duration = null)
        { Code = code; StartMonoSeconds = start; ObservedMonoSeconds = observed; DurationSeconds = duration; }
    }
    public interface IRobotStateSource
    {
        string Kind { get; }
        bool Stale { get; }
        double SampleAgeSeconds { get; }
        double LastSimTime { get; }
        bool ResetConfirmed { get; }
        event Action<SourceEvent> Event;
        SceneFrame Render(double now);
        bool ConfirmReset(SceneFrame neutral, double now);
    }

    // Echo-derived interval does not assume symmetric network delay. The drift
    // bound needs independent evidence; an unqualified echo never enables cues.
    public sealed class SourceClock
    {
        readonly double drift, maxAge;
        readonly bool qualified;
        double low, high, at;
        bool measured;
        public SourceClock(double driftPpm, double maxEchoAgeSeconds, string evidenceSha256)
        {
            if (double.IsNaN(driftPpm) || double.IsInfinity(driftPpm) || driftPpm < 0 ||
                double.IsNaN(maxEchoAgeSeconds) || double.IsInfinity(maxEchoAgeSeconds) ||
                maxEchoAgeSeconds <= 0 || maxEchoAgeSeconds > 5) throw new ArgumentException("Invalid clock bounds");
            drift = driftPpm/1e6; maxAge = maxEchoAgeSeconds; qualified = StateParser.IsHash(evidenceSha256);
        }
        public bool Echo(double c0, ulong s1ns, ulong s2ns, double c3)
        {
            double s1=s1ns/1e9, s2=s2ns/1e9;
            if (double.IsNaN(c0) || double.IsNaN(c3) || double.IsInfinity(c0) || double.IsInfinity(c3) ||
                c3<c0 || s2<s1 || c3-c0>maxAge || s2-s1>c3-c0) { measured=false; return false; }
            double nextLow=s2-c3, nextHigh=s1-c0;
            if (measured && (nextLow>high+Math.Abs(c3-at)*drift || nextHigh<low-Math.Abs(c3-at)*drift))
            { measured=false; return false; }
            low=nextLow; high=nextHigh; at=c3; measured=true; return true;
        }
        public void Reset() { measured=false; }
        public bool Fresh(SceneFrame frame, double received, double now)
        {
            if (!qualified || !measured || now<at || now-at>maxAge || now<received || now-received>.25) return false;
            double published=frame.PublishedNs/1e9, tolerance=(now-at)*drift;
            double lowerAge=now+low-tolerance-published, upperAge=now+high+tolerance-published;
            return lowerAge<=.25 && upperAge<=.25 && upperAge>=-.001;
        }
    }

    public sealed class LiveIsaacSource : IRobotStateSource
    {
        sealed class Sample { public SceneFrame Frame; public double Received; }
        readonly List<Sample> buffer = new List<Sample>();
        readonly HashSet<string> retired = new HashSet<string>();
        readonly double delay, started;
        readonly SourceClock clock;
        SceneFrame latest, held, confirmedNeutral;
        double lastReceived, gapStart;
        bool stale;
        public string Kind => "live";
        public bool Stale => stale;
        public double SampleAgeSeconds { get; private set; }
        public double LastSimTime => latest?.SimTime ?? 0;
        public bool ResetConfirmed { get; private set; }
        public bool SourceFresh { get; private set; }
        public SceneFrame Latest => latest;
        public event Action<SourceEvent> Event;
        public LiveIsaacSource(double startMonoSeconds, double interpolationDelaySeconds=2.0/30, SourceClock sourceClock=null)
        {
            if (!Finite(startMonoSeconds) || !Finite(interpolationDelaySeconds) || interpolationDelaySeconds<0 || interpolationDelaySeconds>.2)
                throw new ArgumentException("Invalid source timing");
            started=lastReceived=startMonoSeconds; delay=interpolationDelaySeconds; clock=sourceClock;
        }
        static bool Finite(double value) => !double.IsNaN(value) && !double.IsInfinity(value);
        public bool Receive(SceneFrame frame, double received, double now)
        {
            if (!Finite(received) || !Finite(now) || received<started || now<received || now-received>.25)
            { Event?.Invoke(new SourceEvent("STATE_QUEUED_TOO_LONG", received, now)); return false; }
            if(clock!=null && !clock.Fresh(frame,received,now))
            {
                Tick(now); ResetConfirmed=false;
                Event?.Invoke(new SourceEvent("STATE_CLOCK_OR_AGE_INVALID",received,now));
                return false;
            }
            if (latest != null)
            {
                if (received<lastReceived) return false;
                bool same=frame.SessionId==latest.SessionId;
                if (retired.Contains(frame.SessionId) || (same &&
                    (frame.Sequence<=latest.Sequence || frame.SimStep<=latest.SimStep ||
                     frame.SimTime<=latest.SimTime || frame.PublishedNs<=latest.PublishedNs)))
                { Event?.Invoke(new SourceEvent("STATE_NONPROGRESSING", received, now)); return false; }
                if (!same)
                {
                    if (retired.Count>=128) throw new StateFault("STATE_RESTART_LIMIT");
                    retired.Add(latest.SessionId); buffer.Clear(); ResetConfirmed=false;
                    Event?.Invoke(new SourceEvent("STATE_SOURCE_RESTART", received, now));
                }
                else if (frame.Sequence!=latest.Sequence+1)
                    Event?.Invoke(new SourceEvent("STATE_SEQUENCE_GAP", lastReceived, now));
            }
            Tick(now); // Detect a gap even if the render loop was suspended.
            if (stale)
            {
                Event?.Invoke(new SourceEvent("STATE_RECOVERED", gapStart, now, received-gapStart));
                stale=false;
            }
            lastReceived=received; latest=frame;
            if(ResetConfirmed && !NeutralComparison.Matches(frame,confirmedNeutral)) ResetConfirmed=false;
            buffer.Add(new Sample { Frame=frame, Received=received });
            if (buffer.Count>128) buffer.RemoveAt(0);
            SourceFresh=clock!=null && clock.Fresh(frame, received, now);
            if (!SourceFresh) ResetConfirmed=false;
            return true;
        }
        public void Invalidate(string code,double now)
        { ResetConfirmed=false; SourceFresh=false; Event?.Invoke(new SourceEvent(code,now,now)); }
        void Tick(double now)
        {
            if (!Finite(now) || now<lastReceived) throw new StateFault("HOST_CLOCK_REGRESSED");
            SampleAgeSeconds=now-lastReceived;
            SourceFresh=latest!=null && clock!=null && clock.Fresh(latest, lastReceived, now);
            if (SampleAgeSeconds>.25 && !stale)
            {
                stale=true; gapStart=lastReceived; ResetConfirmed=false;
                Event?.Invoke(new SourceEvent("STATE_STALE", gapStart, now));
            }
            if (!SourceFresh) ResetConfirmed=false;
        }
        public SceneFrame Render(double now)
        {
            Tick(now);
            if (stale) return held ?? latest;
            if (buffer.Count==0) return null;
            double renderAt=now-delay;
            while (buffer.Count>2 && buffer[1].Received<=renderAt) buffer.RemoveAt(0);
            if (renderAt<=buffer[0].Received) held=buffer[0].Frame;
            else if (buffer.Count>1 && renderAt<buffer[1].Received)
                held=SceneFrame.Interpolate(buffer[0].Frame, buffer[1].Frame,
                    (renderAt-buffer[0].Received)/(buffer[1].Received-buffer[0].Received));
            else held=buffer[buffer.Count-1].Frame; // Hold newest, never extrapolate.
            return held;
        }
        public bool ConfirmReset(SceneFrame neutral, double now)
        {
            Tick(now);
            ResetConfirmed=!stale && SourceFresh && NeutralComparison.Matches(latest, neutral) &&
                NeutralComparison.Matches(Render(now), neutral);
            confirmedNeutral=ResetConfirmed?neutral:null;
            return ResetConfirmed;
        }
    }

    public static class NeutralComparison
    {
        public static bool Matches(SceneFrame frame, SceneFrame neutral)
        {
            if (frame==null || neutral==null || frame.Joints.Count!=neutral.Joints.Count || frame.Objects.Count!=neutral.Objects.Count) return false;
            for (int i=0;i<frame.Joints.Count;i++)
                if (Math.Abs(frame.Joints[i]-neutral.Joints[i])>Math.PI/360) return false;
            for (int i=0;i<frame.Objects.Count;i++)
            {
                var a=frame.Objects[i]; var b=neutral.Objects[i];
                if (a.Id!=b.Id || Vector3.Distance(a.Position,b.Position)>.001 ||
                    Quaternion.Angle(a.Rotation,b.Rotation)>.5 || a.Visible!=b.Visible || a.Enabled!=b.Enabled ||
                    !VisualMatches(a.VisualState,b.VisualState)) return false;
            }
            return true;
        }
        static bool VisualMatches(JObject a,JObject b)
        {
            if(a.Count!=b.Count) return false;
            foreach(var field in a.Properties())
            {
                var other=b[field.Name]; if(other==null) return false;
                bool Numeric(JToken value) => value.Type==JTokenType.Integer || value.Type==JTokenType.Float;
                // JSON numeric spelling is not physical state. 1 and 1.0 are
                // exactly equal; booleans, strings and field sets stay strict.
                if(Numeric(field.Value) && Numeric(other))
                { if((double)field.Value!=(double)other) return false; }
                else if(!JToken.DeepEquals(field.Value,other)) return false;
            }
            return true;
        }
    }
}
