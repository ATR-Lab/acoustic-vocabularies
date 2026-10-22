using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.StateIntegration;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    // #81 failed_reset hook: the actual reply is withheld at the receive queue.
    // The ordinary pending/health gates must then refuse exposure on their own.
    public sealed class SimulationResetFaultTests
    {
        const string Pin="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        double now;PrivateModeResetClient client;readonly List<JObject> journal=new List<JObject>();string root;SimulationTestAuthority authority;
        JObject Health(double sample)=>new JObject{["control_session_id"]=Pin,["mode"]="teaching",["paused"]=false,["stopped"]=false,["fault"]=null,["demo_active"]=false,["publisher_ready"]=true,["neutral_verification_age_ms"]=10,["publisher_age_ms"]=15,["health_sample_host_mono_ms"]=sample,["exposure_ready"]=false,["public_stream_recovered"]=false};
        JObject Reply(string id,string command,double sample)=>new JObject{["version"]=1,["kind"]="private_reply",["request_id"]=id,["accepted"]=true,["reason"]=command=="set_mode"?"MODE_CHANGED":"RESET_COMPLETE",["mode"]="teaching",["host_mono_ms"]=sample,["sim_time"]=1,["reset_ok"]=command=="set_mode"?JValue.CreateNull():new JValue(true),["duplicate"]=false,["health"]=Health(sample)};
        void ReceiveHealth(double sample,double sent,double received){string id=Guid.NewGuid().ToString("N");client.ReceiveHealthProbe(id,new JObject{["version"]=1,["kind"]="private_health_reply",["control_session_id"]=Pin,["request_id"]=id,["accepted"]=true,["reason"]="HEALTH",["health"]=Health(sample)}.ToString(),sent,received);}
        [SetUp]public void Setup()
        {
            now=0;journal.Clear();client=new PrivateModeResetClient("ws://127.0.0.1:1/commands",Pin,"teaching",x=>journal.Add((JObject)x.DeepClone()),()=>now,false);
            root=Path.Combine(Path.GetTempPath(),".local","simulation-test-reset-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
            byte[] raw=Encoding.UTF8.GetBytes(new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["fixture_set_sha256"]=new string('a',64),["package_sha256"]=new string('b',64),["schedule_sha256"]=new string('c',64),["build_id"]="mock-build",["protocol_version"]="simulation-test-v1",["output_directory"]=Path.Combine(root,"evidence"),["audio_gain"]=.05,["participant_admission"]=false,["acoustic_qualification"]=false}.ToString());
            string path=Path.Combine(root,"capability.json");File.WriteAllBytes(path,raw);
            using var sha=SHA256.Create();authority=SimulationTestAuthority.Load(path,BitConverter.ToString(sha.ComputeHash(raw)).Replace("-","").ToLowerInvariant(),"mock-build","simulation-test-v1");
        }
        [TearDown]public void Cleanup(){client.Dispose();if(Directory.Exists(root))Directory.Delete(root,true);}
        void Mode(){client.RequestMode();string mode=(string)journal[0]["request"]["request_id"];now=20;client.ReceiveReply(mode,Reply(mode,"set_mode",10).ToString(),0,20);client.Pump();}
        [Test]public void HookRequiresSimulationAuthority()
        {
            Assert.That(Assert.Throws<ControlFault>(()=>client.SimulationWithholdNextResetReply(null,_=>{})).Code,Is.EqualTo("CONTROL_SIMULATION_AUTHORITY"));
            Assert.Throws<ControlFault>(()=>client.SimulationCancelWithheldReset(null));
        }
        [Test]public void WithheldActualResetReplyIsNotPersistedAndNeverAdmitsExposure()
        {
            Mode();string withheld=null;client.SimulationWithholdNextResetReply(authority,id=>withheld=id);
            string reset=client.RequestReset();now=30;client.ReceiveReply(reset,Reply(reset,"reset",15).ToString(),20,30);client.Pump();
            Assert.That(withheld,Is.EqualTo(reset));
            Assert.That(journal.FindAll(x=>(string)x["kind"]=="control_reply"&&(string)x["reply"]["request_id"]==reset),Is.Empty,"No reply evidence is fabricated or kept");
            now=40;ReceiveHealth(20,30,40);
            Assert.That(client.ResetAcknowledged(reset),Is.False);Assert.That(client.NeutralHoldHealthy,Is.False,"The unanswered request keeps exposure blocked");
            Assert.That((int)client.ReadinessDiagnostic(reset)["pending_commands"],Is.EqualTo(1));Assert.That((bool)client.ReadinessDiagnostic(reset)["exact_reset_recorded"],Is.False);
        }
        [Test]public void OnlyTheNextResetIsWithheldAndModeRepliesPass()
        {
            int withheld=0;client.SimulationWithholdNextResetReply(authority,_=>withheld++);Mode();Assert.That(client.ModeAcknowledged,Is.True);
            string first=client.RequestReset();now=30;client.ReceiveReply(first,Reply(first,"reset",15).ToString(),20,30);client.Pump();
            string second=client.RequestReset();now=50;client.ReceiveReply(second,Reply(second,"reset",35).ToString(),40,50);client.Pump();
            Assert.That(withheld,Is.EqualTo(1));Assert.That(journal.Exists(x=>(string)x["kind"]=="control_reply"&&(string)x["reply"]["request_id"]==second),Is.True);
        }
        [Test]public void CancelledWithholdLeavesOrdinaryResetPath()
        {
            Mode();client.SimulationWithholdNextResetReply(authority,_=>Assert.Fail("cancelled"));client.SimulationCancelWithheldReset(authority);
            string reset=client.RequestReset();now=30;client.ReceiveReply(reset,Reply(reset,"reset",15).ToString(),20,30);
            now=40;ReceiveHealth(20,30,40);Assert.That(client.ResetAcknowledged(reset),Is.True);
        }
    }
}
