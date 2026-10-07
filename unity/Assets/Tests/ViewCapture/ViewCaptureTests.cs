using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using NUnit.Framework;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.ViewCapture.Tests
{
    public sealed class ViewCaptureTests
    {
        // Closed field sets copied from tools/view_leakage.py (verify, routing,
        // view and reply_shape). The offline verifier remains authoritative.
        static readonly string[] RowKeys="capture_id pose_id action target head_pose reset_received_ns captured_ns cue_requested_ns state_sample_ns reset_reply image state audio view".Split(' ');
        static readonly string[] AudioKeys="spatial_blend pan_stereo doppler_level spread volume pitch mute enabled loop output_mixer_group route_id source_count effects".Split(' ');
        static readonly string[] ViewKeys="panel_state_sha256 visible_text renderer_inventory_sha256 console_visible diagnostics_visible".Split(' ');
        static readonly string[] ReplyKeys="version kind request_id accepted reason mode host_mono_ms sim_time reset_ok duplicate health".Split(' ');

        static string Code(TestDelegate action){var error=Assert.Throws<ViewCaptureFault>(action);return error.Code;}
        static JObject Json(string path)=>JObject.Parse(File.ReadAllText(path,Encoding.ASCII));
        static List<JObject> Rows(string run)=>File.ReadAllLines(Path.Combine(run,ViewCaptureOutput.RowsFile)).Select(JObject.Parse).ToList();
        static void SameKeys(JObject value,IEnumerable<string> keys)=>CollectionAssert.AreEquivalent(keys,value.Properties().Select(p=>p.Name));

        [Test]public void ValidPlanMirrorsVerifierProfile()
        {
            using var f=new ViewCaptureFixture();var plan=ViewCapturePlan.Load(f.PlanPath,f.PlanPin);
            Assert.That(plan.Poses.Select(p=>p.Id),Is.EqualTo(new[]{"center","yaw_left","yaw_right","pitch_up","pitch_down"}));
            Assert.That(plan.CapturesRequired,Is.EqualTo(160));Assert.That(LegalPairs.All.Count,Is.EqualTo(32));
            Assert.That(LegalPairs.All.Select(p=>p.Action+"/"+p.Target).Distinct().Count(),Is.EqualTo(32));
        }

        [TestCase("one_pose","FIVE_POSES_REQUIRED")]
        [TestCase("repeated_pose","POSES_NOT_DISTINCT")]
        [TestCase("renamed_pose","FIVE_POSE_IDS_REQUIRED")]
        [TestCase("noise_1","ZERO_PIXEL_TOLERANCE_REQUIRED")]
        [TestCase("placeholder_build","UNCONFIGURED_PLAN_HASH")]
        [TestCase("placeholder_panel","UNCONFIGURED_PLAN_HASH")]
        [TestCase("participants","PLAN_SCOPE")]
        [TestCase("unknown_field","PLAN_CLOSED_FIELDS")]
        [TestCase("hidden_target_field","PLAN_CLOSED_FIELDS")]
        [TestCase("oversized_image","IMAGE_DIMENSION")]
        [TestCase("blank_screen","NONBLANK_SCREEN")]
        [TestCase("head_tolerance","FINITE_RANGE")]
        [TestCase("state_tolerance","FINITE_RANGE")]
        [TestCase("arrival_age","FINITE_RANGE")]
        [TestCase("non_unit_rotation","HEAD_POSE_INVALID")]
        [TestCase("scene_binding","SCENE_BINDING")]
        [TestCase("snapshot_pin","FILE_HASH")]
        [TestCase("snapshot_traversal","PLAN_RELATIVE_REFERENCE")]
        [TestCase("source_kind","CAPTURE_SOURCE")]
        public void PlanRefusals(string mutation,string code)
        {
            using var f=new ViewCaptureFixture(p=>
            {
                var poses=(JArray)p["poses"];
                switch(mutation)
                {
                    case "one_pose":while(poses.Count>1)poses.RemoveAt(1);break;
                    case "repeated_pose":poses[1]["head_pose"]=poses[0]["head_pose"].DeepClone();break;
                    case "renamed_pose":poses[0]["id"]="front";break;
                    case "noise_1":p["image"]["max_channel_delta"]=1;break;
                    case "placeholder_build":p["build_sha256"]=new string('0',64);break;
                    case "placeholder_panel":p["panel_state_sha256"]=new string('0',64);break;
                    case "participants":p["participants"]=true;break;
                    case "unknown_field":p["notes"]="extra";break;
                    case "hidden_target_field":poses[0]["target"]="tray_A";break;
                    case "oversized_image":p["image"]["width"]=4096;p["image"]["height"]=2048;break;
                    case "blank_screen":p["image"]["minimum_unique_rgb_colors"]=1;break;
                    case "head_tolerance":p["head_position_tolerance_m"]=.002;break;
                    case "state_tolerance":p["state_tolerances"]["joint_rad"]=1;break;
                    case "arrival_age":p["max_state_arrival_age_ms"]=251;break;
                    case "non_unit_rotation":poses[0]["head_pose"]["rotation_xyzw"]=new JArray(0,0,0,2);break;
                    case "scene_binding":p["scene_sha256"]=new string('f',64);break;
                    case "snapshot_pin":p["snapshot"]["sha256"]=new string('f',64);break;
                    case "snapshot_traversal":p["snapshot"]["path"]="../neutral.json";break;
                    case "source_kind":p["source_kind"]="recorded";break;
                }
            });
            Assert.That(Code(()=>ViewCapturePlan.Load(f.PlanPath,f.PlanPin)),Is.EqualTo(code));
        }

        [Test]public void PlanPinDuplicateKeysAndNonfiniteValuesRefused()
        {
            using var f=new ViewCaptureFixture();
            Assert.That(Code(()=>ViewCapturePlan.Load(f.PlanPath,new string('f',64))),Is.EqualTo("PLAN_FILE_HASH"));
            Assert.That(Code(()=>ViewCapturePlan.Load("relative/plan.json",f.PlanPin)),Is.EqualTo("PLAN_LOCAL_ABSOLUTE_PATH"));
            string text=File.ReadAllText(f.PlanPath);
            foreach(string bad in new[]{text.Replace("{\"build_sha256\"","{\"version\":1,\"build_sha256\""),text.Replace("\"max_state_arrival_age_ms\":250","\"max_state_arrival_age_ms\":NaN")})
            {
                Assert.That(bad,Is.Not.EqualTo(text));byte[] raw=Encoding.UTF8.GetBytes(bad);File.WriteAllBytes(f.PlanPath,raw);
                Assert.That(Code(()=>ViewCapturePlan.Load(f.PlanPath,CaptureJson.Hash(raw))),Is.EqualTo("PLAN_JSON_INVALID"));
            }
        }

        [Test]public void PanelDescriptorIsIndependentlyComputable()
        {
            // Python: json.dumps(value,sort_keys=True,separators=(',',':'))+'\n'
            string expected="{\"configuration_sha256\":\""+new string('e',64)+"\",\"kind\":\"view_capture_panel_state\",\"request_open\":false,\"version\":1}\n";
            Assert.That(Encoding.ASCII.GetString(CaptureJson.Canonical(SceneViewInventory.PanelStateDescriptor(new string('e',64),false))),Is.EqualTo(expected));
            Assert.That(ViewCaptureFixture.PanelPin,Is.EqualTo(CaptureJson.Hash(Encoding.ASCII.GetBytes(expected))));
        }

        [Test]public void OrdinaryBuildWithoutCompiledCapabilityCannotConstructDriver()
        {
            using var f=new ViewCaptureFixture();
            Assert.That(Code(()=>f.Driver(compiled:false)),Is.EqualTo("VIEW_CAPTURE_NOT_COMPILED"));
            Assert.That(Directory.EnumerateFileSystemEntries(Path.Combine(f.Root,"evidence")).Any(),Is.False);
        }

        [Test]public void SimulationAuthorityIsRequired()
        {
            using var f=new ViewCaptureFixture();var plan=ViewCapturePlan.Load(f.PlanPath,f.PlanPin);
            Assert.That(Code(()=>new ViewCaptureDriver(plan,null,f.Ports(),new ViewCaptureSettings(1,1,500,"view-capture-x"),f.Clock.NowNs)),Is.EqualTo("VIEW_CAPTURE_SIMULATION_AUTHORITY_REQUIRED"));
        }

        [TestCase("teaching_mode")][TestCase("mode_unacknowledged")][TestCase("control_failed")]
        public void CannotStartOutsideAcknowledgedProtectedTestMode(string condition)
        {
            using var f=new ViewCaptureFixture();
            if(condition=="teaching_mode")f.Control.RequiredMode="teaching";else if(condition=="mode_unacknowledged")f.Control.ModeAcknowledged=false;else f.Control.FaultCode="CONTROL_SOCKET_ERROR";
            var driver=f.Driver();
            Assert.That(Code(()=>driver.Start()),Is.EqualTo("VIEW_CAPTURE_PROTECTED_TEST_MODE_REQUIRED"));
            Assert.That(Directory.EnumerateFileSystemEntries(Path.Combine(f.Root,"evidence")).Any(),Is.False);
            Assert.That(f.Control.Requests,Is.Zero);
        }

        [Test]public void SurfaceMustMatchPoseControl()
        {
            using var f=new ViewCaptureFixture();f.Rig.CanSetPose=false;
            Assert.That(Code(()=>f.Driver().Start()),Is.EqualTo("VIEW_CAPTURE_SURFACE_MISMATCH"));
        }

        [Test]public void ExistingRunDirectoryIsNeverReused()
        {
            using var f=new ViewCaptureFixture();Directory.CreateDirectory(Path.Combine(f.Authority.OutputDirectory,"view-capture-synthetic"));
            Assert.That(Code(()=>f.Driver().Start()),Is.EqualTo("CAPTURE_OUTPUT_EXISTS"));
            Assert.That(Code(()=>f.Driver(run:"../view-capture-escape").Start()),Is.EqualTo("CAPTURE_RUN_NAME"));
        }

        [Test]public void CompleteSyntheticMatrixMatchesVerifierManifestShape()
        {
            using var f=new ViewCaptureFixture(export:true);var driver=f.Driver();driver.Start();ViewCaptureFixture.Drain(driver);
            Assert.That(driver.Fault,Is.Null);Assert.That(driver.Complete,Is.True);Assert.That(driver.CapturesCompleted,Is.EqualTo(160));
            string run=driver.RunDirectory;var status=Json(Path.Combine(run,ViewCaptureOutput.StatusFile));
            Assert.That((bool)status["complete"],Is.True);Assert.That((int)status["captures_completed"],Is.EqualTo(160));Assert.That((bool)status["participant_admission"],Is.False);
            Assert.That(CaptureJson.Hash(File.ReadAllBytes(Path.Combine(run,ViewCaptureOutput.RowsFile))),Is.EqualTo((string)status["rows"]["sha256"]));
            var header=Json(Path.Combine(run,ViewCaptureOutput.RunFile));
            Assert.That((string)header["plan_sha256"],Is.EqualTo(f.PlanPin));Assert.That((string)header["clock_id"],Does.Match("^[0-9a-f]{32}$"));
            var rows=Rows(run);Assert.That(rows.Count,Is.EqualTo(160));
            long last=long.Parse((string)header["plan_seen_ns"]);var resets=new HashSet<string>();var paths=new HashSet<string>();var keys=new HashSet<string>();
            for(int i=0;i<rows.Count;i++)
            {
                var row=rows[i];
                SameKeys(row,RowKeys.Concat(new[]{"inventory","applied"}));
                var pose=f.Plan["poses"][i/32];var pair=LegalPairs.All[i%32];
                Assert.That((string)row["pose_id"],Is.EqualTo((string)pose["id"]));Assert.That((string)row["action"],Is.EqualTo(pair.Action));Assert.That((string)row["target"],Is.EqualTo(pair.Target));
                Assert.That(keys.Add((string)row["pose_id"]+(string)row["action"]+(string)row["target"]),Is.True);
                foreach(string axis in new[]{"position_m","rotation_xyzw"})
                    Assert.That(row["head_pose"][axis].Select(x=>(double)x),Is.EqualTo(pose["head_pose"][axis].Select(x=>(double)x)).Within(1e-12));
                long reset=long.Parse((string)row["reset_received_ns"]),captured=long.Parse((string)row["captured_ns"]),cue=long.Parse((string)row["cue_requested_ns"]),sample=long.Parse((string)row["state_sample_ns"]);
                Assert.That(reset<=sample&&sample<=captured&&captured<cue&&captured>last,Is.True);last=captured;
                foreach(string key in new[]{"reset_reply","image","state","audio","view","inventory"})
                {
                    var reference=(JObject)row[key];SameKeys(reference,new[]{"path","sha256"});
                    Assert.That(paths.Add((string)reference["path"]),Is.True,"each capture has distinct files");
                    Assert.That((string)reference["path"],Does.StartWith("c"+(i+1).ToString("000")+"-"));
                    Assert.That((string)reference["path"],Does.Not.Contain("tray").And.Not.Contain("container"));
                    Assert.That(CaptureJson.Hash(File.ReadAllBytes(Path.Combine(run,(string)reference["path"]))),Is.EqualTo((string)reference["sha256"]));
                }
                Assert.That(row["applied"].Type,Is.EqualTo(JTokenType.Null));
                var reply=Json(Path.Combine(run,(string)row["reset_reply"]["path"]));SameKeys(reply,ReplyKeys);Assert.That(resets.Add((string)reply["request_id"]),Is.True);
                SameKeys(Json(Path.Combine(run,(string)row["audio"]["path"])),AudioKeys);SameKeys(Json(Path.Combine(run,(string)row["view"]["path"])),ViewKeys);
                Assert.That(JToken.DeepEquals(Json(Path.Combine(run,(string)row["state"]["path"])),f.NeutralState),Is.True);
                Assert.That(File.ReadAllBytes(Path.Combine(run,(string)row["image"]["path"])).Take(8),Is.EqualTo(new byte[]{0x89,0x50,0x4E,0x47,0x0D,0x0A,0x1A,0x0A}));
            }
            Assert.That(f.Boundary.Loaded,Is.EqualTo(Enumerable.Range(0,160).Select(i=>LegalPairs.All[i%32].Action+"/"+LegalPairs.All[i%32].Target)));
            Assert.That(f.Rig.Sets,Is.EqualTo(160));Assert.That(f.Control.Requests,Is.EqualTo(160));Assert.That(f.Boundary.Unloads,Is.EqualTo(160));
        }

        [Test]public void PlacedPoseReadbackMismatchRefusesWithoutRetry()
        {
            using var f=new ViewCaptureFixture();f.Rig.Offset=new[]{.002,0,0};var driver=f.Driver();driver.Start();ViewCaptureFixture.Drain(driver);
            Assert.That(driver.Fault,Is.EqualTo("HEAD_POSE_READBACK_MISMATCH"));Assert.That(driver.Complete,Is.False);
            Assert.That(f.Rig.Sets,Is.EqualTo(1));Assert.That(f.Control.Requests,Is.EqualTo(1));Assert.That(f.Boundary.Unloads,Is.EqualTo(1));
            var status=Json(Path.Combine(driver.RunDirectory,ViewCaptureOutput.StatusFile));
            Assert.That((bool)status["complete"],Is.False);Assert.That((string)status["fault"],Is.EqualTo("HEAD_POSE_READBACK_MISMATCH"));Assert.That((int)status["captures_completed"],Is.Zero);
            Assert.That(File.ReadAllBytes(Path.Combine(driver.RunDirectory,ViewCaptureOutput.RowsFile)),Is.Empty);
            Assert.That(Directory.GetFiles(driver.RunDirectory,"c001-*"),Is.Empty,"no capture artifact without a verified pose");
        }

        [Test]public void TrackedHeadThatNeverReachesPoseTimesOut()
        {
            using var f=new ViewCaptureFixture(p=>p["capture_surface"]="headset");f.Rig.CanSetPose=false;var driver=f.Driver();driver.Start();ViewCaptureFixture.Drain(driver);
            Assert.That(driver.Fault,Is.EqualTo("HEAD_POSE_NOT_REACHED"));Assert.That(f.Rig.Sets,Is.Zero);
        }

        [Test]public void MissingResetAcknowledgmentStopsAndRetainsPartialRun()
        {
            using var f=new ViewCaptureFixture();f.Control.NeverAcknowledgeFrom=3;var driver=f.Driver();driver.Start();ViewCaptureFixture.Drain(driver);
            Assert.That(driver.Fault,Is.EqualTo("RESET_ACK_TIMEOUT"));Assert.That(f.Control.Requests,Is.EqualTo(3),"no reset retry");
            var rows=Rows(driver.RunDirectory);Assert.That(rows.Count,Is.EqualTo(2));
            foreach(var row in rows)Assert.That(File.Exists(Path.Combine(driver.RunDirectory,(string)row["image"]["path"])),Is.True);
            var status=Json(Path.Combine(driver.RunDirectory,ViewCaptureOutput.StatusFile));
            Assert.That((bool)status["complete"],Is.False);Assert.That((int)status["captures_completed"],Is.EqualTo(2));Assert.That((string)status["fault"],Is.EqualTo("RESET_ACK_TIMEOUT"));
            Assert.That(Code(()=>new ViewCaptureOutput_ProbeExisting(driver.RunDirectory)),Is.EqualTo("CAPTURE_OUTPUT_EXISTS"));
        }

        [TestCase("panel","PANEL_STATE_MISMATCH")][TestCase("console","VISIBLE_OVERLAY")][TestCase("control","CONTROL_FAILED")]
        public void VisibleOrControlChangeRefusesCapture(string change,string code)
        {
            using var f=new ViewCaptureFixture();
            if(change=="panel")f.View.Panel=new string('9',64);else if(change=="console")f.View.Console=true;else f.Control.NeverAcknowledgeFrom=1;
            var driver=f.Driver();driver.Start();if(change=="control")f.Control.FaultCode="CONTROL_SOCKET_ERROR";ViewCaptureFixture.Drain(driver);
            Assert.That(driver.Fault,Is.EqualTo(code));Assert.That(Rows(driver.RunDirectory),Is.Empty);
        }

        // Re-creating the same run name must refuse; a failed run is retained.
        sealed class ViewCaptureOutput_ProbeExisting
        {
            internal ViewCaptureOutput_ProbeExisting(string run){ViewCaptureOutput.Create(Path.GetDirectoryName(run),Path.GetFileName(run));}
        }
    }
}
