using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using AcousticVocab.DataLogging;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine;
namespace AcousticVocab.SessionIntegration.Tests
{
    // Synthetic delivery outcomes and synthetic loopback captures on the real
    // engine, durable journal and audio adapter, joined in the order
    // SessionIntegrationOwner uses. They exercise the evidence rule only; no
    // outcome or capture here is acoustic evidence.
    public sealed class NoOnsetReporterTests
    {
        static T New<T>(params object[] a)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,a,null);
        static readonly string Hash=new string('a',64),Package=new string('b',64),Wave=new string('c',64),Pcm=new string('d',64),Captured=new string('e',64);
        static SlotItem Item(string id,int plays=1,bool heldout=false)=>New<SlotItem>(id,heldout?"novel":"trained",heldout?"K-a1-r2":"K-a1-r1",null,null,"protected",heldout,14,plays,1);
        static ScheduleBlock Block(string name,params SlotItem[] items)=>New<ScheduleBlock>(name,items);
        sealed class Clock:ISessionClock{internal double Time=1000;public double NowMs=>Time;}
        // A synthetic loopback analysis keyed by audio request.
        sealed class Route:ILoopbackObservationSource
        {
            internal readonly Dictionary<string,LoopbackObservation> Captures=new Dictionary<string,LoopbackObservation>(StringComparer.Ordinal);
            internal bool Fail;internal int Calls;
            public LoopbackObservation Find(string id){Calls++;if(Fail)throw new InvalidOperationException("capture failed");return Captures.TryGetValue(id,out var o)?o:null;}
        }
        sealed class Content:ISlotContent
        {
            readonly Fixture owner;bool reset;internal SlotContext Context;
            internal Content(Fixture owner){this.owner=owner;}
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete=>reset;
            public void Prepare(SlotContext c){Context=c;}
            public void RequestCue(SlotContext c,INovelSlotAuthorization permit){if(permit!=null)Assert.That(permit.TryConsume(Package,c.Item.ContentId),Is.True);owner.Request(c);}
            public void OpenResponse(SlotContext c){if(!owner.Silent.Contains(c.Item.TrialId))owner.Deliver(c,"AUDIO_ONSET_ESTIMATED",512,1);}
            public void CloseResponse(SlotContext c){if(!owner.Silent.Contains(c.Item.TrialId))owner.Deliver(c,"AUDIO_PLAYBACK_COMPLETED",48000,94);}
            public void RequestReset(SlotContext c){reset=true;}
            public void Interrupt(string code){}
        }
        sealed class Fixture:ISlotContentFactory,IDisposable
        {
            internal readonly Clock Clock=new Clock();
            internal readonly string Folder=Path.Combine(Environment.CurrentDirectory,".local","no-onset-tests",Guid.NewGuid().ToString("N"));
            internal readonly DataIdentity Identity=new DataIdentity(new string('1',32),"synthetic-person","DEMO","station-01","engineering",Hash);
            internal readonly DataJournal Data;internal readonly FixedSlotEngine Engine;internal readonly NoOnsetReporter Reporter;internal readonly Route Loopback;
            internal readonly List<Content> Made=new List<Content>();internal readonly HashSet<string> Silent=new HashSet<string>(StringComparer.Ordinal);
            readonly GameObject host;readonly AudioDataAdapter audio;
            readonly Dictionary<string,AudioRequestContext> bound=new Dictionary<string,AudioRequestContext>(StringComparer.Ordinal);
            // A null route is the default station today: no loopback source.
            internal Fixture(Route loopback,params ScheduleBlock[] blocks)
            {
                Loopback=loopback;
                Data=new DataJournal(Folder,Identity,new string('2',32),()=>Clock.Time);
                host=new GameObject("Synthetic no-onset audio");host.SetActive(false);
                audio=new AudioDataAdapter(Data,host.AddComponent<AudioPlayer>(),e=>bound.TryGetValue(e.AudioId,out var c)?c:null,subscribeToPlayer:false);
                Engine=new FixedSlotEngine(New<VisitSchedule>(Hash,Package,"synthetic","DEMO",true,blocks,"A","S",null),Clock,new SessionDataJournal(Data),this);
                Reporter=loopback==null?new NoOnsetReporter(Engine,Data):new NoOnsetReporter(Engine,Data,loopback);
            }
            public ISlotContent Create(SlotItem item){var c=new Content(this);Made.Add(c);return c;}
            // Owner order: bind before scheduling; every event is durable before
            // the reporter sees it.
            internal void Request(SlotContext c)
            {
                string id=c.AudioRequestIds[0];bound.Add(id,new AudioRequestContext(new EventContext(c.OpportunityId,c.Item.TrialId,id),id,Wave));
                Reporter.Bind(c,0);Assert.That(Persist(Event(c,"AUDIO_REQUESTED",0,0,null)),Is.False);
            }
            internal bool Deliver(SlotContext c,string code,long samples,long callbacks)=>Persist(Event(c,code,samples,callbacks,callbacks>0?(double?)5:null));
            internal bool Persist(AudioPlaybackEvent e){audio.Record(e);return Reporter.Observe(e);}
            internal AudioPlaybackEvent Event(SlotContext c,string code,long samples,long callbacks,double? firstCallback)
            {
                double onset=c.OnsetMonoMs/1000;var timing=New<AudioScheduleTiming>(onset-.75,onset,5.0,(double?)onset,(double?)5,(double?)0);
                return New<AudioPlaybackEvent>(code,c.AudioRequestIds[0],Pcm,null,null,timing,Clock.Time/1000,samples,callbacks,firstCallback);
            }
            // A capture of the request from just before its hand-off (Event uses
            // onset - 0.75 s) to just after the current time, when the outcome is
            // delivered. Shifts move either edge.
            internal LoopbackObservation Capture(SlotContext c,double peak=-90,double rms=-100,string request=null,double startShift=0,double endShift=0)
            {
                double handoff=c.OnsetMonoMs/1000-.75,now=Clock.Time/1000;
                var capture=new LoopbackObservation(request??c.AudioRequestIds[0],"loopback-in-1",handoff-.25+startShift,now+.25+endShift,peak,rms,Captured);
                Loopback.Captures[c.AudioRequestIds[0]]=capture;return capture;
            }
            internal void Start(){Engine.ConfirmResume();Engine.Tick();}
            internal void At(double ms){Clock.Time=ms;Engine.Tick();}
            internal void Run(double until=120000){while(Clock.Time<until&&Engine.Status==SessionState.Running){Clock.Time+=50;Engine.Tick();}}
            internal List<SessionRecord> Session=>new SessionDataJournal(Data).Records.ToList();
            internal List<DataRecord> Rows(string kind)=>Data.Records.Where(r=>r.Kind==kind).ToList();
            internal DerivedTables Derive(){Data.Dispose();return DataDeriver.Derive(DataJournal.Verify(Folder,Identity),Identity);}
            public void Dispose()
            {
                try{Data.Dispose();}finally{UnityEngine.Object.DestroyImmediate(host);try{Directory.Delete(Folder,true);}catch(IOException){}catch(UnauthorizedAccessException){}}
            }
        }
        // The held-out message is not offered again, no retry exists, and the
        // trial carries the missing-playback fault with its exposure consumed.
        static void AssertConsumedFaultWithoutRetry(Fixture f,SlotContext c,string next)
        {
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(f.Session.Any(r=>r.Event=="onset_evidence"||r.Event=="retry_queued"),Is.False);
            var rows=f.Session.Where(r=>r.TrialId==c.Item.TrialId&&r.State>=ItemState.CueRequested).ToList();
            Assert.That(rows,Is.Not.Empty);Assert.That(rows.All(r=>r.ExposureConsumed&&r.AudibleStatus==AudibleStatus.Uncertain),Is.True);
            f.Engine.ConfirmResume();f.Engine.Tick();Assert.That(f.Engine.CurrentTrialId,Is.EqualTo(next),"the consumed message is not offered again");
            var tables=f.Derive();
            var trial=tables.Trials.Single(r=>r["attempt_id"]==c.Item.TrialId);
            Assert.That(trial["playback_status"],Is.EqualTo("uncertain"));Assert.That(trial["exposure_consumed"],Is.EqualTo("true"));
            Assert.That(trial["technical_fault_code"].Split(';'),Has.Member("AUDIO_MISSING_PLAYBACK"),"scored by the fault rule");
            Assert.That(tables.Trials.Any(r=>r["retry_of"].Length>0),Is.False);
            var exposure=tables.Exposures.Single(r=>r["audio_request_id"]==c.AudioRequestIds[0]);
            Assert.That(exposure["audible_status"],Is.EqualTo("uncertain"));Assert.That(exposure["exposure_consumed"],Is.EqualTo("true"));
        }

