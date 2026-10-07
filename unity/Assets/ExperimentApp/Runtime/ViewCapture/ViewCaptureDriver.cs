using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.ViewCapture
{
    public sealed class ViewCaptureSettings
    {
        public int PoseSettleFrames{get;}public int PreCueFrames{get;}public int StepTimeoutMs{get;}public string RunName{get;}
        public ViewCaptureSettings(int poseSettleFrames,int preCueFrames,int stepTimeoutMs,string runName)
        {
            ViewCaptureFault.Require(poseSettleFrames>=1&&poseSettleFrames<=120&&preCueFrames>=1&&preCueFrames<=120&&stepTimeoutMs>=100&&stepTimeoutMs<=10000,"CAPTURE_SETTINGS");
            PoseSettleFrames=poseSettleFrames;PreCueFrames=preCueFrames;StepTimeoutMs=stepTimeoutMs;RunName=runName;
        }
        internal JObject ToJson()=>new JObject{["pose_settle_frames"]=PoseSettleFrames,["pre_cue_frames"]=PreCueFrames,["step_timeout_ms"]=StepTimeoutMs};
    }

    public sealed class ViewCapturePorts
    {
        public IViewCaptureClock Clock;public IViewCaptureControl Control;public IHeadPoseRig Rig;public IFrameGrabber Grabber;
        public IStateReadback State;public IAudioRouting Audio;public IViewInventory View;public IProtectedTrialBoundary Boundary;
        internal bool Complete=>Clock!=null&&Control!=null&&Rig!=null&&Grabber!=null&&State!=null&&Audio!=null&&View!=null&&Boundary!=null;
    }

    // Protected-test-mode-only capture sequencer. For each plan pose and each
    // of the 32 legal pairs: load the pair at the trial boundary, require a
    // fresh accepted reset, set/read back the pose, wait for the configured
    // pre-cue point, capture, then mark the cue request. The first refusal
    // stops the run: no retry, no skipped capture, no replaced file.
    public sealed class ViewCaptureDriver
    {
        readonly ViewCapturePlan plan;readonly ViewCapturePorts ports;readonly ViewCaptureSettings settings;readonly SimulationTestAuthority authority;
        readonly long planSeenNs;readonly HashSet<string> resetIds=new HashSet<string>(StringComparer.Ordinal);
        ViewCaptureOutput output;JObject runReference;long lastCapturedNs;bool started,loaded;
        public string Fault{get;private set;}
        public bool Finished{get;private set;}
        public bool Complete=>Finished&&Fault==null;
        public int CapturesCompleted=>output?.RowCount??0;
        public string RunDirectory=>output?.Directory;
        public JObject StatusReference{get;private set;}

        public ViewCaptureDriver(ViewCapturePlan plan,SimulationTestAuthority authority,ViewCapturePorts ports,ViewCaptureSettings settings,long planSeenNs)
            :this(plan,authority,ports,settings,planSeenNs,SimulationTestAuthority.CompiledCapability){}
        // The compiled flag is injectable so tests can prove the refusal that
        // an ordinary player build (without AV_SIMULATION_TEST) receives.
        internal ViewCaptureDriver(ViewCapturePlan plan,SimulationTestAuthority authority,ViewCapturePorts ports,ViewCaptureSettings settings,long planSeenNs,bool compiledCapability)
        {
            ViewCaptureFault.Require(compiledCapability,"VIEW_CAPTURE_NOT_COMPILED");
            ViewCaptureFault.Require(authority!=null&&!authority.ParticipantAdmission,"VIEW_CAPTURE_SIMULATION_AUTHORITY_REQUIRED");
            ViewCaptureFault.Require(plan!=null&&settings!=null&&ports!=null&&ports.Complete,"VIEW_CAPTURE_PORTS");
            ViewCaptureFault.Require(planSeenNs>0,"VIEW_CAPTURE_CLOCK");
            this.plan=plan;this.authority=authority;this.ports=ports;this.settings=settings;this.planSeenNs=planSeenNs;
        }

        // Synchronous gate and run header. Refusal here creates no output when
        // the gate fails, and a status record for any later header failure.
        public void Start()
        {
            ViewCaptureFault.Require(!started,"VIEW_CAPTURE_ALREADY_STARTED");started=true;
            var control=ports.Control;
            ViewCaptureFault.Require(control.RequiredMode=="test"&&control.ModeAcknowledged&&control.FaultCode==null,"VIEW_CAPTURE_PROTECTED_TEST_MODE_REQUIRED");
            ViewCaptureFault.Require(control.ControlSessionId!=null&&System.Text.RegularExpressions.Regex.IsMatch(control.ControlSessionId,@"\A[0-9a-f]{32}\z"),"VIEW_CAPTURE_CONTROL_SESSION");
            ViewCaptureFault.Require((plan.CaptureSurface=="desktop_engineering")==ports.Rig.CanSetPose,"VIEW_CAPTURE_SURFACE_MISMATCH");
            long now=ports.Clock.NowNs;ViewCaptureFault.Require(now>=planSeenNs,"VIEW_CAPTURE_CLOCK");
            output=ViewCaptureOutput.Create(authority.OutputDirectory,settings.RunName);
            try
            {
                var run=new JObject{["version"]=1,["kind"]="view_capture_run",["scope"]="SIMULATION_TEST",["participant_admission"]=false,["g3_signed"]=false,
                    ["plan_sha256"]=plan.Sha256,["station_id"]=plan.StationId,["source_kind"]=plan.SourceKind,["capture_surface"]=plan.CaptureSurface,
                    ["build_sha256"]=plan.BuildSha256,["clock_id"]=Guid.NewGuid().ToString("N"),["clock_kind"]="client_stopwatch_monotonic_ns",["plan_seen_ns"]=planSeenNs.ToString(),
                    ["control_session_id"]=control.ControlSessionId,["simulation_capability_sha256"]=authority.RawSha256,
                    ["capture_method"]=ports.Grabber.Method,["state_origin"]=ports.State.Origin,["trial_boundary"]=ports.Boundary.Kind,
                    ["settings"]=settings.ToJson(),["poses"]=new JArray(plan.Poses.Select(p=>p.Id)),["legal_pairs"]=LegalPairs.All.Count,["captures_required"]=plan.CapturesRequired};
                runReference=output.WriteJson(ViewCaptureOutput.RunFile,run);output.OpenRows();
            }
            catch(ViewCaptureFault error){Finish(error.Code);throw;}
            catch(Exception){Finish("VIEW_CAPTURE_HEADER_FAILED");throw new ViewCaptureFault("VIEW_CAPTURE_HEADER_FAILED");}
        }

        // Unity coroutine (or test loop). Each yield is one rendered frame.
        public IEnumerator Run()
        {
            if(!started||output==null||Finished)throw new ViewCaptureFault("VIEW_CAPTURE_NOT_STARTED");
            var inner=Captures().GetEnumerator();
            while(true)
            {
                bool more;
                try{more=inner.MoveNext();}
                catch(ViewCaptureFault error){Finish(error.Code);yield break;}
                catch(Exception){Finish("VIEW_CAPTURE_RUNTIME_FAILED");yield break;}
                if(!more)break;
                yield return inner.Current;
            }
            Finish(null);
        }

        // External abort (host disabled, application quit). Records, never retries.
        public void Abort(string code){if(started&&!Finished)Finish(code??"VIEW_CAPTURE_ABORTED");}

        void Finish(string fault)
        {
            if(Finished)return;Finished=true;Fault=fault;
            if(loaded){loaded=false;try{ports.Boundary.Unload();}catch(Exception){Fault??="TRIAL_BOUNDARY_UNLOAD_FAILED";}}
            if(output==null)return;
            if(Fault==null&&output.RowCount!=plan.CapturesRequired)Fault="CAPTURE_MATRIX_INCOMPLETE";
            try{StatusReference=output.WriteStatus(Fault==null,Fault,plan.CapturesRequired,runReference);}
            catch(Exception){Fault??="CAPTURE_STATUS_WRITE_FAILED";}
            finally{output.Dispose();}
        }

        void Deadline(long since,string code)=>ViewCaptureFault.Require(ports.Clock.NowNs-since<=settings.StepTimeoutMs*1_000_000L,code);
        void ControlHealthy()=>ViewCaptureFault.Require(ports.Control.FaultCode==null,"CONTROL_FAILED");

        IEnumerable<object> Captures()
        {
            int index=0;
            foreach(var pose in plan.Poses)
                foreach(var pair in LegalPairs.All)
                {
                    index++;
                    foreach(var step in Capture(index,pose,pair))yield return step;
                }
        }

        IEnumerable<object> Capture(int index,PlanPose pose,LegalPair pair)
        {
            var clock=ports.Clock;var control=ports.Control;var rig=ports.Rig;
            ports.Boundary.Load(pair);loaded=true;
            // A fresh reset is requested for every capture and its exact
            // accepted test-mode acknowledgment is required. None is reused.
            ControlHealthy();string requestId=control.RequestReset();long requested=clock.NowNs;
            ViewCaptureFault.Require(requestId!=null&&resetIds.Add(requestId),"RESET_ID_REUSED");
            byte[] reply;long receivedNs;
            while(!control.TryResetAcknowledged(requestId,out reply,out receivedNs)){ControlHealthy();Deadline(requested,"RESET_ACK_TIMEOUT");yield return null;}
            ViewCaptureFault.Require(reply!=null&&reply.Length>0&&reply.Length<=65536,"RESET_REPLY_INVALID");
            CheckReply(reply,requestId,control.ControlSessionId);
            ViewCaptureFault.Require(receivedNs>=planSeenNs&&receivedNs>=lastCapturedNs&&receivedNs<=clock.NowNs,"RESET_RECEIPT_ORDER");

            if(rig.CanSetPose)rig.SetPose(pose.Pose);
            long poseRequested=clock.NowNs;int frames=0;
            while(true)
            {
                yield return null;frames++;
                if(frames<settings.PoseSettleFrames)continue;
                var read=rig.ReadPose();
                if(read.Matches(pose.Pose,plan.HeadPositionToleranceM,plan.HeadOrientationToleranceRad))break;
                // A placed camera that reads back elsewhere is a fault, not
                // something to set again. A tracked head may still be arriving.
                ViewCaptureFault.Require(!rig.CanSetPose,"HEAD_POSE_READBACK_MISMATCH");
                Deadline(poseRequested,"HEAD_POSE_NOT_REACHED");
            }
            for(int i=0;i<settings.PreCueFrames;i++)yield return null;

            // Pre-cue point. Every remaining check refuses rather than waits,
            // except for the first post-reset neutral state sample.
            StateReading state;long stateRequested=clock.NowNs;
            while(!ports.State.TryRead(receivedNs,out state)){ControlHealthy();Deadline(stateRequested,"STATE_SAMPLE_UNAVAILABLE");yield return null;}
            ViewCaptureFault.Require(state!=null&&(plan.SourceKind=="snapshot"?state.State!=null:state.Applied!=null),"STATE_READING_INCOMPLETE");
            ViewCaptureFault.Require(control.TryResetAcknowledged(requestId,out _,out long stillReceived)&&stillReceived==receivedNs,"RESET_NOT_CURRENT_AT_CAPTURE");
            var head=rig.ReadPose();
            ViewCaptureFault.Require(head.Matches(pose.Pose,plan.HeadPositionToleranceM,plan.HeadOrientationToleranceRad),"HEAD_POSE_READBACK_MISMATCH");
            var view=ports.View.Read();
            ViewCaptureFault.Require(view?.View!=null&&view.Inventory!=null,"VIEW_INVENTORY_UNAVAILABLE");
            ViewCaptureFault.Require((string)view.View["panel_state_sha256"]==plan.PanelStateSha256,"PANEL_STATE_MISMATCH");
            ViewCaptureFault.Require(view.View["console_visible"]?.Type==JTokenType.Boolean&&!(bool)view.View["console_visible"]&&
                view.View["diagnostics_visible"]?.Type==JTokenType.Boolean&&!(bool)view.View["diagnostics_visible"],"VISIBLE_OVERLAY");
            var audio=ports.Audio.Read();ViewCaptureFault.Require(audio!=null,"AUDIO_ROUTING_UNAVAILABLE");

            long capturedNs=clock.NowNs;
            byte[] png=ports.Grabber.CapturePng(plan.Width,plan.Height);
            var after=rig.ReadPose();
            ViewCaptureFault.Require(after.Matches(head,plan.HeadPositionToleranceM,plan.HeadOrientationToleranceRad),"HEAD_MOVED_DURING_CAPTURE");
            long cueNs=ports.Boundary.RequestCue();
            loaded=false;ports.Boundary.Unload();
            CaptureFiles.RequirePng(png,plan.Width,plan.Height);
            ViewCaptureFault.Require(capturedNs>lastCapturedNs&&capturedNs>=receivedNs&&cueNs>capturedNs,"PRE_CUE_ORDER");
            ViewCaptureFault.Require(state.SampleNs>=receivedNs&&state.SampleNs>=planSeenNs&&state.SampleNs<=capturedNs,"STATE_SAMPLE_ORDER");
            ViewCaptureFault.Require((capturedNs-state.SampleNs)/1e6<=plan.MaxStateArrivalAgeMs,"STATE_ARRIVAL_AGE");
            lastCapturedNs=capturedNs;

            // File names carry only the capture index; the private pair is in
            // the manifest row, never in a path or renderer input.
            string prefix="c"+index.ToString("000");
            var row=new JObject{["capture_id"]=Guid.NewGuid().ToString("N"),["pose_id"]=pose.Id,["action"]=pair.Action,["target"]=pair.Target,
                ["head_pose"]=head.ToJson(),["reset_received_ns"]=receivedNs.ToString(),["captured_ns"]=capturedNs.ToString(),
                ["cue_requested_ns"]=cueNs.ToString(),["state_sample_ns"]=state.SampleNs.ToString()};
            row["reset_reply"]=output.WriteExclusive(prefix+"-reset.json",reply);
            row["image"]=output.WriteExclusive(prefix+"-image.png",png);
            row["state"]=state.State==null?JValue.CreateNull():output.WriteJson(prefix+"-state.json",state.State);
            row["audio"]=output.WriteJson(prefix+"-audio.json",audio);
            row["view"]=output.WriteJson(prefix+"-view.json",view.View);
            row["inventory"]=output.WriteJson(prefix+"-inventory.json",view.Inventory);
            row["applied"]=state.Applied==null?JValue.CreateNull():output.WriteJson(prefix+"-applied.json",state.Applied);
            output.AppendRow(row);
        }

        // Inspect, never grant: the exact accepted reset reply in test mode.
        static void CheckReply(byte[] raw,string requestId,string session)
        {
            var reply=CaptureJson.ParseStrictObject(raw,65536,"RESET_REPLY_INVALID");
            CaptureJson.Keys(reply,"RESET_REPLY_INVALID","version","kind","request_id","accepted","reason","mode","host_mono_ms","sim_time","reset_ok","duplicate","health");
            var health=reply["health"] as JObject;
            ViewCaptureFault.Require((string)reply["kind"]=="private_reply"&&(string)reply["request_id"]==requestId&&reply["accepted"].Type==JTokenType.Boolean&&(bool)reply["accepted"]&&
                reply["reset_ok"].Type==JTokenType.Boolean&&(bool)reply["reset_ok"]&&reply["duplicate"].Type==JTokenType.Boolean&&!(bool)reply["duplicate"]&&
                (string)reply["mode"]=="test"&&(string)reply["reason"]=="RESET_COMPLETE"&&health!=null&&(string)health["control_session_id"]==session&&
                health["fault"]?.Type==JTokenType.Null&&health["exposure_ready"]?.Type==JTokenType.Boolean&&(bool)health["exposure_ready"]&&
                health["paused"]?.Type==JTokenType.Boolean&&!(bool)health["paused"]&&health["stopped"]?.Type==JTokenType.Boolean&&!(bool)health["stopped"]&&
                health["demo_active"]?.Type==JTokenType.Boolean&&!(bool)health["demo_active"],"RESET_NOT_VERIFIED");
        }
    }

    static class CaptureFiles
    {
        // Same encodings the offline decoder accepts: 8-bit RGB/RGBA,
        // noninterlaced, exact plan dimensions. No resizing or re-encoding.
        internal static void RequirePng(byte[] png,int width,int height)
        {
            ViewCaptureFault.Require(png!=null&&png.Length>33&&png.Length<=80*1024*1024,"CAPTURE_PNG_INVALID");
            byte[] signature={0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A};
            for(int i=0;i<8;i++)ViewCaptureFault.Require(png[i]==signature[i],"CAPTURE_PNG_INVALID");
            ViewCaptureFault.Require(Encoding.ASCII.GetString(png,12,4)=="IHDR"&&BigEndian(png,8)==13,"CAPTURE_PNG_INVALID");
            ViewCaptureFault.Require(BigEndian(png,16)==width&&BigEndian(png,20)==height,"CAPTURE_DIMENSIONS");
            ViewCaptureFault.Require(png[24]==8&&(png[25]==2||png[25]==6)&&png[26]==0&&png[27]==0&&png[28]==0,"CAPTURE_PNG_ENCODING");
        }
        static long BigEndian(byte[] b,int at)=>((long)b[at]<<24)|((long)b[at+1]<<16)|((long)b[at+2]<<8)|b[at+3];
    }
}
