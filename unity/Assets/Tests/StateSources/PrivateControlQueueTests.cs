using System;
using System.Collections.Generic;
using System.IO;
using AcousticVocab.StateIntegration;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    public sealed class PrivateControlQueueTests
    {
        const string Pin="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        double now;PrivateModeResetClient client;readonly List<JObject> journal=new List<JObject>();bool rejectPersist;
        JObject Health(double sample)=>new JObject{["control_session_id"]=Pin,["mode"]="teaching",["paused"]=false,["stopped"]=false,["fault"]=null,["demo_active"]=false,["publisher_ready"]=true,["neutral_verification_age_ms"]=10,["publisher_age_ms"]=15,["health_sample_host_mono_ms"]=sample,["exposure_ready"]=false,["public_stream_recovered"]=false};
        JObject Reply(string id,string command,double sample)=>new JObject{["version"]=1,["kind"]="private_reply",["request_id"]=id,["accepted"]=true,["reason"]=command=="set_mode"?"MODE_CHANGED":"RESET_COMPLETE",["mode"]="teaching",["host_mono_ms"]=sample,["sim_time"]=1,["reset_ok"]=command=="set_mode"?JValue.CreateNull():new JValue(true),["duplicate"]=false,["health"]=Health(sample)};
        [SetUp]public void Setup(){now=0;journal.Clear();rejectPersist=false;client=new PrivateModeResetClient("ws://127.0.0.1:1/commands",Pin,"teaching",x=>{if(rejectPersist)throw new IOException("synthetic durable sink failure");journal.Add((JObject)x.DeepClone());},()=>now,false);}
        [TearDown]public void Cleanup()=>client.Dispose();
        string Admit()
        {
            client.RequestMode();string mode=(string)journal[0]["request"]["request_id"];now=20;client.ReceiveReply(mode,Reply(mode,"set_mode",10).ToString(),0,20);client.Pump();
            string reset=client.RequestReset();now=40;client.ReceiveReply(reset,Reply(reset,"reset",20).ToString(),20,40);
            Assert.That(client.ResetAcknowledged(reset),Is.True,"Read must persist a queued exact reset reply before admission");return reset;
        }
        [Test]public void SecondExposureReadUsesQueuedRealProgressAfterDurableWriteDelay()
        {
            string reset=Admit();now=254;Assert.That(client.ResetAcknowledged(reset),Is.True);
            // Eight milliseconds elapse during the synchronous durable request.
            // The worker received a newer actual sample while that owner waited.
            now=262;client.ReceiveHealth(Health(30).ToString(),250,260);
            Assert.That(client.ResetAcknowledged(reset),Is.True);
            var detail=client.ReadinessDiagnostic(reset);Assert.That((double)detail["receipt_elapsed_ms"],Is.EqualTo(2));Assert.That((double)detail["round_trip_ms"],Is.EqualTo(10));Assert.That((double)detail["effective_age_ms"],Is.EqualTo(27));Assert.That((int)detail["queued_arrivals"],Is.Zero);
            Assert.That(journal.Count,Is.EqualTo(4),"Health consumption does not reissue or duplicate command history");
        }
        [Test]public void AbsenceOrDuplicateProgressCannotExtendTheOriginalFreshnessBound()
        {
            string reset=Admit();now=255;Assert.That(client.ResetAcknowledged(reset),Is.True);now=255.001;Assert.That(client.ResetAcknowledged(reset),Is.False);
            now=262;client.ReceiveHealth(Health(20).ToString(),250,260);Assert.That(client.ResetAcknowledged(reset),Is.False);Assert.That((double)client.ReadinessDiagnostic(reset)["receipt_elapsed_ms"],Is.EqualTo(222));
        }
        [TestCase("stale_queue")][TestCase("future_arrival")][TestCase("wrong_session")][TestCase("unknown_field")][TestCase("clock_regression")]
        public void InvalidQueuedObservationLatchesFailureWithoutGrantingHealth(string fault)
        {
            string reset=Admit();var health=Health(30);double sent=250,received=260;now=262;
            if(fault=="stale_queue"){sent=0;received=10;}
            if(fault=="future_arrival")received=263;
            if(fault=="wrong_session")health["control_session_id"]=new string('b',32);
            if(fault=="unknown_field")health["answer"]="forbidden";
            if(fault=="clock_regression")health["health_sample_host_mono_ms"]=19;
            client.ReceiveHealth(health.ToString(),sent,received);Assert.Throws<ControlFault>(()=>client.ResetAcknowledged(reset));Assert.That(client.ResetAcknowledged(reset),Is.False);Assert.That((bool)client.ReadinessDiagnostic(reset)["failed"],Is.True);
        }
        [Test]public void NewlyReceivedUnhealthySampleRevokesPreviouslyFreshHealth()
        {string reset=Admit();now=60;var health=Health(30);health["paused"]=true;client.ReceiveHealth(health.ToString(),45,55);Assert.That(client.ResetAcknowledged(reset),Is.False);}
        [Test]public void FailedCommandPersistenceCannotRecordOrGrantReset()
        {
            Admit();string reset=client.RequestReset();now=60;client.ReceiveReply(reset,Reply(reset,"reset",30).ToString(),45,55);rejectPersist=true;
            Assert.Throws<IOException>(()=>client.ResetAcknowledged(reset));Assert.That(client.ResetAcknowledged(reset),Is.False);Assert.That((bool)client.ReadinessDiagnostic(reset)["exact_reset_recorded"],Is.False);
        }
        [Test]public void ReceiveCapacityFailureCannotAdmitPreviouslyValidReset()
        {string reset=Admit();now=60;for(int i=0;i<9;i++)client.ReceiveHealth(Health(30+i).ToString(),45,55);Assert.That(client.ResetAcknowledged(reset),Is.False);}
        [Test]public void GetterDrainIsBoundedToTheBatchPresentAtEntry()
        {
            Admit();string reset=client.RequestReset();int observed=0;
            // A separate fresh instance injects new arrivals from the durable
            // reply sink, proving Pump doesn't chase replenished input forever.
            client.Dispose();client=new PrivateModeResetClient("ws://127.0.0.1:1/commands",Pin,"teaching",row=>{journal.Add(row);if((string)row["kind"]=="control_reply"){observed++;client.ReceiveHealth(Health(20).ToString(),0,20);}},()=>now,false);
            now=0;client.RequestMode();string mode=(string)journal[journal.Count-1]["request"]["request_id"];now=20;client.ReceiveReply(mode,Reply(mode,"set_mode",10).ToString(),0,20);client.Pump();
            Assert.That(observed,Is.EqualTo(1));Assert.That((int)client.ReadinessDiagnostic(reset)["queued_arrivals"],Is.EqualTo(1));Assert.That(client.NeutralHoldHealthy,Is.True);
        }
    }
}