        // Default station today: software evidence alone never earns a retry.
        [Test, Category("REQ_INT_06"), Category("REQ_RSV_02"), Category("REQ_INT_07"), Category("REQ_MISS_02")]
        [NUnit.Framework.Property("Requirement","REQ-INT-06"), NUnit.Framework.Property("Requirement","REQ-RSV-02"), NUnit.Framework.Property("Requirement","REQ-INT-07"), NUnit.Framework.Property("Requirement","REQ-MISS-02")]
        public void SoftwareOnlyNoOnsetIsATechnicalFaultAndKeepsTheHeldOutExposureConsumed()
        {
            using var f=new Fixture(null,Block("novel",Item("N0",heldout:true),Item("N1",heldout:true)));
            f.Silent.Add("N0");f.Start();f.At(4500);var c=f.Made[0].Context;
            Assert.That(f.Deliver(c,"AUDIO_MISSING_PLAYBACK",0,0),Is.False);
            Assert.That(f.Reporter.VerdictFor(c.AudioRequestIds[0]),Is.EqualTo(NoOnsetVerdict.NoLoopbackSource));
            Assert.That(f.Rows("audio_evidence"),Is.Empty);
            f.Engine.Fault("AUDIO_MISSING_PLAYBACK");f.Run();
            AssertConsumedFaultWithoutRetry(f,c,"N1");
        }

