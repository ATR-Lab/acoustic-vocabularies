using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using AcousticVocab.Foundation;
using AcousticVocab.StateIntegration;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    // #148 regression: the real Run() worker, its catch and RecordFailure, after
    // an admitted reset. A scripted loopback peer answers the actual command
    // and probe envelopes; only the transport fault is synthetic.
    public sealed class PrivateControlWorkerFailureTests
    {
        const string Pin="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        static double Clock()=>Stopwatch.GetTimestamp()*1000.0/Stopwatch.Frequency;
        static JObject Health(double sample)=>new JObject{["control_session_id"]=Pin,["mode"]="teaching",["paused"]=false,["stopped"]=false,["fault"]=null,["demo_active"]=false,["publisher_ready"]=true,["neutral_verification_age_ms"]=10,["publisher_age_ms"]=15,["health_sample_host_mono_ms"]=sample,["exposure_ready"]=false,["public_stream_recovered"]=false};
        static JObject ProbeReply(string id,double sample)=>new JObject{["version"]=1,["kind"]="private_health_reply",["control_session_id"]=Pin,["request_id"]=id,["accepted"]=true,["reason"]="HEALTH",["health"]=Health(sample)};
        sealed class ScriptedPeer:WebSocket
        {
            internal volatile string FailProbe;internal int Probes,Commands;double sample=1000;
            byte[] reply;bool replyIsProbe;
            internal readonly ManualResetEventSlim Failed=new ManualResetEventSlim();
            public override WebSocketCloseStatus? CloseStatus=>null;public override string CloseStatusDescription=>null;public override WebSocketState State=>WebSocketState.Open;public override string SubProtocol=>null;
            public override void Abort(){}public override void Dispose(){}
            public override Task CloseAsync(WebSocketCloseStatus s,string d,CancellationToken c)=>Task.CompletedTask;public override Task CloseOutputAsync(WebSocketCloseStatus s,string d,CancellationToken c)=>Task.CompletedTask;
            public override Task SendAsync(ArraySegment<byte> b,WebSocketMessageType t,bool end,CancellationToken token)
            {
                var request=JObject.Parse(Encoding.UTF8.GetString(b.Array,b.Offset,b.Count));string id=(string)request["request_id"];
                replyIsProbe=(string)request["kind"]=="private_health_probe";
                if(replyIsProbe&&FailProbe=="send"){Failed.Set();throw new WebSocketException("scripted send failure");}
                sample+=10;JObject value;
                if(replyIsProbe){Interlocked.Increment(ref Probes);value=ProbeReply(id,sample);}
                else
                {
                    Interlocked.Increment(ref Commands);bool mode=(string)request["command"]=="set_mode";
                    value=new JObject{["version"]=1,["kind"]="private_reply",["request_id"]=id,["accepted"]=true,["reason"]=mode?"MODE_CHANGED":"RESET_COMPLETE",["mode"]="teaching",
                        ["host_mono_ms"]=sample,["sim_time"]=1,["reset_ok"]=mode?JValue.CreateNull():new JValue(true),["duplicate"]=false,["health"]=Health(sample)};
                }
                reply=Encoding.UTF8.GetBytes(value.ToString(Formatting.None));return Task.CompletedTask;
            }
            public override async Task<WebSocketReceiveResult> ReceiveAsync(ArraySegment<byte> buffer,CancellationToken token)
            {
                if(replyIsProbe&&FailProbe=="receive"){Failed.Set();throw new WebSocketException("scripted receive failure");}
                if(replyIsProbe&&FailProbe=="timeout"){Failed.Set();await Task.Delay(Timeout.Infinite,token);}
                Array.Copy(reply,0,buffer.Array,buffer.Offset,reply.Length);return new WebSocketReceiveResult(reply.Length,WebSocketMessageType.Text,true);
            }
        }
        static bool Until(Func<bool> condition,int milliseconds)
        {var watch=Stopwatch.StartNew();while(watch.ElapsedMilliseconds<milliseconds){if(condition())return true;Thread.Sleep(2);}return condition();}

        [TestCase("send","send","CONTROL_SOCKET_ERROR")]
        [TestCase("receive","receive","CONTROL_SOCKET_ERROR")]
        [TestCase("timeout","receive","CONTROL_OPERATION_CANCELLED")]
        public void WorkerFailureAfterAdmittedResetRevokesItAndRetainsFirstCause(string fault,string stage,string code)
        {
            var journal=new List<JObject>();var peer=new ScriptedPeer();var ledger=new WorkerLedger();
            using var client=new PrivateModeResetClient("ws://127.0.0.1:1/commands",Pin,"teaching",row=>{lock(journal)journal.Add((JObject)row.DeepClone());},Clock,()=>peer,ledger);
            client.RequestMode();
            Assert.That(Until(()=>client.NeutralHoldHealthy,3000),Is.True,"Real worker mode ACK and fresh post-mode probe");
            string reset=client.RequestReset();
            Assert.That(Until(()=>client.ResetAcknowledged(reset),3000),Is.True,"Exact durable reset ACK plus a later fresh probe admitted exposure");
            int journalRows;lock(journal)journalRows=journal.Count;Assert.That(journalRows,Is.EqualTo(4));

            peer.FailProbe=fault;
            Assert.That(peer.Failed.Wait(2000),Is.True,"The scripted fault reached the worker's health exchange");
            Assert.That(client.Worker.Wait(2000),Is.True,"Run() ends through its catch rather than hanging");
            Assert.That(client.Worker.Status,Is.EqualTo(TaskStatus.RanToCompletion));

            Assert.That(client.ResetAcknowledged(reset),Is.False,"A failed worker revokes the previously admitted reset");
            Assert.That(client.NeutralHoldHealthy,Is.False);
            var detail=client.ReadinessDiagnostic(reset);
            Assert.That((bool)detail["failed"],Is.True);Assert.That((bool)detail["disposed"],Is.False);
            Assert.That((string)detail["first_failure_code"],Is.EqualTo(code));
            Assert.That((string)detail["first_failure_phase"],Is.EqualTo("health_exchange"));
            Assert.That(detail["first_failure_local_mono_ms"].Type,Is.EqualTo(JTokenType.Float));
            Assert.That((bool)detail["exact_reset_recorded"],Is.True,"Accepted reset history is retained, not rewritten");
            var failed=(JObject)detail["exchange_failure"];
            Assert.That(failed,Is.Not.Null,"The failed-exchange snapshot is present");
            Assert.That((string)failed["exchange_kind"],Is.EqualTo("health_probe"));Assert.That((string)failed["stage"],Is.EqualTo(stage));Assert.That((int)failed["timeout_ms"],Is.EqualTo(200));
            Assert.That((bool)failed["timeout_token_cancelled"],Is.EqualTo(fault=="timeout"));Assert.That((bool)failed["lifetime_token_cancelled"],Is.False);
            Assert.That(((string)failed["request_id"]).Length,Is.EqualTo(32));
            Assert.That(detail.ToString(),Does.Not.Contain("scripted"),"No exception text enters the diagnostic");

            // A later well-formed, fresh, correctly correlated arrival cannot
            // revive the failed client or replace the original cause.
            string late=Guid.NewGuid().ToString("N");double now=Clock();
            client.ReceiveHealthProbe(late,ProbeReply(late,1000000).ToString(),now-1,now);
            Assert.That(client.ResetAcknowledged(reset),Is.False);Assert.That(client.NeutralHoldHealthy,Is.False);
            Assert.Throws<ControlFault>(()=>client.Pump());
            var after=client.ReadinessDiagnostic(reset);
            Assert.That((string)after["first_failure_code"],Is.EqualTo(code));Assert.That((string)after["first_failure_phase"],Is.EqualTo("health_exchange"));
            Assert.That(JToken.DeepEquals(after["exchange_failure"],failed),Is.True);
            lock(journal)Assert.That(journal.Count,Is.EqualTo(journalRows),"No replay, new reset or invented durable row after failure");
            Assert.That(ledger.Drain(QuitCoordinator.DefaultWorkerBudgetMs).Single().Outcome,Is.EqualTo("completed"));
        }
    }
}
