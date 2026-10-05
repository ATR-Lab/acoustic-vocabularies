using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using AcousticVocab.Foundation.Editor;

namespace AcousticVocab.Foundation.Tests
{
    public class StationConfigTests
    {
        string Schema => File.ReadAllText("Assets/ExperimentApp/Resources/StationConfigSchema.json");
        JObject Example => JObject.Parse(File.ReadAllText("Assets/Tests/EditMode/StationExample.json"));
        JObject Provisioned { get { var value = Example; value["provisioning_status"] = "provisioned"; return value; } }
        JObject Check(JObject value) => StationConfig.Validate(value.ToString(), Schema, (string)Example["protocol_version"]);

        [Test] public void ProvisionedEngineeringConfigValidates() => Assert.That(Check(Provisioned)["station_id"], Is.Not.Null);
        [Test] public void PublicExampleCannotStart() => Assert.Throws<ConfigurationFault>(() => Check(Example));
        [Test] public void EveryRequiredFieldIsRequired()
        {
            foreach (var name in Provisioned.Properties().Select(x => x.Name))
            { var value = Provisioned; value.Remove(name); Assert.Throws<ConfigurationFault>(() => Check(value), name); }
            foreach (var section in new[] { "audio", "observer_reference" })
                foreach (var name in ((JObject)Provisioned[section]).Properties().Select(x => x.Name))
                { var value = Provisioned; ((JObject)value[section]).Remove(name); Assert.Throws<ConfigurationFault>(() => Check(value), section + "." + name); }
        }
        [Test] public void UnknownFieldsAreRejectedRecursively()
        {
            foreach (string section in new[] { "", "audio", "observer_reference" })
            { var value = Provisioned; (section == "" ? value : (JObject)value[section])["unexpected"] = 1; Assert.Throws<ConfigurationFault>(() => Check(value)); }
        }
        [TestCase("{'x':1}")]
        [TestCase("{\"x\":1,}")]
        [TestCase("{\"x\":[1,]}")]
        [TestCase("{/*comment*/\"x\":1}")]
        [TestCase("{\"x\":NaN}")]
        [TestCase("{\"x\":Infinity}")]
        [TestCase("{\"x\":01}")]
        [TestCase("{\"x\":0x12}")]
        [TestCase("{\"x\":1,\"x\":2}")]
        [TestCase("{}{}")]
        public void NonJsonAndDuplicateKeysRejected(string json) => Assert.Throws<ConfigurationFault>(() => StationConfig.ParseStrict(json));
        [Test] public void WrongTypesAndEnumsRejected()
        {
            var value = Provisioned; value["refresh_hz"] = "72"; Assert.Throws<ConfigurationFault>(() => Check(value));
            value = Provisioned; value["input_method"] = "unknown"; Assert.Throws<ConfigurationFault>(() => Check(value));
            value = Provisioned; value["audio"]["buffer_samples"] = -1; Assert.Throws<ConfigurationFault>(() => Check(value));
        }
        [Test] public void ProtocolMismatchFailsClosed() => Assert.Throws<ConfigurationFault>(() => StationConfig.Validate(Provisioned.ToString(), Schema, "different-protocol"));
        [Test] public void QuaternionMustBeUnitLength()
        { var value = Provisioned; value["observer_reference"]["rotation_xyzw"] = new JArray(0, 0, 0, 0); Assert.Throws<ConfigurationFault>(() => Check(value)); }
        [Test] public void ReferenceCannotTiltTheWorld()
        {
            var value = Provisioned; var tilt = Quaternion.Euler(15, 30, 0);
            value["observer_reference"]["rotation_xyzw"] = new JArray(tilt.x, tilt.y, tilt.z, tilt.w);
            Assert.Throws<ConfigurationFault>(() => Check(value));
        }
        [Test] public void EndpointCannotContainCredentials()
        { var value = Provisioned; value["isaac_endpoint"] = "ws://user:secret@example.invalid"; Assert.Throws<ConfigurationFault>(() => Check(value)); }
        [Test] public void IdentifierAndEndpointCannotHideTrailingNewline()
        {
            var value = Provisioned; value["station_id"] = "station\n"; Assert.Throws<ConfigurationFault>(() => Check(value));
            value = Provisioned; value["isaac_endpoint"] = "ws://example.invalid\n"; Assert.Throws<ConfigurationFault>(() => Check(value));
        }
        [Test] public void NewUnimplementedSchemaConstraintFailsClosed()
        { var schema = JObject.Parse(Schema); schema["properties"]["station_id"]["not"] = new JObject(); Assert.Throws<ConfigurationFault>(() => StationConfig.Validate(Provisioned.ToString(), schema.ToString(), (string)Example["protocol_version"])); }
        [Test] public void EmbeddedSchemaMatchesCanonical() => FoundationBuild.VerifySchema();
        [Test] public void PublicExampleCopyMatchesCanonical() => Assert.That(File.ReadAllText("Assets/Tests/EditMode/StationExample.json"), Is.EqualTo(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/examples/station.example.json"))));
    }

    public class ObserverAndBuildTests
    {
        [Test] public void StartupWaitsForStableAcknowledgedOriginAfterDelayedEvent()
        {
            var gate=new StartupOriginGate();
            Assert.That(gate.Observe(true,10),Is.False);
            Assert.That(gate.Observe(true,10.019),Is.False);
            gate.Reset(); // Actual simulator origin notification trailed acknowledgement by ~19 ms.
            Assert.That(gate.Observe(true,10.020),Is.False);
            Assert.That(gate.Observe(true,10.269),Is.False);
            Assert.That(gate.Observe(true,10.271),Is.True);
        }
        [Test] public void InterruptedStartupMustEarnAnotherFullStableInterval()
        {
            var gate=new StartupOriginGate();gate.Observe(true,1);
            Assert.That(gate.Observe(false,1.24),Is.False); // Tracking, origin mode, focus or pause eligibility lost.
            Assert.That(gate.Observe(true,2),Is.False);Assert.That(gate.Observe(true,2.249),Is.False);
            Assert.That(gate.Observe(true,2.25),Is.True);gate.Reset();Assert.That(gate.Settled,Is.False);
        }
        [Test] public void OriginEventsAfterStartupStillLatchARecenterFault()
        {
            var root=new GameObject("SyntheticRecenterLifecycle");var cameraObject=new GameObject("Camera");var view=new GameObject("View");
            try
            {
                var bootstrap=root.AddComponent<FoundationBootstrap>();bootstrap.observerCamera=cameraObject.AddComponent<Camera>();bootstrap.presentationRoot=view;
                var flags=System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance;
                var gate=(StartupOriginGate)typeof(FoundationBootstrap).GetField("startupOrigin",flags).GetValue(bootstrap);
                gate.Observe(true,1);gate.Observe(true,1.3);
                typeof(FoundationBootstrap).GetMethod("OnTrackingOriginUpdated",flags).Invoke(bootstrap,new object[]{null});Assert.That(gate.Settled,Is.False);
                typeof(FoundationBootstrap).GetField("initialRestore",flags).SetValue(bootstrap,false);
                var reference=(ObserverReference)typeof(FoundationBootstrap).GetField("reference",flags).GetValue(bootstrap);reference.Restored();
                string reason=null;bootstrap.Faulted+=value=>reason=value;
                typeof(FoundationBootstrap).GetMethod("OnTrackingOriginUpdated",flags).Invoke(bootstrap,new object[]{null});
                Assert.That(reason,Is.EqualTo("tracking_origin_changed"));Assert.That(reference.RestorePending,Is.True);Assert.That(view.activeSelf,Is.False);
            }
            finally{UnityEngine.Object.DestroyImmediate(root);UnityEngine.Object.DestroyImmediate(cameraObject);UnityEngine.Object.DestroyImmediate(view);}
        }
        [Test] public void FocusLossLogsFaultAndRemainsNeutralAfterFocusReturns()
        {
            var root=new GameObject("SyntheticFocusTest"); var cameraObject=new GameObject("Camera"); var view=new GameObject("View");
            string directory=Path.Combine(Path.GetTempPath(),"focus-test-"+Guid.NewGuid().ToString("N"));
            FoundationLog testLog=null;
            try
            {
                var bootstrap=root.AddComponent<FoundationBootstrap>();bootstrap.observerCamera=cameraObject.AddComponent<Camera>();bootstrap.presentationRoot=view;
                var flags=System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance;
                testLog=new FoundationLog(directory,new JObject{["build_id"]="synthetic-focus-test",["commit_sha"]=new string('a',40),["protocol_version"]="engineering-test"},"synthetic-station");
                typeof(FoundationBootstrap).GetField("log",flags).SetValue(bootstrap,testLog);
                typeof(FoundationBootstrap).GetField("initialRestore",flags).SetValue(bootstrap,false);
                var reference=(ObserverReference)typeof(FoundationBootstrap).GetField("reference",flags).GetValue(bootstrap);reference.Restored();
                int faults=0;bootstrap.Faulted+=reason=>{Assert.That(reason,Is.EqualTo("application_focus_lost"));faults++;};
                typeof(FoundationBootstrap).GetMethod("OnApplicationFocus",flags).Invoke(bootstrap,new object[]{false});typeof(FoundationBootstrap).GetMethod("OnApplicationFocus",flags).Invoke(bootstrap,new object[]{false});
                Assert.That(faults,Is.EqualTo(1));Assert.That(view.activeSelf,Is.False);Assert.That(bootstrap.Ready,Is.False);Assert.That(reference.RestorePending,Is.True);
                typeof(FoundationBootstrap).GetMethod("OnApplicationFocus",flags).Invoke(bootstrap,new object[]{true});Assert.That(view.activeSelf,Is.False);Assert.That(bootstrap.Ready,Is.False);
                testLog.Dispose();
                var records=File.ReadAllLines(Directory.GetFiles(directory).Single()).Select(JObject.Parse).ToArray();
                Assert.That(records.Length,Is.EqualTo(2));Assert.That((string)records[1]["reason"],Is.EqualTo("application_focus_lost"));
                Assert.That((bool)records[1]["restore_pending"],Is.True);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(root);UnityEngine.Object.DestroyImmediate(cameraObject);UnityEngine.Object.DestroyImmediate(view);
                testLog?.Dispose();
                if(Directory.Exists(directory)){foreach(string file in Directory.GetFiles(directory))File.Delete(file);Directory.Delete(directory);}
            }
        }
        [Test] public void EarlyVisibilityLossCannotStartPresentation()
        {
            var root=new GameObject("SyntheticStartupVisibility");
            try
            {
                var bootstrap=root.AddComponent<FoundationBootstrap>(); var flags=System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance;
                typeof(FoundationBootstrap).GetMethod("OnApplicationFocus",flags).Invoke(bootstrap,new object[]{false});typeof(FoundationBootstrap).GetMethod("OnApplicationPause",flags).Invoke(bootstrap,new object[]{true});
                Assert.That((bool)typeof(FoundationBootstrap).GetField("applicationFocused",flags).GetValue(bootstrap),Is.False);
                Assert.That((bool)typeof(FoundationBootstrap).GetField("applicationPaused",flags).GetValue(bootstrap),Is.True);
                Assert.That(bootstrap.RestoreAtSafeBoundary("startup"),Is.False);
            }
            finally {UnityEngine.Object.DestroyImmediate(root);}
        }
        [Test] public void NeutralRecoveryRestoresConfiguredSceneBackground()
        {
            var root=new GameObject("SyntheticPresentationTest"); var cameraObject=new GameObject("Camera"); var view=new GameObject("View");
            try
            {
                var bootstrap=root.AddComponent<FoundationBootstrap>(); bootstrap.observerCamera=cameraObject.AddComponent<Camera>(); bootstrap.presentationRoot=view;
                var expected=new Color(.95f,.95f,.95f); var flags=System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance;
                typeof(FoundationBootstrap).GetField("presentationBackground",flags).SetValue(bootstrap,expected);
                typeof(FoundationBootstrap).GetMethod("Neutral",flags).Invoke(bootstrap,null);
                Assert.That(bootstrap.observerCamera.backgroundColor,Is.EqualTo(Color.black)); Assert.That(view.activeSelf,Is.False);
                // Exercise the presentation recovery step without impersonating HMD tracking
                // or claiming the guarded observer-calibration path has run on hardware.
                typeof(FoundationBootstrap).GetMethod("ShowPresentation",flags).Invoke(bootstrap,null);
                Assert.That(bootstrap.observerCamera.backgroundColor,Is.EqualTo(expected)); Assert.That(view.activeSelf,Is.True);
            }
            finally { UnityEngine.Object.DestroyImmediate(root); UnityEngine.Object.DestroyImmediate(cameraObject); UnityEngine.Object.DestroyImmediate(view); }
        }
        [Test] public void RestoreMapsCurrentTrackedHeadToReferenceWithoutChangingTrackingPose()
        {
            var target = new Pose(new Vector3(2, 1.3f, -1), Quaternion.Euler(0, 130, 0));
            var tracked = new Pose(new Vector3(.1f, .8f, -.2f), Quaternion.Euler(0, 40, 0));
            var origin = ObserverReference.ResolveOrigin(target, tracked);
            Assert.That(Vector3.Distance(origin.position + origin.rotation * tracked.position, target.position), Is.LessThan(.000001f));
            Assert.That(Quaternion.Angle(origin.rotation * tracked.rotation, target.rotation), Is.LessThan(.001f));
            var naturalMovedHead = tracked.position + Vector3.right * .1f;
            Assert.That(Vector3.Distance(origin.position + origin.rotation * naturalMovedHead, target.position), Is.EqualTo(.1f).Within(.000001f));
        }
        [Test] public void TiltedStartupPreservesWorldUpAndNaturalHeadPitchRoll()
        {
            var desired = new Pose(new Vector3(1, 1.3f, 2), Quaternion.Euler(0, 120, 0));
            var tracked = new Pose(new Vector3(.1f, .9f, .2f), Quaternion.Euler(35, 20, 12));
            var origin = ObserverReference.ResolveOrigin(desired, tracked);
            Assert.That(Vector3.Distance(origin.rotation * Vector3.up, Vector3.up), Is.LessThan(.000001f));
            Assert.That(Vector3.Distance(origin.position + origin.rotation * tracked.position, desired.position), Is.LessThan(.000001f));
            var beforeForward = tracked.rotation * Vector3.forward;
            var afterForward = origin.rotation * beforeForward;
            Assert.That(afterForward.y, Is.EqualTo(beforeForward.y).Within(.000001f));
            Assert.That((origin.rotation * tracked.rotation * Vector3.up).y, Is.EqualTo((tracked.rotation * Vector3.up).y).Within(.000001f));
            beforeForward.y = 0; afterForward.y = 0;
            Assert.That(Vector3.Angle(afterForward, desired.rotation * Vector3.forward), Is.LessThan(.01f));
        }
        [Test] public void VerticalHeadPoseCannotSupplyAStableHeading() => Assert.Throws<ConfigurationFault>(() => ObserverReference.ResolveOrigin(Pose.identity, new Pose(Vector3.zero, Quaternion.Euler(90, 0, 0))));
        [Test] public void DuplicateOriginEventsLatchOneFaultUntilSafeRestore()
        {
            var state = new ObserverReference(); state.Restored();
            Assert.That(state.MarkRecenter(), Is.True); Assert.That(state.MarkRecenter(), Is.False);
            Assert.That(state.RestorePending, Is.True); state.Restored(); Assert.That(state.MarkRecenter(), Is.True);
        }
        [Test] public void ParticipantSceneContainsOnlyApprovedFoundationComponents() => FoundationBuild.VerifyParticipantScene();
        [Test] public void EveryOperatorLogBeginsWithIdentityAndStation()
        {
            string directory = Path.Combine(Path.GetTempPath(), "foundation-test-" + Guid.NewGuid().ToString("N"));
            try
            {
                var identity = new JObject { ["commit_sha"] = new string('a', 40), ["build_id"] = "engineering-test", ["protocol_version"] = "engineering-test" };
                using (var log = new FoundationLog(directory, identity, "synthetic-station")) log.Write("fault", new JObject { ["reason"] = "synthetic" });
                var lines = File.ReadAllLines(Directory.GetFiles(directory).Single()); var header = JObject.Parse(lines[0]);
                Assert.That((string)header["event"], Is.EqualTo("header"));
                Assert.That((string)header["station_id"], Is.EqualTo("synthetic-station"));
                Assert.That(JToken.DeepEquals(header["build_identity"], identity), Is.True);
                Assert.That(lines.Length, Is.EqualTo(2));
            }
            finally { if (Directory.Exists(directory)) Directory.Delete(directory, true); }
        }
    }
}
