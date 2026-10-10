using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.StateSources;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    public sealed class PublicStateQueueTests
    {
        static readonly string[] Names=Enumerable.Range(0,43).Select(i=>"joint_"+i.ToString("00")).ToArray();
        readonly SceneRegistry registry=new SceneRegistry("station-01",new string('a',64),new string('b',64),Names,
            new Dictionary<string,string[]> { ["card"]=new[]{"card_face"} },new[]{"card"});
        readonly List<SourceEvent> events=new List<SourceEvent>();
        double now;LiveIsaacSource source;LiveSocketClient client;Action duringParse;
        string Raw(long seq,double at)=>JObject.FromObject(new {
            version=2,kind="state",source_kind="live",station_id="station-01",scene_sha256=new string('a',64),reset_snapshot_sha256=new string('b',64),
            session_id=new string('c',32),seq,host_monotonic_ns=((ulong)Math.Round(at*1e9)).ToString(),sim_time=at,sim_step=seq,
            joint_names=Names,joint_positions=new double[43],objects=new[]{new {id="card",position_m=new[]{0d,0d,0d},rotation_xyzw=new[]{0d,0d,0d,1d},visible=true,enabled=true,state=new{card_face=0}}}
        }).ToString(Formatting.None);
        [SetUp] public void Setup()
        {
            now=1;events.Clear();duringParse=null;source=new LiveIsaacSource(0,0);source.Event+=events.Add;
            client=new LiveSocketClient("ws://127.0.0.1:1/state",registry,source,null,()=>now,(raw,expected)=>
            {var frame=StateParser.Parse(raw,expected);duringParse?.Invoke();return frame;},false);
        }
        [TearDown] public void Cleanup()=>client.Dispose();
        [Test] public void ArrivalDuringParseUsesActualProcessingTimeWithoutChangingReceipt()
        {
            client.ReceiveRaw(Raw(0,.999),.999);
            duringParse=()=>{duringParse=null;now=1.02;client.ReceiveRaw(Raw(1,1.015),1.015);};
            client.Pump(1);
            Assert.That(source.Latest.Sequence,Is.EqualTo(1));
            Assert.That(source.LastReceivedMonoSeconds,Is.EqualTo(1.015));
            Assert.That(source.LocalProgressFresh(now),Is.True);
            Assert.That(source.SampleAgeSeconds,Is.EqualTo(.005).Within(1e-9));
            Assert.That(events.Any(x=>x.Code=="STATE_QUEUED_TOO_LONG"),Is.False);
        }
        [Test] public void ParsingDelayCannotAdmitAnExpiredArrival()
        {
            client.ReceiveRaw(Raw(0,.9),.9);duringParse=()=>now=1.151;
            client.Pump(1);
            Assert.That(source.Latest,Is.Null);
            Assert.That(source.LocalProgressFresh(now),Is.False);
            Assert.That(events.Any(x=>x.Code=="STATE_QUEUED_TOO_LONG"),Is.True);
        }
        [Test] public void ArrivalAfterCallerSnapshotBeforePumpIsNotReclocked()
        {
            client.ReceiveRaw(Raw(0,1.01),1.01);now=1.02;
            double renderedAt=client.Pump(1);
            Assert.That(source.Render(renderedAt),Is.SameAs(source.Latest));
            Assert.That(source.LastReceivedMonoSeconds,Is.EqualTo(1.01));
            Assert.That(source.SampleAgeSeconds,Is.EqualTo(.01).Within(1e-9));
        }
        [TestCase(.75,true)][TestCase(.749999,false)][TestCase(1.001,false)]
        public void RealQueueAgeBoundaryAndFutureReceiptRemainStrict(double received,bool accepted)
        {
            client.ReceiveRaw(Raw(0,received),received);client.Pump(now);
            Assert.That(source.Latest!=null,Is.EqualTo(accepted));
            Assert.That(source.LocalProgressFresh(now),Is.EqualTo(accepted));
            if(accepted)Assert.That(source.LastReceivedMonoSeconds,Is.EqualTo(received));
        }
        [TestCase(.99)][TestCase(double.NaN)][TestCase(double.PositiveInfinity)]
        public void InvalidProcessingClockCannotConsumeAsFresh(double badTime)
        {
            client.ReceiveRaw(Raw(0,.99),.99);duringParse=()=>now=badTime;
            Assert.That(Assert.Throws<StateFault>(()=>client.Pump(1)).Message,Is.EqualTo("HOST_CLOCK_REGRESSED"));
            Assert.That(source.Latest,Is.Null);
        }
        [Test] public void FailedParseRecordsActualFailureTimeWithoutGrant()
        {
            client.ReceiveRaw(Raw(0,.99),.99);duringParse=()=>{now=1.3;throw new InvalidOperationException("synthetic parser failure");};
            Assert.That(client.Pump(1),Is.EqualTo(1.3));
            Assert.That(events.Last().Code,Is.EqualTo("STATE_MALFORMED"));
            Assert.That(events.Last().ObservedMonoSeconds,Is.EqualTo(1.3));Assert.That(source.Latest,Is.Null);
        }
        [Test] public void MovingQueueRemainsBoundedAndFifo()
        {
            int parsed=0;client.ReceiveRaw(Raw(0,1),1);
            duringParse=()=>{parsed++;now+=.001;client.ReceiveRaw(Raw(parsed,now),now);};
            double end=client.Pump(1);Assert.That(parsed,Is.EqualTo(8));Assert.That(source.Latest.Sequence,Is.EqualTo(7));
            duringParse=null;client.Pump(end);Assert.That(source.Latest.Sequence,Is.EqualTo(8));
            Assert.That(events.Any(x=>x.Code=="STATE_SEQUENCE_GAP"),Is.False);
        }
        [Test] public void EpochFaultAndOverflowStillInvalidateWithoutRenewingReceipt()
        {
            client.ReceiveRaw(Raw(0,.9),.9,2);client.ReceiveRaw(Raw(10,.95),.95,1);client.ReceiveFault("STATE_TRANSPORT_DISCONNECTED",.99,2);
            client.Pump(1);Assert.That(source.Latest.Sequence,Is.Zero);Assert.That(source.LastReceivedMonoSeconds,Is.EqualTo(.9));
            Assert.That(source.LocalProgressFresh(1),Is.False);Assert.That(events.Last().Code,Is.EqualTo("STATE_TRANSPORT_DISCONNECTED"));
            for(int i=1;i<=9;i++)client.ReceiveRaw(Raw(i,.9+i*.001),.9+i*.001,2);
            client.Pump(1);Assert.That(client.DroppedMessages,Is.EqualTo(1));Assert.That(source.Latest.Sequence,Is.EqualTo(8));
            Assert.That(source.LocalProgressFresh(1),Is.False);Assert.That(events.Last().Code,Is.EqualTo("STATE_RECEIVE_QUEUE_OVERFLOW"));
        }
        [Test] public void DurableRestartObserverDelayIsIncludedInQueueAge()
        {
            source.Event+=x=>{if(x.Code=="STATE_TRANSPORT_RESTART")now=1.3;};
            client.ReceiveRaw(Raw(0,.99),.99);client.Pump(1);
            Assert.That(source.Latest,Is.Null);Assert.That(events.Last().Code,Is.EqualTo("STATE_QUEUED_TOO_LONG"));
            Assert.That(events.Last().ObservedMonoSeconds,Is.EqualTo(1.3));
        }
        [Test] public void EchoKeepsOriginalCorrelationAndReceiptTime()
        {
            client.Dispose();var qualified=new SourceClock(0,5,new string('d',64));source=new LiveIsaacSource(0,0,qualified);source.Event+=events.Add;
            client=new LiveSocketClient("ws://127.0.0.1:1/state",registry,source,qualified,()=>now,(raw,expected)=>StateParser.Parse(raw,expected),false);
            var pending=(System.Collections.Concurrent.ConcurrentDictionary<double,byte>)typeof(LiveSocketClient).GetField("echoes",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).GetValue(client);
            pending.TryAdd(.9,0);client.ReceiveRaw("{\"kind\":\"echo\",\"c0_s\":0.9,\"s1_ns\":\"900000000\",\"s2_ns\":\"900000000\"}",.9);
            client.ReceiveRaw(Raw(0,.99),.99);now=1.1;client.Pump(1);
            Assert.That(source.SourceFresh,Is.True);Assert.That(source.LastReceivedMonoSeconds,Is.EqualTo(.99));Assert.That(pending.Count,Is.Zero);
            client.ReceiveRaw("{\"kind\":\"echo\",\"c0_s\":0.9,\"s1_ns\":\"900000000\",\"s2_ns\":\"900000000\"}",1.1);client.Pump(1.1);
            Assert.That(source.LocalProgressFresh(1.1),Is.False);Assert.That(events.Last().Code,Is.EqualTo("STATE_ECHO_CORRELATION"));
        }
        // Attempt simulation-test-B-V1-019-echo-009: during a 4.8 s main-thread
        // stall no Pump consumed outstanding echoes; the clear-at-eight rule
        // discarded echoes whose replies were still queued, so the first reply
        // processed after the stall raised STATE_ECHO_CORRELATION.
        [Test] public void EchoReplyQueuedThroughAMainThreadStallStillCorrelates()
        {
            var pending=(System.Collections.Concurrent.ConcurrentDictionary<double,byte>)typeof(LiveSocketClient).GetField("echoes",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).GetValue(client);
            for(int i=0;i<12;i++)client.RememberEcho(100+i); // one per second, no Pump
            Assert.That(pending.Count,Is.EqualTo(12),"Outstanding echoes survive a stall");
            client.ReceiveRaw(Raw(0,.99),.99);client.ReceiveRaw("{\"kind\":\"echo\",\"c0_s\":100,\"s1_ns\":\"1\",\"s2_ns\":\"2\"}",.995);client.Pump(1);
            Assert.That(events.Any(x=>x.Code=="STATE_ECHO_CORRELATION"),Is.False);Assert.That(source.LocalProgressFresh(1),Is.True);Assert.That(pending.ContainsKey(100),Is.False);
        }
        [Test] public void ExpiredOrEvictedEchoStaysUncorrelatedAndMemoryIsBounded()
        {
            var pending=(System.Collections.Concurrent.ConcurrentDictionary<double,byte>)typeof(LiveSocketClient).GetField("echoes",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic).GetValue(client);
            client.RememberEcho(100);client.RememberEcho(101);client.RememberEcho(100+LiveSocketClient.EchoRetentionSeconds+0.5);
            Assert.That(pending.Keys,Is.EquivalentTo(new[]{101,100+LiveSocketClient.EchoRetentionSeconds+0.5}),"Only echoes older than the retention are expired");
            for(int i=0;i<LiveSocketClient.EchoCapacity+3;i++)client.RememberEcho(200+i*.1); // all inside the retention window
            Assert.That(pending.Count,Is.EqualTo(LiveSocketClient.EchoCapacity));Assert.That(pending.Keys.Min(),Is.EqualTo(200+3*.1),"Capacity evicts only the oldest");
            client.ReceiveRaw(Raw(0,.99),.99);client.ReceiveRaw("{\"kind\":\"echo\",\"c0_s\":100,\"s1_ns\":\"1\",\"s2_ns\":\"2\"}",.995);client.Pump(1);
            Assert.That(events.Last().Code,Is.EqualTo("STATE_ECHO_CORRELATION"));Assert.That(source.LocalProgressFresh(1),Is.False);
        }
    }
}