        [Test, Category("REQ_INT_06"), NUnit.Framework.Property("Requirement","REQ-INT-06")]
        public void SilentLoopbackAndMissingPlaybackQueueOneLinkedRetryAfterTheRestOfItsBlock()
        {
            using var f=new Fixture(new Route(),Block("first",Item("T0"),Item("T1")),Block("second",Item("U0")));
            f.Silent.Add("T0");f.Start();f.At(4500);
            var original=f.Made[0].Context;Assert.That(f.Engine.CurrentState,Is.EqualTo(ItemState.ResponseOpen));
            var capture=f.Capture(original);
            Assert.That(f.Deliver(original,"AUDIO_MISSING_PLAYBACK",0,0),Is.True);
            Assert.That(f.Reporter.VerdictFor(original.AudioRequestIds[0]),Is.EqualTo(NoOnsetVerdict.LoopbackSilent));
            // The module then faults the item with the delivery code, as the joined modules do.
            f.Engine.Fault("AUDIO_MISSING_PLAYBACK");f.Run();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            var missing=f.Rows("audio_observation").Single();string expected=NoOnsetReporter.Evidence(missing.Sha256,capture.EvidenceSha256);
            var onset=f.Session.Single(r=>r.Event=="onset_evidence");
            Assert.That(onset.TrialId,Is.EqualTo("T0"));Assert.That(onset.AudibleStatus,Is.EqualTo(AudibleStatus.ConfirmedNoOnset));
            Assert.That(onset.ExposureConsumed,Is.False);Assert.That(onset.EvidenceSha256,Is.EqualTo(expected),"the evidence binds the durable row and the loopback capture");
            Assert.That(f.Rows("session").Single(r=>(string)r.Payload["event"]=="onset_evidence").Sequence,Is.GreaterThan(missing.Sequence));
            var evidence=f.Rows("audio_evidence").Single();
            Assert.That(evidence.Context.AudioRequestId,Is.EqualTo(original.AudioRequestIds[0]));Assert.That((string)evidence.Payload["evidence_sha256"],Is.EqualTo(expected));
            Assert.That((string)evidence.Payload["evidence_kind"],Is.EqualTo("acoustic_measurement"));Assert.That((string)evidence.Payload["audible_status"],Is.EqualTo("confirmed_no_onset"));
            Assert.That(f.Session.Single(r=>r.Event=="retry_queued").RetryOf,Is.EqualTo("T0"));
            f.Engine.ConfirmResume();f.Engine.Tick();f.Run();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused),"block boundary after the retry");
            Assert.That(f.Made,Has.Count.EqualTo(3));
            var retry=f.Made[2].Context;
            Assert.That(f.Made[1].Context.Item.TrialId,Is.EqualTo("T1"),"the rest of the block runs first");
            Assert.That(retry.RetryOf,Is.EqualTo("T0"));Assert.That(retry.OpportunityId,Is.EqualTo("T0"));Assert.That(retry.Item.TrialId,Is.Not.EqualTo("T0"));
            Assert.That(f.Session.Count(r=>r.Event=="onset_evidence"),Is.EqualTo(1),"normal plays are never reported");
            Assert.That(f.Loopback.Calls,Is.EqualTo(1),"only a missing playback asks the loopback source");
            f.Engine.ConfirmResume();f.Engine.Tick();Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("U0"));
            var tables=f.Derive();
            var first=tables.Trials.Single(r=>r["attempt_id"]=="T0");
            Assert.That(first["playback_status"],Is.EqualTo("confirmed_no_onset"));Assert.That(first["exposure_consumed"],Is.EqualTo("false"));
            Assert.That(first["technical_fault_code"],Does.Contain("AUDIO_MISSING_PLAYBACK"));
            var repeat=tables.Trials.Single(r=>r["attempt_id"]==retry.Item.TrialId);
            Assert.That(repeat["retry_of"],Is.EqualTo("T0"));Assert.That(repeat["opportunity_id"],Is.EqualTo("T0"));Assert.That(repeat["exposure_consumed"],Is.EqualTo("true"));
        }

        // Peak or RMS at or above its threshold is a signal on the route.
        [TestCase(-20.0,-35.0)]
        [TestCase(-50.0,-80.0)]
        [TestCase(-61.0,-69.0)]
        [TestCase(-60.0,-90.0)]
        [Category("REQ_INT_06"), Category("REQ_RSV_02"), Category("REQ_MISS_02")]
        [NUnit.Framework.Property("Requirement","REQ-INT-06"), NUnit.Framework.Property("Requirement","REQ-RSV-02"), NUnit.Framework.Property("Requirement","REQ-MISS-02")]
        public void AudibleLoopbackAllowsNoRetryAndKeepsTheHeldOutExposureConsumed(double peak,double rms)
        {
            using var f=new Fixture(new Route(),Block("novel",Item("N0",heldout:true),Item("N1",heldout:true)));
            f.Silent.Add("N0");f.Start();f.At(4500);var c=f.Made[0].Context;
            var capture=f.Capture(c,peak,rms);Assert.That(capture.Silent,Is.False);
            Assert.That(f.Deliver(c,"AUDIO_MISSING_PLAYBACK",0,0),Is.False);
            Assert.That(f.Reporter.VerdictFor(c.AudioRequestIds[0]),Is.EqualTo(NoOnsetVerdict.LoopbackSignal));
            var evidence=f.Rows("audio_evidence").Single();
            Assert.That((string)evidence.Payload["audible_status"],Is.EqualTo("uncertain"));Assert.That((string)evidence.Payload["evidence_kind"],Is.EqualTo("acoustic_measurement"));
            Assert.That((string)evidence.Payload["evidence_sha256"],Is.EqualTo(capture.EvidenceSha256));
            f.Engine.Fault("AUDIO_MISSING_PLAYBACK");f.Run();
            AssertConsumedFaultWithoutRetry(f,c,"N1");
        }

        // A capture that is absent, bound to another request, starts after the
        // hand-off, ends before the outcome, or whose source fails, proves nothing.
        [TestCase("absent")]
        [TestCase("other_request")]
        [TestCase("late_start")]
        [TestCase("early_end")]
        [TestCase("source_fails")]
        [Category("REQ_INT_06"), Category("REQ_RSV_02"), Category("REQ_MISS_02")]
        [NUnit.Framework.Property("Requirement","REQ-INT-06"), NUnit.Framework.Property("Requirement","REQ-RSV-02"), NUnit.Framework.Property("Requirement","REQ-MISS-02")]
        public void LoopbackForAnotherRequestOrWindowAllowsNoRetry(string kind)
        {
            using var f=new Fixture(new Route(),Block("novel",Item("N0",heldout:true),Item("N1",heldout:true)));
            f.Silent.Add("N0");f.Start();f.At(4500);var c=f.Made[0].Context;
            switch(kind)
            {
                case "other_request":f.Capture(c,request:Guid.NewGuid().ToString("N"));break;
                case "late_start":f.Capture(c,startShift:.5);break;
                case "early_end":f.Capture(c,endShift:-.5);break;
                case "source_fails":f.Capture(c);f.Loopback.Fail=true;break;
            }
            Assert.That(f.Deliver(c,"AUDIO_MISSING_PLAYBACK",0,0),Is.False);
            Assert.That(f.Reporter.VerdictFor(c.AudioRequestIds[0]),Is.EqualTo(NoOnsetVerdict.LoopbackUnusable));
            Assert.That(f.Loopback.Calls,Is.EqualTo(1));Assert.That(f.Rows("audio_evidence"),Is.Empty);
            f.Engine.Fault("AUDIO_MISSING_PLAYBACK");f.Run();
            AssertConsumedFaultWithoutRetry(f,c,"N1");
        }

        // A silent capture cannot override software evidence of output.
        [TestCase("AUDIO_UNDERRUN",512L,1L,true)]
        [TestCase("AUDIO_UNDERRUN",0L,0L,false)]
        [TestCase("AUDIO_MISSING_PLAYBACK",1L,0L,false)]
        [TestCase("AUDIO_MISSING_PLAYBACK",0L,1L,false)]
        [TestCase("AUDIO_MISSING_PLAYBACK",0L,0L,true)]
        [TestCase("AUDIO_FOCUS_LOST",0L,0L,false)]
        [Category("REQ_INT_06"), Category("REQ_RSV_02"), NUnit.Framework.Property("Requirement","REQ-INT-06"), NUnit.Framework.Property("Requirement","REQ-RSV-02")]
        public void AnyRenderedOrAmbiguousOutcomeKeepsTheHeldOutExposureConsumed(string code,long samples,long callbacks,bool onsetFirst)
        {
            using var f=new Fixture(new Route(),Block("novel",Item("N0",heldout:true),Item("N1",heldout:true)));
            f.Silent.Add("N0");f.Start();f.At(2000);var c=f.Made[0].Context;
            if(onsetFirst)Assert.That(f.Deliver(c,"AUDIO_ONSET_ESTIMATED",512,1),Is.False);
            f.At(4500);f.Capture(c);Assert.That(f.Deliver(c,code,samples,callbacks),Is.False);
            f.Engine.Fault(code);f.Run();
            Assert.That(f.Engine.Status,Is.EqualTo(SessionState.Paused));
            Assert.That(f.Session.Any(r=>r.Event=="onset_evidence"||r.Event=="retry_queued"),Is.False);Assert.That(f.Rows("audio_evidence"),Is.Empty);
            var rows=f.Session.Where(r=>r.TrialId=="N0"&&r.State>=ItemState.CueRequested).ToList();
            Assert.That(rows,Is.Not.Empty);Assert.That(rows.All(r=>r.ExposureConsumed&&r.AudibleStatus==AudibleStatus.Uncertain),Is.True);
            f.Engine.ConfirmResume();f.Engine.Tick();Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("N1"),"the consumed held-out message is not offered again");
        }

        [Test, Category("REQ_INT_06"), NUnit.Framework.Property("Requirement","REQ-INT-06")]
        public void MultiPlayItemsAreNeverReported()
        {
            using var f=new Fixture(new Route(),Block("first",Item("L0",plays:2),Item("T1")));
            f.Silent.Add("L0");f.Start();f.At(4500);f.Capture(f.Made[0].Context);
            Assert.That(f.Deliver(f.Made[0].Context,"AUDIO_MISSING_PLAYBACK",0,0),Is.False);
            f.Engine.Fault("AUDIO_MISSING_PLAYBACK");f.Run();
            Assert.That(f.Session.Any(r=>r.Event=="onset_evidence"||r.Event=="retry_queued"),Is.False);
            Assert.That(f.Session.Last(r=>r.TrialId=="L0").ExposureConsumed,Is.True);
            f.Engine.ConfirmResume();f.Engine.Tick();Assert.That(f.Engine.CurrentTrialId,Is.EqualTo("T1"));
        }

        [Test, Category("REQ_INT_06"), NUnit.Framework.Property("Requirement","REQ-INT-06")]
        public void EvidenceIsReportedOncePerRequestAndOnlyWhileItsItemRuns()
        {
            using var f=new Fixture(new Route(),Block("first",Item("T0"),Item("T1"),Item("T2")));
            f.Silent.Add("T0");f.Silent.Add("T1");f.Start();f.At(4500);var first=f.Made[0].Context;f.Capture(first);
            Assert.That(f.Deliver(first,"AUDIO_MISSING_PLAYBACK",0,0),Is.True);
            Assert.That(f.Deliver(first,"AUDIO_MISSING_PLAYBACK",0,0),Is.False,"a repeated outcome is not reported again");
            Assert.That(f.Session.Count(r=>r.Event=="onset_evidence"),Is.EqualTo(1));Assert.That(f.Rows("audio_evidence"),Has.Count.EqualTo(1));
            // An outcome that arrives after its item has finished is not reported,
            // even with a silent capture.
            while(f.Engine.CurrentTrialId!="T2"||f.Engine.CurrentState!=ItemState.CueRequested){f.Clock.Time+=50;f.Engine.Tick();Assert.That(f.Clock.Time,Is.LessThan(60000));}
            f.Capture(f.Made[1].Context);
            Assert.That(f.Deliver(f.Made[1].Context,"AUDIO_MISSING_PLAYBACK",0,0),Is.False);
            Assert.That(f.Session.Count(r=>r.Event=="onset_evidence"),Is.EqualTo(1));Assert.That(f.Session.Count(r=>r.Event=="retry_queued"),Is.EqualTo(1));
            Assert.That(f.Session.Last(r=>r.TrialId=="T1").ExposureConsumed,Is.True);
            // Unbound requests and missing events are ignored.
            Assert.That(f.Reporter.Observe(null),Is.False);
            var stranger=f.Event(f.Made[2].Context,"AUDIO_MISSING_PLAYBACK",0,0,null);
            Assert.That(f.Reporter.Observe(New<AudioPlaybackEvent>(stranger.Code,Guid.NewGuid().ToString("N"),Pcm,null,null,stranger.Timing,stranger.ObservedMonoSeconds,0L,0L,null)),Is.False);
        }

        // The evidence hash is reproducible from the stored observation fields
        // and changes with any of them; malformed observations are refused.
        [Test, Category("REQ_INT_06"), NUnit.Framework.Property("Requirement","REQ-INT-06")]
        public void LoopbackObservationHashBindsEveryFieldAndRefusesMalformedValues()
        {
            string id=Guid.NewGuid().ToString("N");
            LoopbackObservation Make(string request=null,string device="loopback-in-1",double start=1,double end=9,double peak=-90,double rms=-100,string capture=null)=>
                new LoopbackObservation(request??id,device,start,end,peak,rms,capture??Captured);
            var o=Make();
            string text="loopback-observation-v1\n"+id+"\nloopback-in-1\n1\n9\n-90\n-100\n"+Captured+"\n";
            Assert.That(o.Canonical(),Is.EqualTo(text));Assert.That(o.EvidenceSha256,Is.EqualTo(Sha(text)));
            Assert.That(new[]{Make(request:Guid.NewGuid().ToString("N")),Make(device:"loopback-in-2"),Make(start:0.5),Make(end:10),Make(peak:-80),Make(rms:-95),Make(capture:Hash)}
                .Select(x=>x.EvidenceSha256).Distinct().Concat(new[]{o.EvidenceSha256}).Distinct().Count(),Is.EqualTo(8));
            Assert.That(Make(peak:double.NegativeInfinity,rms:double.NegativeInfinity).Silent,Is.True,"digital zero is silent");
            Assert.That(Make(peak:-60.0001,rms:-70.0001).Silent,Is.True);Assert.That(Make(peak:-60,rms:-90).Silent,Is.False);Assert.That(Make(peak:-61,rms:-70).Silent,Is.False);
            Assert.That(o.Covers(id,1,9),Is.True);Assert.That(o.Covers(id,0.9,9),Is.False);Assert.That(o.Covers(id,1,9.1),Is.False);Assert.That(o.Covers(Guid.NewGuid().ToString("N"),2,8),Is.False);
            foreach(var bad in new Func<LoopbackObservation>[]{
                ()=>Make(request:""),()=>Make(device:null),()=>Make(device:"bad device"),()=>Make(start:9,end:9),()=>Make(start:double.NaN),()=>Make(end:double.PositiveInfinity),
                ()=>Make(peak:double.NaN),()=>Make(peak:1),()=>Make(peak:-90,rms:-80),()=>Make(capture:"E".PadRight(64,'E')),()=>Make(capture:"abc")})
                Assert.Throws<ArgumentException>(()=>bad());
            Assert.That(NoOnsetReporter.Evidence(Hash,o.EvidenceSha256),Is.EqualTo(Sha("no-onset-evidence-v1\n"+Hash+"\n"+o.EvidenceSha256+"\n")));
        }
        static string Sha(string text){using var sha=SHA256.Create();return string.Concat(sha.ComputeHash(Encoding.UTF8.GetBytes(text)).Select(b=>b.ToString("x2")));}

        // The joined owner binds every scheduled request and hands each event to
        // the reporter only after the audio adapter has made it durable.
        [Test, Category("REQ_INT_06"), NUnit.Framework.Property("Requirement","REQ-INT-06")]
        public void JoinedOwnerReportsOnlyAfterTheAudioRowIsDurable()
        {
            const BindingFlags Flags=BindingFlags.Instance|BindingFlags.Public|BindingFlags.NonPublic;
            var persist=Names(typeof(SessionIntegrationOwner).GetMethod("PersistAudio",Flags));
            Assert.That(persist.IndexOf("AudioDataAdapter.Record"),Is.GreaterThanOrEqualTo(0));
            Assert.That(persist.IndexOf("NoOnsetReporter.Observe"),Is.GreaterThan(persist.IndexOf("AudioDataAdapter.Record")));
            Assert.That(Names(typeof(SessionIntegrationOwner.Resources).GetMethod(nameof(SessionIntegrationOwner.Resources.BindAudio),Flags)),Has.Member("NoOnsetReporter.Bind"));
            Assert.That(typeof(SessionIntegrationOwner).GetConstructors(Flags).SelectMany(c=>Names(c)),Has.Member("NoOnsetReporter..ctor"));
        }
        // Declaring type and name of each call, callvirt and newobj target in IL order.
        static List<string> Names(MethodBase method)
        {
            var found=new List<string>();byte[] il=method.GetMethodBody().GetILAsByteArray();
            for(int i=0;i+4<il.Length;i++)
            {
                if(il[i]!=0x28&&il[i]!=0x6F&&il[i]!=0x73)continue;
                int token=BitConverter.ToInt32(il,i+1),table=(int)((uint)token>>24);if(table!=0x06&&table!=0x0A)continue;
                MethodBase target=null;try{target=method.Module.ResolveMethod(token);}catch(Exception){}
                if(target?.DeclaringType!=null)found.Add(target.DeclaringType.Name+"."+target.Name);
            }
            return found;
        }
    }
}
