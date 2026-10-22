using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.WebSockets;
using System.Threading;
using System.Threading.Tasks;
using AcousticVocab.Foundation;
using AcousticVocab.StateIntegration;
using AcousticVocab.StateSources;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    // Quit-time worker bounds (#150). Scripted sockets drive the real client
    // workers; nothing here opens a network connection or claims native timing.
    public sealed class ShutdownWorkerTests
    {
        const string Pin="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        const int Bound=QuitCoordinator.DefaultWorkerBudgetMs;
        // Socket that is open until cancelled, aborted, or (when hung) never.
        sealed class BlockingSocket:WebSocket
        {
            internal bool IgnoreCancellation,IgnoreAbort;internal int Sends;
            readonly TaskCompletionSource<bool> aborted=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            internal readonly TaskCompletionSource<bool> Release=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            internal readonly ManualResetEventSlim Receiving=new ManualResetEventSlim();
            public override WebSocketCloseStatus? CloseStatus=>null;public override string CloseStatusDescription=>null;public override WebSocketState State=>WebSocketState.Open;public override string SubProtocol=>null;
            public override void Abort(){if(!IgnoreAbort)aborted.TrySetResult(true);}
            public override void Dispose(){}
            public override Task CloseAsync(WebSocketCloseStatus s,string d,CancellationToken c)=>Task.CompletedTask;public override Task CloseOutputAsync(WebSocketCloseStatus s,string d,CancellationToken c)=>Task.CompletedTask;
            public override Task SendAsync(ArraySegment<byte> b,WebSocketMessageType t,bool end,CancellationToken token){Interlocked.Increment(ref Sends);return Task.CompletedTask;}
            public override async Task<WebSocketReceiveResult> ReceiveAsync(ArraySegment<byte> buffer,CancellationToken token)
            {
                Receiving.Set();
                var cancelled=new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                using(IgnoreCancellation?default(CancellationTokenRegistration):token.Register(()=>cancelled.TrySetResult(true)))
                {
                    var first=await Task.WhenAny(cancelled.Task,aborted.Task,Release.Task);
                    if(first==cancelled.Task)throw new OperationCanceledException(token);
                    if(first==aborted.Task)throw new WebSocketException("scripted abort");
                    throw new WebSocketException("scripted release");
                }
            }
        }
        static PrivateModeResetClient Control(WebSocket socket,WorkerLedger ledger)=>
            new PrivateModeResetClient("ws://127.0.0.1:1/commands",Pin,"teaching",_=>{},()=>Stopwatch.GetTimestamp()*1000.0/Stopwatch.Frequency,()=>socket,ledger);
        static LiveSocketClient State(WebSocket socket,WorkerLedger ledger)
        {
            var registry=new SceneRegistry("station-01",new string('a',64),new string('b',64),Enumerable.Range(0,43).Select(i=>"joint_"+i.ToString("00")),
                new Dictionary<string,string[]>{["card"]=new[]{"card_face"}},new[]{"card"});
            return new LiveSocketClient("ws://127.0.0.1:1/state",registry,new LiveIsaacSource(0,0),null,()=>LiveSocketClient.Now,(raw,expected)=>StateParser.Parse(raw,expected),()=>socket,ledger);
        }

        [Test]public void LedgerRecordsEveryOutcomeAndSharesOneTotalBound()
        {
            var ledger=new WorkerLedger();var never=new TaskCompletionSource<bool>();var alsoNever=new TaskCompletionSource<bool>();
            ledger.Track("finished",Task.CompletedTask);ledger.Track("faulted",Task.FromException(new IOException("private detail")));
            ledger.Track("cancelled",Task.FromCanceled(new CancellationToken(true)));ledger.Track("hung_one",never.Task);ledger.Track("hung_two",alsoNever.Task);
            var watch=Stopwatch.StartNew();var outcomes=ledger.Drain(120);watch.Stop();
            Assert.That(outcomes.Select(x=>x.Name+":"+x.Outcome),Is.EqualTo(new[]{"finished:completed","faulted:faulted","cancelled:cancelled","hung_one:timed_out","hung_two:timed_out"}));
            Assert.That(outcomes[1].FaultType,Is.EqualTo("IOException"));Assert.That(outcomes[1].ToJson().ToString(),Does.Not.Contain("private detail"));
            Assert.That(watch.Elapsed.TotalMilliseconds,Is.LessThan(120+250),"Two hung workers share one total budget instead of one budget each");
            Assert.That(ledger.Count,Is.Zero);Assert.That(ledger.Drain(Bound),Is.Empty,"Drained workers are not waited for twice");
            never.SetResult(true);alsoNever.SetResult(true);
        }
        [Test]public void QuitCoordinatorRunsEveryStageOnceAndRecordsFaultsAndTimeouts()
        {
            var ledger=new WorkerLedger();var crumbs=new List<(string Stage,JObject Detail)>();var ran=new List<string>();var never=new TaskCompletionSource<bool>();
            ledger.Track("hung",never.Task);ledger.Track("faulted",Task.FromException(new WebSocketException()));
            var quit=new QuitCoordinator(ledger,100,(s,d)=>crumbs.Add((s,d)));
            quit.Add("first",()=>{ran.Add("first");throw new ControlFault("CONTROL_SYNTHETIC_CLOSE");});quit.Add("second",()=>ran.Add("second"));
            var watch=Stopwatch.StartNew();var outcomes=quit.Run("test_quit");watch.Stop();
            Assert.That(ran,Is.EqualTo(new[]{"first","second"}),"A failed stage cannot skip later cleanup");
            Assert.That(watch.Elapsed.TotalMilliseconds,Is.LessThan(100+250));
            Assert.That(crumbs.Select(x=>x.Stage),Is.EqualTo(new[]{"quit_begin","stage_begin","stage_end","stage_begin","stage_end","worker_drain_begin","worker_outcome","worker_outcome","quit_ready"}));
            Assert.That((bool)crumbs[2].Detail["succeeded"],Is.False);Assert.That((string)crumbs[2].Detail["fault"],Is.EqualTo("CONTROL_SYNTHETIC_CLOSE"));Assert.That((bool)crumbs[4].Detail["succeeded"],Is.True);
            Assert.That(outcomes.Select(x=>x.Outcome),Is.EqualTo(new[]{"timed_out","faulted"}));
            Assert.That((string)crumbs[6].Detail["outcome"],Is.EqualTo("timed_out"));Assert.That((string)crumbs[7].Detail["fault_type"],Is.EqualTo("WebSocketException"));
            Assert.That((int)crumbs[8].Detail["timed_out"],Is.EqualTo(1));Assert.That((int)crumbs[8].Detail["faulted"],Is.EqualTo(1));
            Assert.That(quit.Run("again"),Is.Empty);Assert.That(ran.Count,Is.EqualTo(2),"Quit runs once");
            never.SetResult(true);
        }
        [Test]public void DisposedControlWorkerStopsWithinQuitBound()
        {
            var ledger=new WorkerLedger();var socket=new BlockingSocket();var client=Control(socket,ledger);
            Assert.That(socket.Receiving.Wait(2000),Is.True,"The real worker reached a pending receive");
            client.Dispose();var watch=Stopwatch.StartNew();var outcomes=ledger.Drain(Bound);
            Assert.That(outcomes.Single().Name,Is.EqualTo("private_control_socket"));Assert.That(outcomes.Single().Outcome,Is.EqualTo("completed"));
            Assert.That(watch.Elapsed.TotalMilliseconds,Is.LessThan(Bound));Assert.That(client.NeutralHoldHealthy,Is.False);
        }
        [Test]public void AbortReleasesControlWorkerWhoseIoIgnoresCancellation()
        {
            var ledger=new WorkerLedger();var socket=new BlockingSocket{IgnoreCancellation=true};var client=Control(socket,ledger);
            Assert.That(socket.Receiving.Wait(2000),Is.True);client.Dispose();
            Assert.That(ledger.Drain(Bound).Single().Outcome,Is.EqualTo("completed"));
        }
        [Test]public void HungControlWorkerIsRecordedAsTimedOutWithoutExtendingTheBound()
        {
            var ledger=new WorkerLedger();var socket=new BlockingSocket{IgnoreCancellation=true,IgnoreAbort=true};var client=Control(socket,ledger);
            Assert.That(socket.Receiving.Wait(2000),Is.True);client.Dispose();var worker=client.Worker;
            var watch=Stopwatch.StartNew();var outcome=ledger.Drain(100).Single();watch.Stop();
            Assert.That(outcome.Outcome,Is.EqualTo("timed_out"));Assert.That(watch.Elapsed.TotalMilliseconds,Is.LessThan(100+250));
            socket.Release.SetResult(true);Assert.That(worker.Wait(2000),Is.True,"Released scripted IO lets the worker end after the test");
        }
        [Test]public void DisposedPublicStateWorkerStopsWithinQuitBound()
        {
            var ledger=new WorkerLedger();var socket=new BlockingSocket();var client=State(socket,ledger);
            Assert.That(socket.Receiving.Wait(2000),Is.True,"The real worker connected and awaits a frame");
            client.Dispose();var watch=Stopwatch.StartNew();var outcome=ledger.Drain(Bound).Single();
            Assert.That(outcome.Name,Is.EqualTo("public_state_socket"));Assert.That(outcome.Outcome,Is.EqualTo("completed"));Assert.That(watch.Elapsed.TotalMilliseconds,Is.LessThan(Bound));
            Assert.That(socket.Sends,Is.GreaterThanOrEqualTo(1),"The echo sender also ran on the scripted socket");
        }
        [Test]public void AbortSurfacedAsSocketErrorStillEndsPublicStateWorkerCleanly()
        {
            var ledger=new WorkerLedger();var socket=new BlockingSocket{IgnoreCancellation=true};var client=State(socket,ledger);
            Assert.That(socket.Receiving.Wait(2000),Is.True);client.Dispose();
            Assert.That(ledger.Drain(Bound).Single().Outcome,Is.EqualTo("completed"),"Owner disposal is not reported as a worker fault");
        }
        [Test]public void JournalCloseRecordsWriterOutcomeAndLedgerSeesWriterEnd()
        {
            string directory=Path.Combine(Path.GetTempPath(),"av-journal-close-"+Guid.NewGuid().ToString("N"));var ledger=new WorkerLedger();
            try
            {
                var registry=new SceneRegistry("station-01",new string('a',64),new string('b',64),Enumerable.Range(0,43).Select(i=>"joint_"+i.ToString("00")),
                    new Dictionary<string,string[]>{["card"]=new[]{"card_face"}},new[]{"card"});
                var journal=new StateSourceJournal(directory,new JObject{["build_id"]="test"},registry,"live",ledger);
                journal.Dispose();
                Assert.That((bool)journal.CloseOutcome["writer_stopped"],Is.True);Assert.That((bool)journal.CloseOutcome["writer_failed"],Is.False);
                Assert.That((int)journal.CloseOutcome["join_timeout_ms"],Is.EqualTo(StateSourceJournal.JoinTimeoutMs));
                var last=JObject.Parse(File.ReadAllLines(Directory.GetFiles(directory,"foundation-*.jsonl").Single()).Last());
                Assert.That((string)last["event"],Is.EqualTo("state_source_journal_closed"));Assert.That((bool)last["writer_stopped"],Is.True);
                var outcome=ledger.Drain(Bound).Single();Assert.That(outcome.Name,Is.EqualTo("state_source_journal_writer"));Assert.That(outcome.Outcome,Is.EqualTo("completed"));
            }
            finally{if(Directory.Exists(directory))Directory.Delete(directory,true);}
        }
        [Test]public void FailedSocketFactoryEndsControlWorkerWithRetainedCause()
        {
            var ledger=new WorkerLedger();
            var client=new PrivateModeResetClient("ws://127.0.0.1:1/commands",Pin,"teaching",_=>{},()=>Stopwatch.GetTimestamp()*1000.0/Stopwatch.Frequency,()=>throw new WebSocketException("synthetic connect refusal"),ledger);
            Assert.That(client.Worker.Wait(2000),Is.True);Assert.That(ledger.Drain(Bound).Single().Outcome,Is.EqualTo("completed"));
            var detail=client.ReadinessDiagnostic(null);Assert.That((string)detail["first_failure_code"],Is.EqualTo("CONTROL_SOCKET_ERROR"));Assert.That((string)detail["first_failure_phase"],Is.EqualTo("connect"));
            Assert.That(client.NeutralHoldHealthy,Is.False);client.Dispose();
        }
    }
}
