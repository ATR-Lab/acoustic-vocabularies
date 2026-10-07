using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.ViewCapture.PlayModeTests
{
    // Real Unity rig, offscreen camera grabber, renderer inventory and audio
    // routing ports in a conspicuously synthetic runtime scene. Control,
    // state and trial boundary are synthetic: this is not a station capture.
    public sealed class ViewCapturePlayTests
    {
        sealed class SyntheticControl:IViewCaptureControl
        {
            readonly IViewCaptureClock clock;readonly Dictionary<string,long> received=new Dictionary<string,long>();int next;
            public string RequiredMode=>"test";public bool ModeAcknowledged=>true;public string ControlSessionId=>new string('a',32);public string FaultCode=>null;
            public SyntheticControl(IViewCaptureClock clock){this.clock=clock;}
            public string RequestReset()=>(++next).ToString("x32");
            public bool TryResetAcknowledged(string id,out byte[] raw,out long ns)
            {
                if(!received.TryGetValue(id,out ns)){ns=clock.NowNs;received[id]=ns;}
                int index=Convert.ToInt32(id,16);
                raw=CaptureJson.Canonical(new JObject{["version"]=1,["kind"]="private_reply",["request_id"]=id,["accepted"]=true,["reason"]="RESET_COMPLETE",["mode"]="test",
                    ["host_mono_ms"]=index,["sim_time"]=0,["reset_ok"]=true,["duplicate"]=false,["health"]=new JObject{["control_session_id"]=ControlSessionId,["mode"]="test",
                    ["paused"]=false,["stopped"]=false,["fault"]=JValue.CreateNull(),["demo_active"]=false,["publisher_ready"]=true,["exposure_ready"]=true,["public_stream_recovered"]=false,
                    ["neutral_verification_age_ms"]=0,["publisher_age_ms"]=0,["health_sample_host_mono_ms"]=index}});
                return true;
            }
        }
        sealed class SyntheticState:IStateReadback
        {
            readonly IViewCaptureClock clock;readonly JObject state;
            public SyntheticState(IViewCaptureClock clock,JObject state){this.clock=clock;this.state=state;}
            public string Origin=>"synthetic_repository_neutral_state";
            public bool TryRead(long notBefore,out StateReading reading){long now=clock.NowNs;reading=new StateReading((JObject)state.DeepClone(),now,null);return now>=notBefore;}
        }
        // Batch runs use -nographics; the real offscreen grabber runs in the
        // GraphicsRequired category and whenever a graphics device exists.
        sealed class CpuGrabber:IFrameGrabber
        {
            public string Method=>"synthetic_cpu_png_no_graphics_device";
            public byte[] CapturePng(int width,int height)
            {
                var texture=new Texture2D(width,height,TextureFormat.RGB24,false,false);
                try{texture.SetPixels32(Enumerable.Range(0,width*height).Select(i=>new Color32((byte)(i%7*30),(byte)(i%5*40),(byte)(i%3*60),255)).ToArray());return texture.EncodeToPNG();}
                finally{UnityEngine.Object.Destroy(texture);}
            }
        }
        // Moves the camera after placement, as an unexpected tracker would.
        sealed class Intruder:MonoBehaviour{void LateUpdate()=>transform.position+=new Vector3(.01f,0,0);}

        static readonly string Panel=SceneViewInventory.PanelStateSha256(new string('e',64),false);
        string root;readonly List<GameObject> scene=new List<GameObject>();Camera camera;JObject neutralState;

        [SetUp]public void Setup()
        {
            root=Path.Combine(Path.GetTempPath(),".local","simulation-test-viewcapture-play-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(Path.Combine(root,"evidence"));
            var cameraObject=new GameObject("Synthetic participant camera");scene.Add(cameraObject);camera=cameraObject.AddComponent<Camera>();
            camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=new Color(.1f,.1f,.1f);
            cameraObject.AddComponent<UnityEngine.InputSystem.XR.TrackedPoseDriver>();
            var shader=Shader.Find("Unlit/Color");
            for(int i=0;i<4;i++)
            {
                var cube=GameObject.CreatePrimitive(PrimitiveType.Cube);cube.name="Synthetic fixed object "+i;scene.Add(cube);
                cube.transform.position=new Vector3(-1.5f+i,1.2f,0);
                if(shader!=null){var material=new Material(shader);material.color=Color.HSVToRGB(i/4f,.8f,.9f);cube.GetComponent<Renderer>().sharedMaterial=material;}
            }
            var audio=new GameObject("Synthetic inert audio owner",typeof(AudioSource));scene.Add(audio);var source=audio.GetComponent<AudioSource>();
            source.playOnAwake=false;source.spatialBlend=0;source.panStereo=0;source.dopplerLevel=0;source.spread=0;source.volume=.1f;
            string repository=Directory.GetParent(Application.dataPath).Parent.FullName;
            byte[] neutral=File.ReadAllBytes(Path.Combine(repository,"isaac","snapshots","neutral_v1.json"));File.WriteAllBytes(Path.Combine(root,"neutral.json"),neutral);
            neutralState=(JObject)JObject.Parse(Encoding.UTF8.GetString(neutral))["state"];
        }
        [TearDown]public void Cleanup(){foreach(var item in scene)UnityEngine.Object.Destroy(item);scene.Clear();if(Directory.Exists(root))Directory.Delete(root,true);}

        (ViewCapturePlan Plan,SimulationTestAuthority Authority) Load(string surface)
        {
            byte[] neutral=File.ReadAllBytes(Path.Combine(root,"neutral.json"));string scene=(string)JObject.Parse(Encoding.UTF8.GetString(neutral))["scene_sha256"];
            JObject Pose(string id,Quaternion q)=>new JObject{["id"]=id,["head_pose"]=new JObject{["position_m"]=new JArray(0.0,1.2,-3.0),["rotation_xyzw"]=new JArray((double)q.x,(double)q.y,(double)q.z,(double)q.w)}};
            var plan=new JObject{["version"]=1,["scope"]="engineering_provisional",["participants"]=false,["source_kind"]="snapshot",["capture_surface"]=surface,
                ["station_id"]="DEMO-station",["build_sha256"]=new string('b',64),["scene_sha256"]=scene,["snapshot"]=new JObject{["path"]="neutral.json",["sha256"]=CaptureJson.Hash(neutral)},
                ["image"]=new JObject{["width"]=32,["height"]=16,["max_channel_delta"]=0,["minimum_unique_rgb_colors"]=2},
                ["poses"]=new JArray(Pose("center",Quaternion.identity),Pose("yaw_left",Quaternion.Euler(0,-15,0)),Pose("yaw_right",Quaternion.Euler(0,15,0)),Pose("pitch_up",Quaternion.Euler(-10,0,0)),Pose("pitch_down",Quaternion.Euler(10,0,0))),
                ["head_position_tolerance_m"]=.001,["head_orientation_tolerance_rad"]=Math.PI/180*.5,
                ["state_tolerances"]=new JObject{["joint_rad"]=Math.PI/180*.5,["position_m"]=.001,["orientation_rad"]=Math.PI/180*.5,["linear_velocity_m_s"]=1e-5,["angular_velocity_rad_s"]=1e-5,["environment_absolute"]=1e-7},
                ["panel_state_sha256"]=Panel,["max_state_arrival_age_ms"]=250};
            byte[] raw=CaptureJson.Canonical(plan);File.WriteAllBytes(Path.Combine(root,"plan.json"),raw);
            var capability=new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["fixture_set_sha256"]=new string('a',64),["package_sha256"]=new string('b',64),["schedule_sha256"]=new string('c',64),
                ["build_id"]="view-capture-play",["protocol_version"]="simulation-test-v1",["output_directory"]=Path.Combine(root,"evidence"),["audio_gain"]=.05,["participant_admission"]=false,["acoustic_qualification"]=false};
            byte[] cap=Encoding.UTF8.GetBytes(capability.ToString());File.WriteAllBytes(Path.Combine(root,"capability.json"),cap);
            return (ViewCapturePlan.Load(Path.Combine(root,"plan.json"),CaptureJson.Hash(raw)),SimulationTestAuthority.Load(Path.Combine(root,"capability.json"),CaptureJson.Hash(cap),"view-capture-play","simulation-test-v1"));
        }

        static bool Graphics=>SystemInfo.graphicsDeviceType!=UnityEngine.Rendering.GraphicsDeviceType.Null;
        ViewCaptureDriver Driver(string surface,int timeoutMs=2000)
        {
            var (plan,authority)=Load(surface);var clock=new StopwatchCaptureClock();long seen=clock.NowNs;
            var ports=new ViewCapturePorts{Clock=clock,Control=new SyntheticControl(clock),Rig=new TransformHeadRig(camera,surface=="desktop_engineering"),Grabber=Graphics?new CameraFrameGrabber(camera):new CpuGrabber(),
                State=new SyntheticState(clock,neutralState),Audio=new AudioSourceRouting(),View=new SceneViewInventory(camera,()=>(new string('e',64),false)),Boundary=new DriverOnlyTrialBoundary(clock)};
            return new ViewCaptureDriver(plan,authority,ports,new ViewCaptureSettings(1,1,timeoutMs,"view-capture-play"),seen);
        }

        [UnityTest]public IEnumerator RealRigGrabberAndInventoryCaptureCompleteSyntheticMatrix()
        {
            var driver=Driver("desktop_engineering");driver.Start();
            Assert.That(camera.GetComponent<UnityEngine.InputSystem.XR.TrackedPoseDriver>().enabled,Is.False,"placed desktop camera is not tracked");
            yield return driver.Run();
            Assert.That(driver.Fault,Is.Null);Assert.That(driver.CapturesCompleted,Is.EqualTo(160));
            var rows=File.ReadAllLines(Path.Combine(driver.RunDirectory,ViewCaptureOutput.RowsFile)).Select(JObject.Parse).ToList();
            Assert.That(rows.Count,Is.EqualTo(160));
            var header=JObject.Parse(File.ReadAllText(Path.Combine(driver.RunDirectory,ViewCaptureOutput.RunFile)));
            Assert.That((string)header["trial_boundary"],Is.EqualTo("driver_only_no_session_engine_no_cue_audio"));
            Assert.That((string)header["capture_method"],Does.Contain(Graphics?"not_compositor_output":"no_graphics_device"));
            foreach(var row in rows)
            {
                byte[] png=File.ReadAllBytes(Path.Combine(driver.RunDirectory,(string)row["image"]["path"]));
                Assert.That(png[16]<<24|png[17]<<16|png[18]<<8|png[19],Is.EqualTo(32));Assert.That(png[20]<<24|png[21]<<16|png[22]<<8|png[23],Is.EqualTo(16));
                Assert.That(png[24],Is.EqualTo(8));Assert.That(png[25],Is.EqualTo(2),"RGB24, no alpha channel");
                var audio=JObject.Parse(File.ReadAllText(Path.Combine(driver.RunDirectory,(string)row["audio"]["path"])));
                Assert.That((int)audio["source_count"],Is.EqualTo(1));Assert.That((double)audio["spatial_blend"],Is.Zero);Assert.That((double)audio["pan_stereo"],Is.Zero);
                var view=JObject.Parse(File.ReadAllText(Path.Combine(driver.RunDirectory,(string)row["view"]["path"])));
                Assert.That((string)view["panel_state_sha256"],Is.EqualTo(Panel));Assert.That((bool)view["console_visible"],Is.False);Assert.That((bool)view["diagnostics_visible"],Is.False);
            }
            // The inventory changes with the view, never with the hidden pair.
            foreach(var group in rows.GroupBy(r=>(string)r["pose_id"]))
                Assert.That(group.Select(r=>(string)JObject.Parse(File.ReadAllText(Path.Combine(driver.RunDirectory,(string)r["view"]["path"])))["renderer_inventory_sha256"]).Distinct().Count(),Is.EqualTo(1));
            var center=JObject.Parse(File.ReadAllText(Path.Combine(driver.RunDirectory,(string)rows[0]["inventory"]["path"])));
            Assert.That(center["renderers"].Select(r=>(string)r["path"]),Has.Some.StartsWith("Synthetic fixed object"));
        }

        // Exact pixel equality is meaningful only when a GPU actually renders.
        [UnityTest,Category("GraphicsRequired")]public IEnumerator RenderedFramesAreNonblankAndPairIndependentPerPose()
        {
            Assert.That(Graphics,Is.True,"run with -GraphicsTests");
            var driver=Driver("desktop_engineering");driver.Start();yield return driver.Run();
            Assert.That(driver.Fault,Is.Null);
            var rows=File.ReadAllLines(Path.Combine(driver.RunDirectory,ViewCaptureOutput.RowsFile)).Select(JObject.Parse).ToList();
            foreach(var group in rows.GroupBy(r=>(string)r["pose_id"]))
            {
                var hashes=group.Select(r=>(string)r["image"]["sha256"]).Distinct().ToList();
                Assert.That(hashes.Count,Is.EqualTo(1),"synthetic static scene renders identically for every pair");
                var texture=new Texture2D(2,2);Assert.That(texture.LoadImage(File.ReadAllBytes(Path.Combine(driver.RunDirectory,(string)group.First()["image"]["path"]))),Is.True);
                Assert.That(texture.GetPixels32().Select(c=>(c.r,c.g,c.b)).Distinct().Count(),Is.GreaterThanOrEqualTo(2));UnityEngine.Object.Destroy(texture);
            }
            Assert.That(rows.Select(r=>(string)r["image"]["sha256"]).Distinct().Count(),Is.GreaterThan(1),"different poses render different frames");
        }

        [UnityTest]public IEnumerator PlacedCameraMovedByAnotherComponentIsRefused()
        {
            camera.gameObject.AddComponent<Intruder>();var driver=Driver("desktop_engineering");driver.Start();yield return driver.Run();
            Assert.That(driver.Fault,Is.EqualTo("HEAD_POSE_READBACK_MISMATCH"));Assert.That(driver.CapturesCompleted,Is.Zero);
            var status=JObject.Parse(File.ReadAllText(Path.Combine(driver.RunDirectory,ViewCaptureOutput.StatusFile)));
            Assert.That((bool)status["complete"],Is.False);
        }

        [UnityTest]public IEnumerator TrackedHeadAwayFromPlanPoseIsRefused()
        {
            camera.transform.SetPositionAndRotation(new Vector3(0,1.2f,-2.9f),Quaternion.identity);
            var driver=Driver("headset",200);driver.Start();
            Assert.That(camera.GetComponent<UnityEngine.InputSystem.XR.TrackedPoseDriver>().enabled,Is.True,"tracked head is never placed by the driver");
            yield return driver.Run();
            Assert.That(driver.Fault,Is.EqualTo("HEAD_POSE_NOT_REACHED"));Assert.That(camera.transform.position.z,Is.EqualTo(-2.9f).Within(1e-4f));
        }
    }
}
