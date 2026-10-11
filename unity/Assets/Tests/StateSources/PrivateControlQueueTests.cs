using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using AcousticVocab.StateIntegration;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    public sealed class PrivateControlQueueTests
    {
        const string Pin="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        double now;PrivateModeResetClient client;readonly List<JObject> journal=new List<JObject>();bool rejectPersist;Action<JObject> duringPersist;
        JObject Health(double sample)=>new JObject{["control_session_id"]=Pin,["mode"]="teaching",["paused"]=false,["stopped"]=false,["fault"]=null,["demo_active"]=false,["publisher_ready"]=true,["neutral_verification_age_ms"]=10,["publisher_age_ms"]=15,["health_sample_host_mono_ms"]=sample,["exposure_ready"]=false,["public_stream_recovered"]=false};
        JObject Reply(string id,string command,double sample)=>new JObject{["version"]=1,["kind"]="private_reply",["request_id"]=id,["accepted"]=true,["reason"]=command=="set_mode"?"MODE_CHANGED":"RESET_COMPLETE",["mode"]="teaching",["host_mono_ms"]=sample,["sim_time"]=1,["reset_ok"]=command=="set_mode"?JValue.CreateNull():new JValue(true),["duplicate"]=false,["health"]=Health(sample)};
        void ReceiveHealth(string raw,double sent,double received) { string id=Guid.NewGuid().ToString("N"); var reply=new JObject{["version"]=1,["kind"]="private_health_reply",["control_session_id"]=Pin,["request_id"]=id,["accepted"]=true,["reason"]="HEALTH",["health"]=JObject.Parse(raw)}; client.ReceiveHealthProbe(id,reply.ToString(),sent,received); }
        [SetUp]public void Setup(){now=0;journal.Clear();rejectPersist=false;duringPersist=null;client=new PrivateModeResetClient("ws://127.0.0.1:1/commands",Pin,"teaching",x=>{if(rejectPersist)throw new IOException("synthetic durable sink failure");duringPersist?.Invoke(x);journal.Add((JObject)x.DeepClone());},()=>now,false);}
        [TearDown]public void Cleanup()=>client.Dispose();
        string Admit()
        {
            client.RequestMode();string mode=(string)journal[0]["request"]["request_id"];now=20;client.ReceiveReply(mode,Reply(mode,"set_mode",10).ToString(),0,20);client.Pump();
            string reset=client.RequestReset();now=30;client.ReceiveReply(reset,Reply(reset,"reset",15).ToString(),20,30);
            Assert.That(client.ResetAcknowledged(reset),Is.False,"An exact ACK alone must not admit exposure");
            now=40;var probe=Health(20);probe["publisher_age_ms"]=25;ReceiveHealth(probe.ToString(),30,40);
            Assert.That(client.ResetAcknowledged(reset),Is.True,"Admission requires a progressing real probe after the durable ACK");return reset;
        }
        [Test]public void SecondExposureReadUsesQueuedRealProgressAfterDurableWriteDelay()
        {
            string reset=Admit();now=254;Assert.That(client.ResetAcknowledged(reset),Is.True);
            // Eight milliseconds elapse during the synchronous durable request.
            // The worker received a newer actual sample while that owner waited.
            now=262;ReceiveHealth(Health(30).ToString(),250,260);
            Assert.That(client.ResetAcknowledged(reset),Is.True);
            var detail=client.ReadinessDiagnostic(reset);Assert.That((double)detail["receipt_elapsed_ms"],Is.EqualTo(2));Assert.That((double)detail["round_trip_ms"],Is.EqualTo(10));Assert.That((double)detail["effective_age_ms"],Is.EqualTo(27));Assert.That((int)detail["queued_arrivals"],Is.Zero);
            Assert.That(journal.Count,Is.EqualTo(4),"Health consumption does not reissue or duplicate command history");
        }
        [TestCase("missing",false)][TestCase("sent_before_ack",false)][TestCase("same_host_sample",false)]
        [TestCase("wrong_mode",false)][TestCase("stale",false)][TestCase("fresh",true)]
        public void NewExactResetNeedsItsOwnFreshProgressingPostReceiptProbe(string scenario,bool expected)
        {
            string previous=Admit();string reset=client.RequestReset();now=70;
            client.ReceiveReply(reset,Reply(reset,"reset",40).ToString(),50,70);
            Assert.That(client.ResetAcknowledged(reset),Is.False);
            Assert.That((bool)client.ReadinessDiagnostic(reset)["exact_reset_recorded"],Is.True,"Durable exact acknowledgement history is retained while admission waits");
            Assert.That((bool)client.ReadinessDiagnostic(reset)["post_reset_probe_observed"],Is.False);
            Assert.That(client.ResetAcknowledged(previous),Is.False,"A command snapshot cannot silently replace the required probe for exposure");
            now=100;
            if(scenario!="missing")
            {
                var probe=Health(scenario=="same_host_sample"?40:50);
                if(scenario=="wrong_mode")probe["mode"]="test";
                if(scenario=="stale")probe["publisher_age_ms"]=225;
                ReceiveHealth(probe.ToString(),scenario=="sent_before_ack"?69:70,90);
            }
            Assert.That(client.ResetAcknowledged(reset),Is.EqualTo(expected));
            Assert.That(journal.Count,Is.EqualTo(6),"Waiting for a probe does not issue another reset, duplicate its journal row or create new authority");
        }
        [Test]public void NativeNearExpiryResetReceiptWaitsForRealProbeWithoutFresheningItsAges()
        {
            Admit();string reset=client.RequestReset();now=41597.9773;
            var reply=Reply(reset,"reset",1220897258.133139);
            reply["health"]["neutral_verification_age_ms"]=3.889507;reply["health"]["publisher_age_ms"]=90.972948;
            client.ReceiveReply(reset,reply.ToString(),41457.3190,41582.8842);
            Assert.That(client.ResetAcknowledged(reset),Is.False,"A009's actual reset ACK has too little remaining budget to serve as the first post-reset probe");
            var retained=client.ReadinessDiagnostic(reset);
            Assert.That((double)retained["last_command_received_local_mono_ms"],Is.EqualTo(41582.8842));
            Assert.That((double)retained["round_trip_ms"],Is.EqualTo(10),"Historical ACK never overwrites the prior probe timestamps");
            Assert.That((double)retained["publisher_age_ms"],Is.EqualTo(25));
            Assert.That((bool)retained["health_progressing_and_valid"],Is.False);
            now=41620;Assert.That(client.ResetAcknowledged(reset),Is.False);
            var probe=Health(1220897300);probe["publisher_age_ms"]=20;now=41640;
            ReceiveHealth(probe.ToString(),41582.9806,41635);
            Assert.That(client.ResetAcknowledged(reset),Is.True);
            var fresh=client.ReadinessDiagnostic(reset);Assert.That((double)fresh["round_trip_ms"],Is.EqualTo(52.0194).Within(.00001));
            Assert.That((double)fresh["receipt_elapsed_ms"],Is.EqualTo(5));
            now=41813;Assert.That(client.ResetAcknowledged(reset),Is.False,"The probe's original 250 ms bound still expires; ACK history is not freshened");
            Assert.That((bool)client.ReadinessDiagnostic(reset)["exact_reset_recorded"],Is.True);
        }
        [Test]public void NativeSlowDurableResetIsHistoryUntilARealFreshPostReceiptProbe()
        {
            Admit();string reset=client.RequestReset();var ack=Reply(reset,"reset",1222206886);
            ack["health"]["neutral_verification_age_ms"]=216.807068;ack["health"]["publisher_age_ms"]=336.50367;ack["health"]["publisher_ready"]=false;
            now=62599;client.ReceiveReply(reset,ack.ToString(),62061.5561,62598.4520);
            bool written=false;duringPersist=row=>{if((string)row["kind"]=="control_reply"){written=true;Assert.That((bool)client.ReadinessDiagnostic(reset)["exact_reset_recorded"],Is.False,"The durable write must precede historical completion");}};
            Assert.That(client.ResetAcknowledged(reset),Is.False);Assert.That(written,Is.True);
            var history=client.ReadinessDiagnostic(reset);Assert.That((bool)history["exact_reset_recorded"],Is.True);Assert.That((bool)history["failed"],Is.False);
            Assert.That((bool)history["health_progressing_and_valid"],Is.False);Assert.That((double)history["round_trip_ms"],Is.EqualTo(10),"ACK cannot replace the probe's original age evidence");
            Assert.That(JToken.DeepEquals(journal[journal.Count-1]["reply"],ack),Is.True);
            now=62670;ReceiveHealth(Health(1222207000).ToString(),62599,62669);Assert.That(client.ResetAcknowledged(reset),Is.True);
            Assert.That((double)client.ReadinessDiagnostic(reset)["effective_age_ms"],Is.EqualTo(86));
            now=62835;Assert.That(client.ResetAcknowledged(reset),Is.False,"The real probe still expires at its unchanged bound");
        }
        [Test]public void PendingMutationsRevokeAllPriorResetAndHoldGrantsUntilLatestCompletionProbe()
        {
            string old=Admit(),first=client.RequestReset(),second=client.RequestReset();now=60;
            ReceiveHealth(Health(30).ToString(),45,55);Assert.That(client.NeutralHoldHealthy,Is.False);Assert.That(client.ResetAcknowledged(old),Is.False);
            now=80;client.ReceiveReply(first,Reply(first,"reset",40).ToString(),60,70);ReceiveHealth(Health(50).ToString(),70,80);
            Assert.That(client.ResetAcknowledged(first),Is.False,"Another command is still pending");
            now=100;client.ReceiveReply(second,Reply(second,"reset",60).ToString(),80,90);client.Pump();
            Assert.That(client.ResetAcknowledged(first),Is.False);Assert.That(client.ResetAcknowledged(second),Is.False);
            ReceiveHealth(Health(70).ToString(),90,100);Assert.That(client.ResetAcknowledged(first),Is.True);Assert.That(client.ResetAcknowledged(second),Is.True);
            Assert.That(journal.Count,Is.EqualTo(8),"Every command has exactly one request and durable completion");
        }
        [Test]public void RequestPersistenceCannotReenterAdmissionAndFailureLatchesOriginalCause()
        {
            string old=Admit();now=60;ReceiveHealth(Health(30).ToString(),45,55);
            duringPersist=_=>{Assert.That(client.NeutralHoldHealthy,Is.False);Assert.That(client.ResetAcknowledged(old),Is.False);};
            client.RequestReset();duringPersist=null;rejectPersist=true;
            Assert.Throws<IOException>(()=>client.RequestReset());Assert.That(client.NeutralHoldHealthy,Is.False);
            var detail=client.ReadinessDiagnostic(old);Assert.That((string)detail["first_failure_code"],Is.EqualTo("CONTROL_IO_FAILED"));Assert.That((string)detail["first_failure_phase"],Is.EqualTo("request_persist"));
        }
        [Test]public void FreshProbeQueuedDuringDurableAckWriteRetainsItsRealReceiptAge()
        {
            Admit();string reset=client.RequestReset();now=600;client.ReceiveReply(reset,Reply(reset,"reset",1000).ToString(),50,590);
            duringPersist=row=>{if((string)row["kind"]=="control_reply"){ReceiveHealth(Health(1010).ToString(),590,610);now=620;}};
            Assert.That(client.ResetAcknowledged(reset),Is.False,"The entry batch must not chase new arrivals during persistence");
            Assert.That(client.ResetAcknowledged(reset),Is.True);Assert.That((double)client.ReadinessDiagnostic(reset)["receipt_elapsed_ms"],Is.EqualTo(10));
            now=861;Assert.That(Assert.Throws<ControlFault>(()=>{ReceiveHealth(Health(1020).ToString(),590,610);client.Pump();}).Code,Is.EqualTo("CONTROL_QUEUED"));
        }
        [TestCase("wrong_session")][TestCase("negative_age")][TestCase("unknown_health_field")][TestCase("missing_health_field")]
        [TestCase("bad_health_bool")][TestCase("bad_fault_type")][TestCase("bad_top_mode")][TestCase("bad_reset")]
        [TestCase("duplicate")][TestCase("unknown_id")][TestCase("host_regression")][TestCase("bad_version")]
        public void SlowHistoricalReplyStillRequiresEveryIdentityAndSchemaCheck(string mutation)
        {
            Admit();string reset=client.RequestReset();var ack=Reply(reset,"reset",30);
            switch(mutation)
            {
                case "wrong_session":ack["health"]["control_session_id"]=new string('b',32);break;
                case "negative_age":ack["health"]["publisher_age_ms"]=-1;break;
                case "unknown_health_field":ack["health"]["answer"]="forbidden";break;
                case "missing_health_field":((JObject)ack["health"]).Remove("publisher_ready");break;
                case "bad_health_bool":ack["health"]["paused"]="false";break;
                case "bad_fault_type":ack["health"]["fault"]=7;break;
                case "bad_top_mode":ack["mode"]="test";break;
                case "bad_reset":ack["reset_ok"]=false;break;
                case "duplicate":ack["duplicate"]=true;break;
                case "unknown_id":ack["request_id"]=Guid.NewGuid().ToString("N");break;
                case "host_regression":ack["health"]["health_sample_host_mono_ms"]=19;break;
                case "bad_version":ack["version"]=1.0;break;
            }
            now=610;client.ReceiveReply(reset,ack.ToString(),50,600);Assert.Catch(()=>client.Pump());
            Assert.That(client.NeutralHoldHealthy,Is.False);Assert.That((bool)client.ReadinessDiagnostic(reset)["exact_reset_recorded"],Is.False);Assert.That(journal.Count,Is.EqualTo(5));
        }
        [TestCase(3000,true)][TestCase(3000.001,false)]
        public void HistoricalCommandRetainsOriginalThreeSecondDeadline(double duration,bool accepted)
        {
            Admit();string reset=client.RequestReset();now=50+duration;client.ReceiveReply(reset,Reply(reset,"reset",30).ToString(),50,now);
            if(accepted){client.Pump();Assert.That((bool)client.ReadinessDiagnostic(reset)["exact_reset_recorded"],Is.True);Assert.That(client.ResetAcknowledged(reset),Is.False);}
            else{Assert.That(Assert.Throws<ControlFault>(()=>client.Pump()).Code,Is.EqualTo("CONTROL_STALE"));Assert.That((bool)client.ReadinessDiagnostic(reset)["exact_reset_recorded"],Is.False);}
        }
        [Test]public void ReplayedCompletionCannotOverwriteDurableResetReceipt()
        {
            string reset=Admit();now=60;client.ReceiveReply(reset,Reply(reset,"reset",30).ToString(),45,55);
            Assert.That(Assert.Throws<ControlFault>(()=>client.Pump()).Code,Is.EqualTo("CONTROL_REPLY"));Assert.That(journal.Count,Is.EqualTo(4));Assert.That(client.ResetAcknowledged(reset),Is.False);
        }
        [Test]public void SlowModeCompletionAloneCannotGrantNeutralHold()
        {
            client.RequestMode();string id=(string)journal[0]["request"]["request_id"];now=550;
            client.ReceiveReply(id,Reply(id,"set_mode",10).ToString(),0,550);client.Pump();
            Assert.That(client.ModeAcknowledged,Is.True,"Durable mode completion may construct the next safe-boundary operation");Assert.That(client.NeutralHoldHealthy,Is.False);
            now=580;ReceiveHealth(Health(20).ToString(),550,580);Assert.That(client.NeutralHoldHealthy,Is.True);
            client.RequestReset();Assert.That(client.NeutralHoldHealthy,Is.False,"Pending reset revokes even a fresh post-mode probe");
        }
        [Test]public void CommandSampleFloorCannotBeBypassedByAnOlderOrEqualProbe()
        {
            Admit();string reset=client.RequestReset();now=600;client.ReceiveReply(reset,Reply(reset,"reset",1000).ToString(),50,590);client.Pump();
            now=620;ReceiveHealth(Health(1000).ToString(),590,610);Assert.That(client.ResetAcknowledged(reset),Is.False);
            now=640;ReceiveHealth(Health(999).ToString(),620,630);Assert.That(Assert.Throws<ControlFault>(()=>client.Pump()).Code,Is.EqualTo("CONTROL_STALE"));Assert.That(client.ResetAcknowledged(reset),Is.False);
        }
        [Test]public void AbsenceOrDuplicateProgressCannotExtendTheOriginalFreshnessBound()
        {
            string reset=Admit();now=255;Assert.That(client.ResetAcknowledged(reset),Is.True);now=255.001;Assert.That(client.ResetAcknowledged(reset),Is.False);
            now=262;ReceiveHealth(Health(20).ToString(),250,260);Assert.That(client.ResetAcknowledged(reset),Is.False);Assert.That((double)client.ReadinessDiagnostic(reset)["receipt_elapsed_ms"],Is.EqualTo(222));
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
            ReceiveHealth(health.ToString(),sent,received);Assert.Throws<ControlFault>(()=>client.ResetAcknowledged(reset));Assert.That(client.ResetAcknowledged(reset),Is.False);Assert.That((bool)client.ReadinessDiagnostic(reset)["failed"],Is.True);
        }
        [Test]public void NewlyReceivedUnhealthySampleRevokesPreviouslyFreshHealth()
        {string reset=Admit();now=60;var health=Health(30);health["paused"]=true;ReceiveHealth(health.ToString(),45,55);Assert.That(client.ResetAcknowledged(reset),Is.False);}
        [TestCase(10)][TestCase(536.8959)]public void FailedCommandPersistenceCannotRecordOrGrantReset(double duration)
        {
            Admit();string reset=client.RequestReset();now=45+duration;client.ReceiveReply(reset,Reply(reset,"reset",30).ToString(),45,now);rejectPersist=true;
            Assert.Throws<IOException>(()=>client.ResetAcknowledged(reset));Assert.That(client.ResetAcknowledged(reset),Is.False);Assert.That((bool)client.ReadinessDiagnostic(reset)["exact_reset_recorded"],Is.False);
        }
        [Test]public void ReceiveCapacityFailureCannotAdmitPreviouslyValidReset()
        {string reset=Admit();now=60;for(int i=0;i<9;i++)ReceiveHealth(Health(30+i).ToString(),45,55);Assert.That(client.ResetAcknowledged(reset),Is.False);}
        [Test]public void CapacityCauseSurvivesExplicitInterruptionAndDisposal()
        {
            string reset=Admit();now=60;for(int i=0;i<9;i++)ReceiveHealth(Health(30+i).ToString(),45,55);
            var first=client.ReadinessDiagnostic(reset);Assert.That((string)first["first_failure_code"],Is.EqualTo("CONTROL_ARRIVAL_CAPACITY"));Assert.That((string)first["first_failure_phase"],Is.EqualTo("receive_queue"));Assert.That((double)first["first_failure_local_mono_ms"],Is.EqualTo(60));
            now=70;client.Interrupt();client.Dispose();var later=client.ReadinessDiagnostic(reset);Assert.That((string)later["first_failure_code"],Is.EqualTo((string)first["first_failure_code"]));Assert.That((double)later["first_failure_local_mono_ms"],Is.EqualTo(60));
        }
        [Test]public void StaleQueueCauseIsRecordedBeforeInterruptAndUnknownResetIsNotInvented()
        {
            string reset=Admit();now=400;ReceiveHealth(Health(30).ToString(),50,60);Assert.Throws<ControlFault>(()=>client.Pump());
            var detail=client.ReadinessDiagnostic(null);Assert.That((string)detail["first_failure_code"],Is.EqualTo("CONTROL_QUEUED"));Assert.That((string)detail["first_failure_phase"],Is.EqualTo("pump"));Assert.That(detail["exact_reset_recorded"].Type,Is.EqualTo(JTokenType.Null));
        }
        [Test]public void DiagnosticOnlyReadsCannotDrainOrFreshenQueuedHealth()
        {
            string reset=Admit();now=262;ReceiveHealth(Health(30).ToString(),250,260);var detail=client.ReadinessDiagnostic(reset);
            Assert.That((int)detail["queued_arrivals"],Is.EqualTo(1));Assert.That((double)detail["receipt_elapsed_ms"],Is.EqualTo(222));Assert.That((bool)detail["health_progressing_and_valid"],Is.True);
            Assert.That(detail["first_failure_code"].Type,Is.EqualTo(JTokenType.Null));Assert.That(client.ResetAcknowledged(reset),Is.True);
        }
        [Test]public void ExplicitInterruptIsDistinguishableFromUnobservedTransportFailure()
        {
            Admit();now=70;client.Interrupt();var detail=client.ReadinessDiagnostic(null);
            Assert.That((string)detail["first_failure_code"],Is.EqualTo("CONTROL_EXPLICIT_INTERRUPT"));Assert.That((string)detail["first_failure_phase"],Is.EqualTo("owner"));
            Assert.That(PrivateModeResetClient.FailureCode(new System.Threading.Tasks.TaskCanceledException()),Is.EqualTo("CONTROL_OPERATION_CANCELLED"));
            Assert.That(PrivateModeResetClient.FailureCode(new System.Net.WebSockets.WebSocketException()),Is.EqualTo("CONTROL_SOCKET_ERROR"));
            Assert.That(PrivateModeResetClient.FailureCode(new ControlFault("CONTROL_TRANSPORT_DEADLINE")),Is.EqualTo("CONTROL_TRANSPORT_DEADLINE"));
            Assert.That(PrivateModeResetClient.FailureCode(new Exception("private text must not leak")),Is.EqualTo("CONTROL_WORKER_EXCEPTION"));
        }
        // #148 decision after attempt simulation-test-B-V1-018-assess-007: an idle
        // profile-menu preflight saw a ~415 ms main-thread stall with four
        // 75 ms-cadence health replies queued and latched CONTROL_QUEUED.
        [Test]public void IdlePreflightDropsStaleProbesAfterMainThreadStallAndGrantsOnlyOnAFreshProbe()
        {
            client.DropStaleHealthProbesWhile(()=>true);string reset=Admit();int rows=journal.Count;
            now=300;ReceiveHealth(Health(200).ToString(),290,295);Assert.That(client.ResetAcknowledged(reset),Is.True);
            for(int i=0;i<4;i++)ReceiveHealth(Health(210+i).ToString(),303+75*i,310+75*i); // worker keeps probing while Update stalls
            now=800;Assert.That(client.ResetAcknowledged(reset),Is.False,"Stale replies never grant");Assert.That(client.NeutralHoldHealthy,Is.False);
            var detail=client.ReadinessDiagnostic(reset);Assert.That((bool)detail["failed"],Is.False,"No permanent latch while unexposed");
            Assert.That((int)detail["stale_probes_dropped"],Is.EqualTo(4));Assert.That((int)detail["queued_arrivals"],Is.Zero);
            Assert.That((double)detail["last_stale_probe_age_ms"],Is.EqualTo(265));Assert.That((double)detail["last_stale_probe_dropped_local_mono_ms"],Is.EqualTo(800));
            Assert.That(journal.Skip(rows).Select(x=>(string)x["kind"]),Is.EqualTo(Enumerable.Repeat("stale_health_probe_dropped",4)));
            Assert.That(journal.Skip(rows).Select(x=>(double)x["queue_age_ms"]),Is.EqualTo(new[]{490d,415,340,265}));
            now=805;Assert.That(client.ResetAcknowledged(reset),Is.False,"Still no fresh correlated probe");
            now=830;ReceiveHealth(Health(300).ToString(),815,825);Assert.That(client.ResetAcknowledged(reset),Is.True,"Only a fresh correlated probe restores the grant");
            Assert.That((double)client.ReadinessDiagnostic(reset)["effective_age_ms"],Is.EqualTo(30),"The 250 ms bound is applied unchanged to the fresh probe");
        }
        [TestCase("exposure_pending")][TestCase("never_configured")]
        public void StaleProbeWhileExposurePendingStillLatches(string policy)
        {
            bool unexposed=true;if(policy=="exposure_pending")client.DropStaleHealthProbesWhile(()=>unexposed);
            string reset=Admit();unexposed=false; // a cue is pending/scheduled or within its gate
            for(int i=0;i<4;i++)ReceiveHealth(Health(210+i).ToString(),303+75*i,310+75*i);now=800;
            Assert.That(Assert.Throws<ControlFault>(()=>client.ResetAcknowledged(reset)).Code,Is.EqualTo("CONTROL_QUEUED"));
            var detail=client.ReadinessDiagnostic(reset);Assert.That((bool)detail["failed"],Is.True);Assert.That((int)detail["stale_probes_dropped"],Is.Zero);
            Assert.That((string)detail["first_failure_code"],Is.EqualTo("CONTROL_QUEUED"));
            now=830;ReceiveHealth(Health(300).ToString(),815,825);Assert.That(client.ResetAcknowledged(reset),Is.False,"The latch is permanent");
        }
        [Test]public void StaleCommandReplyAtIdlePreflightStillLatches()
        {
            client.DropStaleHealthProbesWhile(()=>true);Admit();string reset=client.RequestReset();
            now=400;client.ReceiveReply(reset,Reply(reset,"reset",30).ToString(),50,60);
            Assert.That(Assert.Throws<ControlFault>(()=>client.Pump()).Code,Is.EqualTo("CONTROL_QUEUED"));
            var detail=client.ReadinessDiagnostic(reset);Assert.That((bool)detail["failed"],Is.True);Assert.That((bool)detail["exact_reset_recorded"],Is.False);Assert.That((int)detail["stale_probes_dropped"],Is.Zero);
        }
        [Test]public void StalePolicyIsSetOnceAndAThrowingPredicateIsStrict()
        {
            client.DropStaleHealthProbesWhile(()=>throw new InvalidOperationException("synthetic"));
            Assert.That(Assert.Throws<ControlFault>(()=>client.DropStaleHealthProbesWhile(()=>true)).Code,Is.EqualTo("CONTROL_POLICY"));
            string reset=Admit();ReceiveHealth(Health(30).ToString(),50,60);now=400;
            Assert.That(Assert.Throws<ControlFault>(()=>client.ResetAcknowledged(reset)).Code,Is.EqualTo("CONTROL_QUEUED"));
        }
        [Test]public void GetterDrainIsBoundedToTheBatchPresentAtEntry()
        {
            Admit();string reset=client.RequestReset();int observed=0;
            // A separate fresh instance injects new arrivals from the durable
            // reply sink, proving Pump doesn't chase replenished input forever.
            client.Dispose();client=new PrivateModeResetClient("ws://127.0.0.1:1/commands",Pin,"teaching",row=>{journal.Add(row);if((string)row["kind"]=="control_reply"){observed++;ReceiveHealth(Health(20).ToString(),20,30);}},()=>now,false);
            now=0;client.RequestMode();string mode=(string)journal[journal.Count-1]["request"]["request_id"];now=20;client.ReceiveReply(mode,Reply(mode,"set_mode",10).ToString(),0,20);client.Pump();
            Assert.That(observed,Is.EqualTo(1));Assert.That((int)client.ReadinessDiagnostic(reset)["queued_arrivals"],Is.EqualTo(1));now=30;Assert.That(client.NeutralHoldHealthy,Is.True);
        }
    }
}
