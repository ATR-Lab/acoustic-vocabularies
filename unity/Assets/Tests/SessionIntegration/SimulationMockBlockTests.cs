using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.DataLogging;
using AcousticVocab.Foundation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration.Tests
{
    // #67 process-kill harness support. The native kill itself is exercised by
    // tools/session-kill/run-kill-resume.ps1 against the SimulationTest player.
    public sealed class SimulationMockBlockTests
    {
        string root;SimulationTestAuthority authority;
        [SetUp]public void Setup()
        {
            root=Path.Combine(Path.GetTempPath(),".local","simulation-test-mock-"+Guid.NewGuid().ToString("N"));Directory.CreateDirectory(root);
            byte[] raw=Encoding.UTF8.GetBytes(new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["fixture_set_sha256"]=new string('a',64),["package_sha256"]=new string('b',64),["schedule_sha256"]=new string('c',64),["build_id"]="mock-build",["protocol_version"]="simulation-test-v1",["output_directory"]=Path.Combine(root,"evidence"),["audio_gain"]=.05,["participant_admission"]=false,["acoustic_qualification"]=false}.ToString());
            string path=Path.Combine(root,"capability.json");File.WriteAllBytes(path,raw);authority=SimulationTestAuthority.Load(path,PcmWave.Hash(raw),"mock-build","simulation-test-v1");
        }
        [TearDown]public void Cleanup(){if(Directory.Exists(root))Directory.Delete(root,true);}
        [Test]public void SyntheticBlockIsDemoSinglePlayFourteenSecondSlotsWithAStableDefinitionHash()
        {
            var block=VisitSchedule.SyntheticMockBlock(4);var items=block.Blocks.Single().Items;
            Assert.That(items.Select(i=>i.TrialId),Is.EqualTo(new[]{"MOCK-01","MOCK-02","MOCK-03","MOCK-04"}));
            Assert.That(items.All(i=>i.Plays==1&&i.SlotSeconds==14&&!i.Heldout&&i.TrialType=="trained"),Is.True);
            Assert.That(block.Demo,Is.True);Assert.That(block.Visit,Is.EqualTo("MOCK"));Assert.That(block.PackageSha256,Is.EqualTo(new string('0',64)));
            Assert.That(VisitSchedule.SyntheticMockBlock(4).Sha256,Is.EqualTo(block.Sha256));Assert.That(VisitSchedule.SyntheticMockBlock(5).Sha256,Is.Not.EqualTo(block.Sha256));
            Assert.Throws<SessionFault>(()=>VisitSchedule.SyntheticMockBlock(0));Assert.Throws<SessionFault>(()=>VisitSchedule.SyntheticMockBlock(37));
        }
        [Test]public void RunRootMustSitInsideTheCapabilityOutputDirectory()
        {
            string inside=Path.Combine(root,"evidence","kill-run-01");
            Assert.That(SimulationMockBlockHost.ValidateRoot(authority,inside),Is.EqualTo(Path.GetFullPath(inside)));
            foreach(string outside in new[]{Path.Combine(root,"other"),Path.Combine(root,"evidence-sibling","x"),"relative\\path"})
                Assert.That(Assert.Throws<SessionFault>(()=>SimulationMockBlockHost.ValidateRoot(authority,outside)).Code,Is.EqualTo("MOCK_BLOCK_ROOT_INVALID"),outside);
            Assert.Throws<SessionFault>(()=>SimulationMockBlockHost.ValidateRoot(null,inside));
        }
        [TestCase(null,6)][TestCase("2",2)][TestCase("36",36)]
        public void ItemCountDefaultsAndBounds(string value,int expected)=>Assert.That(SimulationMockBlockHost.ParseCount(value),Is.EqualTo(expected));
        [TestCase("1")][TestCase("37")][TestCase("four")]
        public void InvalidItemCountsRefused(string value)=>Assert.Throws<SessionFault>(()=>SimulationMockBlockHost.ParseCount(value));
        [Test]public void JournalIdentityIsStableAcrossRelaunchesOfOneRoot()
        {
            var block=VisitSchedule.SyntheticMockBlock(4);string a=Path.Combine(root,"evidence","a"),b=Path.Combine(root,"evidence","b");
            var first=SimulationMockBlockHost.Identity(block,a,"simulation-test-v1",new string('e',64));
            Assert.That(JToken.DeepEquals(first.ToJson(),SimulationMockBlockHost.Identity(block,a,"simulation-test-v1",new string('e',64)).ToJson()),Is.True);
            Assert.That(SimulationMockBlockHost.Identity(block,b,"simulation-test-v1",new string('e',64)).SessionId,Is.Not.EqualTo(first.SessionId));
            Assert.That(first.CodedId,Is.EqualTo("DEMO-MOCK"));
        }
        [Test]public void SilentCueIsCanonicalAndHalfASecond()
        {
            var wave=SimulationMockBlockHost.SilentCue();Assert.That(wave.SampleCount,Is.EqualTo(24000));Assert.That(wave.CopySamples().All(x=>x==0),Is.True);Assert.That(wave.FileSha256,Is.Not.Null);
        }
        [Test]public void ReopenedJournalResumesAtTheNextUnplayedMockItemAndKeepsTheCueConsumed()
        {
            // In-process stand-in for the native kill: the first writer is
            // abandoned after a durable CueRequested, then a new engine recovers.
            var block=VisitSchedule.SyntheticMockBlock(3);string dir=Path.Combine(root,"evidence","journal");var id=SimulationMockBlockHost.Identity(block,dir,"simulation-test-v1",new string('e',64));
            double now=0;var first=new DataJournal(dir,id,Guid.NewGuid().ToString("N"),()=>now);var factory=new CountingFactory();
            var engine=new FixedSlotEngine(block,new Clock(()=>now),new SessionDataJournal(first),factory);engine.ConfirmResume();engine.Tick();
            while(engine.CurrentTrialId!="MOCK-02"||engine.CurrentState!=ItemState.CueRequested){now+=50;engine.Tick();}
            first.Dispose();
            using var second=new DataJournal(dir,id,Guid.NewGuid().ToString("N"),()=>now);
            var resumed=new FixedSlotEngine(block,new Clock(()=>now),new SessionDataJournal(second),factory);
            Assert.That(resumed.NeedsOperatorConfirmation,Is.True);Assert.That(resumed.CompletedOpportunities,Is.EqualTo(2));
            resumed.ConfirmResume();Assert.That(resumed.CurrentTrialId,Is.EqualTo("MOCK-03"));
            Assert.That(factory.Cues.Count(x=>x=="MOCK-02"),Is.EqualTo(1));
        }
        sealed class Clock:ISessionClock{readonly Func<double> now;internal Clock(Func<double> now){this.now=now;}public double NowMs=>now();}
        sealed class CountingFactory:ISlotContentFactory
        {
            internal readonly System.Collections.Generic.List<string> Cues=new System.Collections.Generic.List<string>();
            public ISlotContent Create(SlotItem item)=>new Content(this);
            sealed class Content:ISlotContent
            {
                readonly CountingFactory owner;bool reset;internal Content(CountingFactory owner){this.owner=owner;}
                public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);public bool ResetComplete=>reset;
                public void Prepare(SlotContext c){}public void RequestCue(SlotContext c,INovelSlotAuthorization p)=>owner.Cues.Add(c.Item.TrialId);
                public void OpenResponse(SlotContext c){}public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c)=>reset=true;public void Interrupt(string code){}
            }
        }
    }
}
