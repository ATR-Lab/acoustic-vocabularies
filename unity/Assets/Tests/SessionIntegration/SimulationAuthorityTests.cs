using System;
using System.IO;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.StudyAudio;
using AcousticVocab.Assessment;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration.Tests
{
    public sealed class SimulationAuthorityTests
    {
        string root,path;JObject config;
        [SetUp]public void Setup()
        {
            root=Path.Combine(Path.GetTempPath(),".local","simulation-test-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);path=Path.Combine(root,"capability.json");
            config=new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["fixture_set_sha256"]=new string('a',64),["package_sha256"]=new string('b',64),["schedule_sha256"]=new string('c',64),["build_id"]="mock-build",["protocol_version"]="simulation-test-v1",["output_directory"]=Path.Combine(root,"evidence"),["audio_gain"]=.05,["participant_admission"]=false,["acoustic_qualification"]=false};
        }
        [TearDown]public void Cleanup(){Directory.Delete(root,true);}
        SimulationTestAuthority Load()
        {byte[] raw=Encoding.UTF8.GetBytes(config.ToString());File.WriteAllBytes(path,raw);return SimulationTestAuthority.Load(path,PcmWave.Hash(raw),"mock-build","simulation-test-v1");}
        [Test]public void SoftwareTimingNeverInventsAcousticOnsetOrQualification()
        {
            var authority=Load();authority.Bind(new string('b',64),new string('c',64),true,true,(string)config["output_directory"]);
            var route=AudioRouteCalibration.ForSimulation(authority);Assert.That(route.IsQualified,Is.False);Assert.That(authority.ParticipantAdmission,Is.False);
            var mapping=new DspClockMapping();mapping.Observe(10,20,10.002,.01);var timing=mapping.Schedule(10.003,11,route);
            Assert.That(timing.SimulationOnly,Is.True);Assert.That(timing.CalibrationOnly,Is.False);Assert.That(timing.OnsetEstimateMonoSeconds,Is.Null);Assert.That(timing.OnsetUncertaintyMs,Is.Null);Assert.That(timing.RouteOffsetMs,Is.Null);
            Assert.That(timing.SoftwareOutputEstimateMonoSeconds,Is.EqualTo(11));Assert.That(timing.SoftwareOutputUncertaintyMs,Is.EqualTo(11).Within(.0001));
        }
        [TestCase("participant_admission")][TestCase("acoustic_qualification")]
        public void CannotAssertRealQualification(string key){config[key]=true;Assert.Throws<InvalidDataException>(()=>Load());}
        [TestCase("scope","DEMO_ENGINEERING")][TestCase("build_id","other")][TestCase("protocol_version","real")][TestCase("audio_gain",1.0)]
        public void WrongScopeIdentityAndGainFail(string key,object value){config[key]=JToken.FromObject(value);Assert.Throws<InvalidDataException>(()=>Load());}
        [Test]public void NonDemoAndWrongPinsNeverBind()
        {var a=Load();Assert.Throws<InvalidDataException>(()=>a.Bind(new string('b',64),new string('c',64),false,true,a.OutputDirectory));Assert.Throws<InvalidDataException>(()=>a.Bind(new string('d',64),new string('c',64),true,true,a.OutputDirectory));}
        [Test]public void OrdinaryPublicOutputPathRefusedDespiteSimulationName()
        {config["output_directory"]=Path.Combine(Path.GetTempPath(),"simulation-test-public");Assert.Throws<InvalidDataException>(()=>Load());}
        [Test]public void AttestationCannotPretendToBeHumanReview()
        {
            var a=Load();var bindings=new JObject{["scripts_sha256"]=new string('d',64)};var p=new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["role"]="assessment",["fixture_set_sha256"]=a.FixtureSetSha256,["bindings"]=bindings};a.Attest(p,"assessment",bindings);
            p["approved"]=true;Assert.Throws<InvalidDataException>(()=>a.Attest(p,"assessment",bindings));
        }
        [Test]public void OrdinaryAssessmentLoaderStillRejectsSimulationAttestation()
        {
            byte[] content=Encoding.UTF8.GetBytes("{\"version\":1,\"scripts\":{}}");var a=Load();byte[] attestation=Encoding.UTF8.GetBytes(new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["role"]="assessment",["fixture_set_sha256"]=a.FixtureSetSha256,["bindings"]=new JObject{["scripts_sha256"]=PcmWave.Hash(content)}}.ToString());
            Assert.Throws<AssessmentFault>(()=>AssessmentScripts.Load(content,PcmWave.Hash(content),attestation,PcmWave.Hash(attestation)));
        }
        [Test]public void ActualPinnedNativeSimulationFixtureLoadsWithoutInventedApproval()
        {
            string joined=Environment.GetEnvironmentVariable("AV_SIMULATION_JOINED_CONFIG"),cap=Environment.GetEnvironmentVariable("AV_SIMULATION_CAPABILITY");
            if(joined==null||cap==null)Assert.Ignore("Explicit local native simulation fixture not supplied");
            var c=JoinedEngineeringConfig.Load(joined,PcmWave.Hash(File.ReadAllBytes(joined)),"simulation-test-v1");
            var authority=SimulationTestAuthority.Load(cap,PcmWave.Hash(File.ReadAllBytes(cap)),c.BuildId,c.ProtocolVersion);
            var assets=new JoinedVisitArtifacts(c,authority);
            Assert.That(assets.MissingAuthority,Is.Null);Assert.That(assets.Route.IsQualified,Is.False);Assert.That(assets.Route.SimulationOnly,Is.True);
            Assert.That(assets.Package.Demo&&assets.Schedule.Demo,Is.True);Assert.That(assets.Teaching,Is.Not.Null);Assert.That(assets.Grammar,Is.Not.Null);Assert.That(assets.Scripts,Is.Not.Null);
            Assert.That(new JoinedVisitArtifacts(c).MissingAuthority,Is.EqualTo("JOIN_AUDIO_CALIBRATION_MISSING"));
        }
    }
}
