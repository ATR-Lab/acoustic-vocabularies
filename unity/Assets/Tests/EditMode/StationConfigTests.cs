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
        [Test] public void EndpointCannotContainCredentials()
        { var value = Provisioned; value["isaac_endpoint"] = "ws://user:secret@example.invalid"; Assert.Throws<ConfigurationFault>(() => Check(value)); }
        [Test] public void NewUnimplementedSchemaConstraintFailsClosed()
        { var schema = JObject.Parse(Schema); schema["properties"]["station_id"]["not"] = new JObject(); Assert.Throws<ConfigurationFault>(() => StationConfig.Validate(Provisioned.ToString(), schema.ToString(), (string)Example["protocol_version"])); }
        [Test] public void EmbeddedSchemaMatchesCanonical() => FoundationBuild.VerifySchema();
        [Test] public void PublicExampleCopyMatchesCanonical() => Assert.That(File.ReadAllText("Assets/Tests/EditMode/StationExample.json"), Is.EqualTo(File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot, "apparatus/examples/station.example.json"))));
    }

    public class ObserverAndBuildTests
    {
        [Test] public void RestoreMapsCurrentTrackedHeadToReferenceWithoutChangingTrackingPose()
        {
            var target = new Pose(new Vector3(2, 1.3f, -1), Quaternion.Euler(0, 130, 0));
            var tracked = new Pose(new Vector3(.1f, .8f, -.2f), Quaternion.Euler(5, 40, -2));
            var origin = ObserverReference.ResolveOrigin(target, tracked);
            Assert.That(Vector3.Distance(origin.position + origin.rotation * tracked.position, target.position), Is.LessThan(.000001f));
            Assert.That(Quaternion.Angle(origin.rotation * tracked.rotation, target.rotation), Is.LessThan(.001f));
            var naturalMovedHead = tracked.position + Vector3.right * .1f;
            Assert.That(Vector3.Distance(origin.position + origin.rotation * naturalMovedHead, target.position), Is.EqualTo(.1f).Within(.000001f));
        }
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
