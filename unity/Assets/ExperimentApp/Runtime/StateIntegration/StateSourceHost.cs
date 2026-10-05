using System;
using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.StateSources;
using AcousticVocab.Workcell;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.StateIntegration
{
    // Active root component; the presentation child can remain disabled during
    // startup. No transport worker owns a Unity transform or session command.
    [DisallowMultipleComponent]
    public sealed class StateSourceHost : MonoBehaviour
    {
        public FoundationBootstrap foundation;
        public WorkcellRegistry workcell;
        public string Kind => source?.Kind ?? "unresolved";
        public bool Initialized => isActiveAndEnabled && source!=null && !failed;
        public bool ResetConfirmed => CheckExposureReady();
        public double LastSimTime => source?.LastSimTime ?? 0;
        public double SampleAgeSeconds { get { RefreshSource(); return source?.SampleAgeSeconds ?? double.PositiveInfinity; } }
        public bool Stale => source?.Stale ?? true;
        public event Action<SourceEvent> Event;
        IRobotStateSource source;
        SnapshotSource snapshot;
        LiveSocketClient socket;
        StateSourceJournal journal;
        WorkcellStateRenderer renderer;
        bool failed, confirmedAtBoundary, refreshing;
        double nextSample;

        void Awake() { if(workcell!=null) workcell.gameObject.SetActive(false); }
        void Start()
        {
            if(failed) return;
            try
            {
                if(foundation==null || workcell==null || foundation.Configuration==null) throw new StateFault("SOURCE_FOUNDATION_UNAVAILABLE");
                var station=foundation.Configuration;
                var setup=StateSourceConfiguration.Load(ReadBounded(Path.Combine(Application.persistentDataPath,"state-source.local.json"),65536));
                if(workcell.ImportedLayout==null || workcell.LayoutSha256!=setup.LayoutHash) throw new StateFault("LAYOUT_HASH_MISMATCH");
                var registry=setup.Registry((string)station["station_id"],workcell.ImportedLayout.text,workcell.CanonicalJointNames);
                string kind=(string)station["robot_state_source"];
                var identity=Resources.Load<TextAsset>("BuildIdentity");
                if(identity==null) throw new StateFault("SOURCE_BUILD_IDENTITY");
                // Open evidence before reading the snapshot, so a bad snapshot
                // hash or schema is recorded durably as well as in the player log.
                journal=new StateSourceJournal(Path.Combine(Application.persistentDataPath,"operator-logs"),StateParser.Json(identity.text),registry,kind);
                byte[] neutralBytes=setup.LoadNeutralBytes(Application.persistentDataPath);
                double now=LiveSocketClient.Now;
                snapshot=new SnapshotSource(neutralBytes,registry,now);
                // The public wire intentionally has no movable root. Bind the
                // neutral to the imported fixed base, so an offset is refused.
                var neutral=StateParser.Json(System.Text.Encoding.UTF8.GetString(neutralBytes));
                var root=neutral["state"]["robot"]; var layout=StateParser.Json(workcell.ImportedLayout.text);
                var expected=layout["robot"]["position_m"];
                for(int i=0;i<3;i++) if(Math.Abs((double)root["root_position_m"][i]-(double)expected[i])>1e-6) throw new StateFault("SOURCE_FIXED_BASE");
                var rotation=root["root_rotation_xyzw"];
                if(Math.Abs((double)rotation[0])+Math.Abs((double)rotation[1])+Math.Abs((double)rotation[2])>1e-6 || Math.Abs(Math.Abs((double)rotation[3])-1)>1e-6) throw new StateFault("SOURCE_FIXED_BASE");
                renderer=new WorkcellStateRenderer(workcell);
                renderer.VerifyImportedNeutral(snapshot.Neutral);
                renderer.Apply(snapshot.Neutral);
                if(kind=="snapshot") source=snapshot;
                else if(kind=="live")
                {
                    var live=new LiveIsaacSource(now,setup.InterpolationDelay,setup.Clock); source=live;
                    socket=new LiveSocketClient((string)station["isaac_endpoint"],registry,live,setup.Clock);
                }
                else throw new StateFault("SOURCE_KIND");
                source.Event+=OnEvent;
                OnEvent(new SourceEvent("STATE_SOURCE_INITIALIZED",now,now));
                nextSample=now;
            }
            catch(StateFault error) { Fail(error.Message); }
            catch(Exception) { Fail("STATE_SOURCE_INITIALIZATION_FAILED"); }
        }
        static string ReadBounded(string path,long maximum)
        { var file=new FileInfo(path); if(!file.Exists || file.Length>maximum) throw new StateFault("SOURCE_CONFIG_MISSING_OR_LARGE"); return File.ReadAllText(path); }
        void OnEvent(SourceEvent value) { journal.Record(value); Event?.Invoke(value); }
        void Fail(string code)
        {
            if(failed) return; failed=true; confirmedAtBoundary=false; if(workcell!=null) workcell.gameObject.SetActive(false); socket?.Dispose();
            var value=new SourceEvent(code,LiveSocketClient.Now,LiveSocketClient.Now);
            try { journal?.Record(value); } catch(Exception) { }
            // Bounded codes only; no private paths, endpoint or exception text.
            Debug.LogError("STATE_SOURCE_FAULT "+code); Event?.Invoke(value);
        }
        void LateUpdate() => RefreshSource();
        bool RefreshSource()
        {
            if(!Initialized || refreshing) return false;
            refreshing=true;
            try
            {
                double now=LiveSocketClient.Now; socket?.Pump(now); var frame=source.Render(now);
                if(!foundation.Ready || !source.ResetConfirmed) confirmedAtBoundary=false;
                if(frame!=null) renderer.Apply(frame);
                workcell.gameObject.SetActive(foundation.Ready && frame!=null);
                if(now>=nextSample) { journal.Sample(source,now); nextSample=now+1.0/30; }
                return true;
            }
            catch(StateFault error) { Fail(error.Message); }
            catch(Exception) { Fail("STATE_SOURCE_RENDER_FAILED"); }
            finally { refreshing=false; }
            return false;
        }
        // Main-thread query at the actual cue boundary. It ages and applies
        // state synchronously; a stalled Update cannot read last frame's grant
        // before LateUpdate notices the outage. This never creates a grant.
        public bool CheckExposureReady()
        {
            return RefreshSource() && confirmedAtBoundary && foundation.Ready && source.ResetConfirmed && !source.Stale;
        }
        // Explicit per-trial gate. A rendered neutral never manufactures the
        // backend reset acknowledgment; the session must require both.
        public bool ConfirmReset()
        {
            if(!Initialized || !foundation.Ready) return false;
            try
            {
                double now=LiveSocketClient.Now; socket?.Pump(now);
                bool valid=source.ConfirmReset(snapshot.Neutral,now); var frame=source.Render(now);
                if(frame!=null) renderer.Apply(frame);
                OnEvent(new SourceEvent(valid?"STATE_RESET_CONFIRMED":"STATE_RESET_REFUSED",now,now));
                confirmedAtBoundary=valid;
                return valid;
            }
            catch(StateFault error) { Fail(error.Message); return false; }
            catch(Exception) { Fail("STATE_RESET_CHECK_FAILED"); return false; }
        }
        public void PlaySnapshotTrajectory(byte[] bytes,string sha256,double durationSeconds)
        {
            if(!Initialized || source!=snapshot || !foundation.Ready) throw new StateFault("SOURCE_PLAYBACK_UNAVAILABLE");
            confirmedAtBoundary=false;
            snapshot.PlayTrajectory(bytes,sha256,LiveSocketClient.Now,durationSeconds);
        }
        public void RestoreSnapshotNeutral()
        {
            if(!Initialized || source!=snapshot) throw new StateFault("SOURCE_RESET_UNAVAILABLE");
            confirmedAtBoundary=false;
            snapshot.RestoreNeutral(LiveSocketClient.Now); renderer.Apply(snapshot.Neutral);
        }
        void OnDestroy()
        {
            socket?.Dispose(); if(source!=null) source.Event-=OnEvent;
            try { journal?.Dispose(); } catch(Exception) { Debug.LogError("STATE_SOURCE_FAULT STATE_LOG_NOT_FINALIZED"); }
        }
        void OnDisable() { if(source!=null && !failed) Fail("STATE_HOST_DISABLED"); }
    }
}
