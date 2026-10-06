using System;
using System.Collections.Generic;
using System.Net.WebSockets;
using System.Text;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using AcousticVocab.StateIntegration;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    public sealed class PrivateHealthProbeTests
    {
        const string Session="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",Id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        JObject Health()=>new JObject{["control_session_id"]=Session,["mode"]="teaching",["paused"]=false,["stopped"]=false,["fault"]=null,["demo_active"]=false,["publisher_ready"]=true,["neutral_verification_age_ms"]=10,["publisher_age_ms"]=15,["health_sample_host_mono_ms"]=1,["exposure_ready"]=false,["public_stream_recovered"]=false};
        JObject Reply()=>new JObject{["version"]=1,["kind"]="private_health_reply",["control_session_id"]=Session,["request_id"]=Id,["accepted"]=true,["reason"]="HEALTH",["health"]=Health()};
        [Test]public void ProbeUsesSeparateClosedTransportNamespaceAndRetainsPayload()
        {
            JObject request=PrivateHealthProbe.Request(Session,Id);
            Assert.That(request.Count,Is.EqualTo(4));Assert.That((string)request["kind"],Is.EqualTo("private_health_probe"));Assert.That(request["command"],Is.Null);
            JObject reply=Reply();Assert.That(PrivateHealthProbe.Payload(reply,Session,Id),Is.SameAs(reply["health"]));
        }
        [TestCase("unknown")][TestCase("missing")][TestCase("float_version")][TestCase("bool_version")][TestCase("wrong_kind")][TestCase("wrong_session")][TestCase("old_request")][TestCase("null_request")][TestCase("string_accepted")][TestCase("unknown_reason")][TestCase("error_with_payload")][TestCase("success_null")]
        public void InvalidEnvelopeLatchesActualReceiveQueueWithoutHealth(string mutation)
        {
            var reply=Reply();
            switch(mutation)
            {
                case "unknown":reply["extra"]=true;break;case "missing":reply.Remove("reason");break;
                case "float_version":reply["version"]=1.0;break;case "bool_version":reply["version"]=true;break;
                case "wrong_kind":reply["kind"]="private_reply";break;case "wrong_session":reply["control_session_id"]=Id;break;
                case "old_request":reply["request_id"]=Session;break;case "null_request":reply["request_id"]=null;break;
                case "string_accepted":reply["accepted"]="true";break;case "unknown_reason":reply["reason"]="OK";break;
                case "error_with_payload":reply["accepted"]=false;reply["reason"]="MALFORMED_PROBE";break;case "success_null":reply["health"]=null;break;
            }
            using var client=new PrivateModeResetClient("ws://127.0.0.1:1/commands",Session,"teaching",_=>Assert.Fail("No command persistence expected"),()=>20,false);
            client.ReceiveHealthProbe(Id,reply.ToString(),0,20);Assert.Throws<ControlFault>(()=>client.Pump());Assert.That(client.NeutralHoldHealthy,Is.False);Assert.That((bool)client.ReadinessDiagnostic(null)["failed"],Is.True);
        }
        [TestCase("MALFORMED_PROBE")][TestCase("CONTROL_SESSION_MISMATCH")][TestCase("HEALTH_SESSION_CHANGED")]
        public void ExplicitRejectionCannotBecomeAHealthObservation(string reason)
        {var reply=Reply();reply["accepted"]=false;reply["reason"]=reason;reply["health"]=null;Assert.That(Assert.Throws<ControlFault>(()=>PrivateHealthProbe.Payload(reply,Session,Id)).Code,Is.EqualTo("CONTROL_HEALTH_PROBE_REJECTED"));}
        [Test]public void DuplicateEnvelopeKeyIsRejectedBeforePayload()
        {
            string raw=Reply().ToString().Replace("\"version\": 1", "\"version\": 1, \"version\": 1");
            using var client=new PrivateModeResetClient("ws://127.0.0.1:1/commands",Session,"teaching",_=>{},()=>20,false);
            client.ReceiveHealthProbe(Id,raw,0,20);Assert.Catch(()=>client.Pump());Assert.That(client.NeutralHoldHealthy,Is.False);
        }
        [Test]public void OldCorrelatedProbeCannotStandInForCurrentRequest()
        {Assert.Throws<ControlFault>(()=>PrivateHealthProbe.Payload(Reply(),Session,Guid.NewGuid().ToString("N")));}

        sealed class ScriptedSocket:WebSocket
        {
            internal readonly Queue<byte[]> Parts=new Queue<byte[]>();internal readonly List<string> Calls=new List<string>();
            internal WebSocketMessageType Type=WebSocketMessageType.Text;internal string Block;internal Action OnReceive,OnSend;internal int BlockReceiveAt,ReceiveCalls;
            public override WebSocketCloseStatus? CloseStatus=>null;public override string CloseStatusDescription=>null;public override WebSocketState State=>WebSocketState.Open;public override string SubProtocol=>null;
            public override void Abort(){}public override void Dispose(){}public override Task CloseAsync(WebSocketCloseStatus s,string d,CancellationToken c)=>Task.CompletedTask;public override Task CloseOutputAsync(WebSocketCloseStatus s,string d,CancellationToken c)=>Task.CompletedTask;
            public override async Task SendAsync(ArraySegment<byte> b,WebSocketMessageType t,bool end,CancellationToken token)
            {Calls.Add("send");Assert.That(t,Is.EqualTo(WebSocketMessageType.Text));Assert.That(end,Is.True);OnSend?.Invoke();if(Block=="send")await Task.Delay(Timeout.Infinite,token);}
            public override async Task<WebSocketReceiveResult> ReceiveAsync(ArraySegment<byte> buffer,CancellationToken token)
            {
                Calls.Add("receive");ReceiveCalls++;if(Block=="receive"||(BlockReceiveAt>0&&ReceiveCalls>=BlockReceiveAt))await Task.Delay(Timeout.Infinite,token);OnReceive?.Invoke();byte[] part=Parts.Dequeue();Array.Copy(part,0,buffer.Array,buffer.Offset,part.Length);return new WebSocketReceiveResult(part.Length,Type,Parts.Count==0);
            }
        }
        [Test]public async Task FragmentedReplyRetainsFullRoundTripBeforeQueueing()
        {
            double clock=100;using var socket=new ScriptedSocket{OnReceive=()=>clock+=20};socket.Parts.Enqueue(Encoding.UTF8.GetBytes("{\"ok\":"));socket.Parts.Enqueue(Encoding.UTF8.GetBytes("true}"));
            var reply=await PrivateControlExchange.Run(socket,"{}",200,()=>clock,CancellationToken.None);
            Assert.That(reply.Raw,Is.EqualTo("{\"ok\":true}"));Assert.That(reply.Sent,Is.EqualTo(100));Assert.That(reply.Received,Is.EqualTo(140));Assert.That(socket.Calls,Is.EqualTo(new[]{"send","receive","receive"}));
        }
        [TestCase("send")][TestCase("receive")]
        public void DeadlineSpansSendAndAllFragments(string block)
        {
            using var socket=new ScriptedSocket{Block=block};socket.Parts.Enqueue(Encoding.UTF8.GetBytes("{}"));
            Assert.ThrowsAsync<TaskCanceledException>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",200,()=>0,CancellationToken.None)));
            Assert.That(socket.Calls.Count,Is.EqualTo(block=="send"?1:2));
        }
        [TestCase(WebSocketMessageType.Binary)][TestCase(WebSocketMessageType.Close)]
        public void NonTextReplyCannotReachHealth(WebSocketMessageType type)
        {using var socket=new ScriptedSocket{Type=type};socket.Parts.Enqueue(new byte[0]);Assert.ThrowsAsync<ControlFault>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",200,()=>0,CancellationToken.None)));}
        [Test]public void OversizedFragmentedReplyCannotReachHealth()
        {using var socket=new ScriptedSocket();for(int i=0;i<5;i++)socket.Parts.Enqueue(new byte[4096]);Assert.ThrowsAsync<ControlFault>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",200,()=>0,CancellationToken.None)));}
        [Test]public void LateCompletedReplyCannotBypassDeadlineWhenCancellationDeliveryIsDelayed()
        {double clock=0;using var socket=new ScriptedSocket{OnReceive=()=>clock=200.001};socket.Parts.Enqueue(Encoding.UTF8.GetBytes("{}"));Assert.That(Assert.ThrowsAsync<ControlFault>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",200,()=>clock,CancellationToken.None))).Code,Is.EqualTo("CONTROL_TRANSPORT_DEADLINE"));}
        [TestCase(3000,true)][TestCase(3000.001,false)]
        public async Task CommandExchangeStillEnforcesItsOriginalDeadline(double elapsed,bool permitted)
        {
            double clock=0;using var socket=new ScriptedSocket{OnReceive=()=>clock=elapsed};socket.Parts.Enqueue(Encoding.UTF8.GetBytes("{}"));
            if(permitted){var reply=await PrivateControlExchange.Run(socket,"{}",ControlHealthGate.CommandDeadlineMs,()=>clock,CancellationToken.None);Assert.That(reply.Received-reply.Sent,Is.EqualTo(elapsed));}
            else Assert.That(Assert.ThrowsAsync<ControlFault>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",ControlHealthGate.CommandDeadlineMs,()=>clock,CancellationToken.None))).Code,Is.EqualTo("CONTROL_TRANSPORT_DEADLINE"));
        }
        [Test]public void InvalidUtf8ReplyIsRefused()
        {using var socket=new ScriptedSocket();socket.Parts.Enqueue(new byte[]{0xff});Assert.ThrowsAsync<DecoderFallbackException>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",200,()=>0,CancellationToken.None)));}
        [TestCase("send")][TestCase("receive")]
        public void CancelledExchangeRetainsAwaitStageAndLinkedTokenState(string stage)
        {
            using var socket=new ScriptedSocket{Block=stage};var clock=Stopwatch.StartNew();PrivateControlExchange.Failure trace=null;
            Assert.ThrowsAsync<TaskCanceledException>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"private request payload",200,()=>clock.Elapsed.TotalMilliseconds,CancellationToken.None,x=>trace=x)));
            Assert.That(trace.Stage,Is.EqualTo(stage));Assert.That(trace.TimeoutMs,Is.EqualTo(200));Assert.That(trace.TimeoutCancelled,Is.True);Assert.That(trace.LifetimeCancelled,Is.False);
            Assert.That(trace.Observed,Is.GreaterThanOrEqualTo(trace.Sent));Assert.That(trace.Fragments,Is.Zero);Assert.That(trace.Bytes,Is.Zero);Assert.That(trace.MessageCompleted,Is.EqualTo(-1));
            Assert.That(trace.SendCompleted>=0,Is.EqualTo(stage=="receive"));Assert.That(trace.ReceiveStarted>=0,Is.EqualTo(stage=="receive"));
            var json=trace.ToJson("health_probe",Id);Assert.That((string)json["request_id"],Is.EqualTo(Id));Assert.That(json.ToString(),Does.Not.Contain("private request payload"));Assert.That(json["first_fragment_local_mono_ms"].Type,Is.EqualTo(JTokenType.Null));
        }
        [Test]public void OwnerCancellationIsRetainedWithoutClaimingItWasTheDeadline()
        {
            using var lifetime=new CancellationTokenSource();using var socket=new ScriptedSocket{Block="send",OnSend=lifetime.Cancel};PrivateControlExchange.Failure trace=null;
            Assert.ThrowsAsync<TaskCanceledException>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",200,()=>50,lifetime.Token,x=>trace=x)));
            Assert.That(trace.Stage,Is.EqualTo("send"));Assert.That(trace.LifetimeCancelled,Is.True);Assert.That(trace.TimeoutCancelled,Is.True,"The timeout token is linked; both true does not distinguish which fired first");Assert.That(trace.SendCompleted,Is.EqualTo(-1));
        }
        [Test]public void PartialReplyRetainsOnlyBoundedCountsAndFragmentTimes()
        {
            using var socket=new ScriptedSocket{BlockReceiveAt=2};socket.Parts.Enqueue(Encoding.UTF8.GetBytes("{\"x\":"));socket.Parts.Enqueue(Encoding.UTF8.GetBytes("1}"));
            var clock=Stopwatch.StartNew();PrivateControlExchange.Failure trace=null;
            Assert.ThrowsAsync<TaskCanceledException>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",200,()=>clock.Elapsed.TotalMilliseconds,CancellationToken.None,x=>trace=x)));
            Assert.That(trace.Stage,Is.EqualTo("receive"));Assert.That(trace.Fragments,Is.EqualTo(1));Assert.That(trace.Bytes,Is.EqualTo(5));
            Assert.That(trace.FirstFragment,Is.GreaterThanOrEqualTo(trace.SendCompleted));Assert.That(trace.LastFragment,Is.EqualTo(trace.FirstFragment));Assert.That(trace.ReceiveStarted,Is.GreaterThanOrEqualTo(trace.LastFragment));Assert.That(trace.MessageCompleted,Is.EqualTo(-1));
        }
        [Test]public void FullLateMessageRetainsValidationStageWithoutInventedCancellation()
        {
            double clock=0;using var socket=new ScriptedSocket{OnReceive=()=>clock=200.001};socket.Parts.Enqueue(Encoding.UTF8.GetBytes("{}"));PrivateControlExchange.Failure trace=null;
            Assert.That(Assert.ThrowsAsync<ControlFault>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",200,()=>clock,CancellationToken.None,x=>trace=x))).Code,Is.EqualTo("CONTROL_TRANSPORT_DEADLINE"));
            Assert.That(trace.Stage,Is.EqualTo("validate"));Assert.That(trace.MessageCompleted,Is.EqualTo(200.001));Assert.That(trace.Observed,Is.EqualTo(200.001));Assert.That(trace.Fragments,Is.EqualTo(1));Assert.That(trace.Bytes,Is.EqualTo(2));Assert.That(trace.TimeoutCancelled,Is.False);Assert.That(trace.LifetimeCancelled,Is.False);
        }
        [Test]public void FailedDiagnosticObserverCannotReplaceOriginalTransportException()
        {
            var original=new IOException("private error detail");using var socket=new ScriptedSocket{OnReceive=()=>throw original};socket.Parts.Enqueue(Encoding.UTF8.GetBytes("{}"));int calls=0;
            var actual=Assert.ThrowsAsync<IOException>(()=>Task.Run(()=>PrivateControlExchange.Run(socket,"{}",200,()=>10,CancellationToken.None,_=>{calls++;throw new InvalidOperationException("observer failure");})));
            Assert.That(actual,Is.SameAs(original));Assert.That(calls,Is.EqualTo(1));Assert.That(socket.Calls,Is.EqualTo(new[]{"send","receive"}));
        }
        [Test]public async Task SuccessfulExchangeNeverInvokesFailureObserverOrChangesReceipt()
        {
            double clock=100;using var socket=new ScriptedSocket{OnReceive=()=>clock+=20};socket.Parts.Enqueue(Encoding.UTF8.GetBytes("{\"ok\":"));socket.Parts.Enqueue(Encoding.UTF8.GetBytes("true}"));int calls=0;
            var reply=await PrivateControlExchange.Run(socket,"{}",200,()=>clock,CancellationToken.None,_=>calls++);
            Assert.That(calls,Is.Zero);Assert.That(reply.Sent,Is.EqualTo(100));Assert.That(reply.Received,Is.EqualTo(140));Assert.That(reply.Raw,Is.EqualTo("{\"ok\":true}"));
        }
    }
}
