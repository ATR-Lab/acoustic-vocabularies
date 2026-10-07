using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.ViewCapture.Tests
{
    // Conspicuously synthetic ports and files. They exercise the driver and
    // its manifest shape only; nothing here is a station capture.
    sealed class FakeClock:IViewCaptureClock
    {
        long now=1_000_000_000;public long Step=1_000_000;
        public long NowNs{get{now+=Step;return now;}}
    }
    sealed class FakeControl:IViewCaptureControl
    {
        readonly IViewCaptureClock clock;readonly Dictionary<string,long> acknowledged=new Dictionary<string,long>();int next;
        public string RequiredMode{get;set;}="test";public bool ModeAcknowledged{get;set;}=true;public string ControlSessionId{get;set;}=new string('a',32);public string FaultCode{get;set;}
        public int Requests;public int NeverAcknowledgeFrom=int.MaxValue;public int AckAfterPolls=2;readonly Dictionary<string,int> polls=new Dictionary<string,int>();
        public FakeControl(IViewCaptureClock clock){this.clock=clock;}
        public string RequestReset(){Requests++;return(++next).ToString("x32");}
        public bool TryResetAcknowledged(string id,out byte[] raw,out long receivedNs)
        {
            raw=null;receivedNs=0;int index=Convert.ToInt32(id,16);
            if(index>=NeverAcknowledgeFrom)return false;
            polls[id]=polls.TryGetValue(id,out int n)?n+1:1;if(polls[id]<AckAfterPolls)return false;
            if(!acknowledged.TryGetValue(id,out receivedNs)){receivedNs=clock.NowNs;acknowledged[id]=receivedNs;}
            raw=ViewCaptureFixture.Reply(id,ControlSessionId,index);return true;
        }
    }
    sealed class FakeRig:IHeadPoseRig
    {
        public bool CanSetPose{get;set;}=true;public HeadPose? Current;public double[] Offset={0,0,0};public int Sets;
        public void SetPose(HeadPose pose){Sets++;Current=pose;}
        public HeadPose ReadPose()
        {
            var p=Current??new HeadPose(new double[]{9,9,9},new double[]{0,0,0,1});
            return new HeadPose(p.Position.Select((x,i)=>x+Offset[i]).ToArray(),p.Rotation);
        }
    }
    sealed class FakeGrabber:IFrameGrabber
    {
        public string Method=>"synthetic_texture2d_png_not_a_render";
        public byte[] CapturePng(int width,int height)
        {
            var texture=new Texture2D(width,height,TextureFormat.RGB24,false,false);
            try
            {
                var pixels=new Color32[width*height];
                for(int i=0;i<pixels.Length;i++)pixels[i]=new Color32((byte)(i*37%256),(byte)(i*91%256),(byte)(i*13%256),255);
                texture.SetPixels32(pixels);return texture.EncodeToPNG();
            }
            finally{UnityEngine.Object.DestroyImmediate(texture);}
        }
    }
    sealed class FakeState:IStateReadback
    {
        readonly IViewCaptureClock clock;readonly JObject state;
        public string Origin=>"synthetic_repository_neutral_state";
        public FakeState(IViewCaptureClock clock,JObject state){this.clock=clock;this.state=state;}
        public bool TryRead(long notBefore,out StateReading reading){long now=clock.NowNs;reading=new StateReading((JObject)state.DeepClone(),now,null);return now>=notBefore;}
    }
    sealed class FakeAudio:IAudioRouting
    {
        public JObject Read()=>new JObject{["spatial_blend"]=0,["pan_stereo"]=0,["doppler_level"]=0,["spread"]=0,["volume"]=.25,["pitch"]=1,["mute"]=false,["enabled"]=true,
            ["loop"]=false,["output_mixer_group"]="",["route_id"]="DEMO-route",["source_count"]=1,["effects"]=new JArray()};
    }
    sealed class FakeView:IViewInventory
    {
        public string Panel=ViewCaptureFixture.PanelPin;public bool Console;
        public ViewReading Read()=>new ViewReading(new JObject{["panel_state_sha256"]=Panel,["visible_text"]=new JArray("DEMO fixed panel"),
            ["renderer_inventory_sha256"]=new string('d',64),["console_visible"]=Console,["diagnostics_visible"]=false},new JObject{["version"]=1,["kind"]="synthetic"});
    }
    sealed class FakeBoundary:IProtectedTrialBoundary
    {
        readonly IViewCaptureClock clock;public readonly List<string> Loaded=new List<string>();public int Unloads;
        public FakeBoundary(IViewCaptureClock clock){this.clock=clock;}
        public string Kind=>"synthetic_boundary";
        public void Load(LegalPair pair)=>Loaded.Add(pair.Action+"/"+pair.Target);
        public long RequestCue()=>clock.NowNs;
        public void Unload()=>Unloads++;
    }

    sealed class ViewCaptureFixture:IDisposable
    {
        public static readonly string PanelPin=SceneViewInventory.PanelStateSha256(new string('e',64),false);
        public readonly string Root,PlanPath,PlanPin;public readonly JObject Plan;public readonly SimulationTestAuthority Authority;public readonly JObject NeutralState;
        public readonly FakeClock Clock=new FakeClock();public FakeControl Control;public FakeRig Rig=new FakeRig();public FakeView View=new FakeView();public FakeBoundary Boundary;
        readonly bool keep;

        public static string RepositoryRoot=>Directory.GetParent(Application.dataPath).Parent.FullName;
        public static byte[] Reply(string id,string session,int index)=>CaptureJson.Canonical(new JObject{["version"]=1,["kind"]="private_reply",["request_id"]=id,["accepted"]=true,
            ["reason"]="RESET_COMPLETE",["mode"]="test",["host_mono_ms"]=index,["sim_time"]=0,["reset_ok"]=true,["duplicate"]=false,["health"]=new JObject{
            ["control_session_id"]=session,["mode"]="test",["paused"]=false,["stopped"]=false,["fault"]=JValue.CreateNull(),["demo_active"]=false,["publisher_ready"]=true,
            ["exposure_ready"]=true,["public_stream_recovered"]=false,["neutral_verification_age_ms"]=0,["publisher_age_ms"]=0,["health_sample_host_mono_ms"]=index}});

        public static JObject PlanJson(string sceneSha256,string snapshotSha256)
        {
            JObject Pose(string id,double[] q)=>new JObject{["id"]=id,["head_pose"]=new JObject{["position_m"]=new JArray(0.0,1.2,-2.0),["rotation_xyzw"]=new JArray(q.Cast<object>().ToArray())}};
            double Half(double degrees)=>Math.PI/180*degrees/2;
            return new JObject{["version"]=1,["scope"]="engineering_provisional",["participants"]=false,["source_kind"]="snapshot",["capture_surface"]="desktop_engineering",
                ["station_id"]="DEMO-station",["build_sha256"]=new string('b',64),["scene_sha256"]=sceneSha256,["snapshot"]=new JObject{["path"]="neutral.json",["sha256"]=snapshotSha256},
                ["image"]=new JObject{["width"]=8,["height"]=4,["max_channel_delta"]=0,["minimum_unique_rgb_colors"]=2},
                ["poses"]=new JArray(Pose("center",new[]{0.0,0,0,1}),Pose("yaw_left",new[]{0,-Math.Sin(Half(15)),0,Math.Cos(Half(15))}),Pose("yaw_right",new[]{0,Math.Sin(Half(15)),0,Math.Cos(Half(15))}),
                    Pose("pitch_up",new[]{-Math.Sin(Half(10)),0,0,Math.Cos(Half(10))}),Pose("pitch_down",new[]{Math.Sin(Half(10)),0,0,Math.Cos(Half(10))})),
                ["head_position_tolerance_m"]=.001,["head_orientation_tolerance_rad"]=Math.PI/180*.5,
                ["state_tolerances"]=new JObject{["joint_rad"]=Math.PI/180*.5,["position_m"]=.001,["orientation_rad"]=Math.PI/180*.5,["linear_velocity_m_s"]=1e-5,["angular_velocity_rad_s"]=1e-5,["environment_absolute"]=1e-7},
                ["panel_state_sha256"]=PanelPin,["max_state_arrival_age_ms"]=250};
        }

        // AV_VIEW_CAPTURE_EXPORT keeps one synthetic run for the offline
        // Python assembler/verifier cross-check; otherwise everything is temporary.
        public ViewCaptureFixture(Action<JObject> editPlan=null,bool export=false)
        {
            string exported=export?Environment.GetEnvironmentVariable("AV_VIEW_CAPTURE_EXPORT"):null;keep=exported!=null;
            Root=keep?Path.GetFullPath(exported):Path.Combine(Path.GetTempPath(),".local","simulation-test-viewcapture-"+Guid.NewGuid().ToString("N"));
            if(keep&&Directory.Exists(Root))throw new IOException("Export directory must be fresh");
            Directory.CreateDirectory(Path.Combine(Root,"evidence"));
            byte[] neutral=File.ReadAllBytes(Path.Combine(RepositoryRoot,"isaac","snapshots","neutral_v1.json"));
            File.WriteAllBytes(Path.Combine(Root,"neutral.json"),neutral);var snapshot=JObject.Parse(Encoding.UTF8.GetString(neutral));NeutralState=(JObject)snapshot["state"];
            Plan=PlanJson((string)snapshot["scene_sha256"],CaptureJson.Hash(neutral));editPlan?.Invoke(Plan);
            byte[] raw=CaptureJson.Canonical(Plan);PlanPath=Path.Combine(Root,"plan.json");File.WriteAllBytes(PlanPath,raw);PlanPin=CaptureJson.Hash(raw);
            var capability=new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["fixture_set_sha256"]=new string('a',64),["package_sha256"]=new string('b',64),["schedule_sha256"]=new string('c',64),
                ["build_id"]="view-capture-test",["protocol_version"]="simulation-test-v1",["output_directory"]=Path.Combine(Root,"evidence"),["audio_gain"]=.05,["participant_admission"]=false,["acoustic_qualification"]=false};
            byte[] cap=Encoding.UTF8.GetBytes(capability.ToString());string capPath=Path.Combine(Root,"capability.json");File.WriteAllBytes(capPath,cap);
            Authority=SimulationTestAuthority.Load(capPath,CaptureJson.Hash(cap),"view-capture-test","simulation-test-v1");
            Control=new FakeControl(Clock);Boundary=new FakeBoundary(Clock);
        }

        public ViewCapturePorts Ports(IStateReadback state=null)=>new ViewCapturePorts{Clock=Clock,Control=Control,Rig=Rig,Grabber=new FakeGrabber(),
            State=state??new FakeState(Clock,NeutralState),Audio=new FakeAudio(),View=View,Boundary=Boundary};

        public ViewCaptureDriver Driver(bool compiled=true,SimulationTestAuthority authority=null,string run="view-capture-synthetic",IStateReadback state=null)=>
            new ViewCaptureDriver(ViewCapturePlan.Load(PlanPath,PlanPin),authority??Authority,Ports(state),new ViewCaptureSettings(1,1,500,run),Clock.NowNs,compiled);

        public static void Drain(ViewCaptureDriver driver)
        {var run=driver.Run();int guard=0;while(run.MoveNext())if(++guard>200000)throw new InvalidOperationException("driver did not finish");}

        public void Dispose(){if(!keep&&Directory.Exists(Root))Directory.Delete(Root,true);}
    }
}
