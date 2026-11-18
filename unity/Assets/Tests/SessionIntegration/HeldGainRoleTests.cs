using System;
using System.IO;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
namespace AcousticVocab.SessionIntegration.Tests
{
    // Decision A10: the held level applies to everyone who hears study sounds.
    // In the app every role (Study A learner, Study B active and assigned
    // partners) restores its own held level through one path.
    public sealed class HeldGainRoleTests
    {
        string directory;
        static readonly string Visit=new string('b',32),Receipt=new string('d',64);
        [SetUp]public void Setup(){directory=Path.Combine(Path.GetTempPath(),"av-held-role-"+Guid.NewGuid().ToString("N"));}
        [TearDown]public void Cleanup(){if(Directory.Exists(directory))Directory.Delete(directory,true);}
        static string Name(string codedId)=>PcmWave.Hash(Encoding.ASCII.GetBytes(codedId))+".local.jsonl";
        byte[] History(string slot,float level,bool hold)
        {
            var store=new ComfortableGainStore(directory);
            store.ChangeForComfort("SCR-"+slot,new string('a',32),level,1,true);store.CarryForward("SCR-"+slot,slot,Receipt,2);
            if(hold)store.Hold(slot,Visit,3);
            return File.ReadAllBytes(Path.Combine(directory,Name(slot)));
        }
        [Test,Category("REQ_AUD_05")]
        public void EveryRoleRestoresItsOwnHeldLevelThroughTheSamePath()
        {
            foreach(var (slot,level) in new[]{("B-C01-M1",.3f),("B-C01-M2",.2f),("A-P01-L01",.4f)})
            {
                byte[] bytes=History(slot,level,true);
                Assert.That(JoinedVisitArtifacts.HeldGain(Name(slot),bytes,PcmWave.Hash(bytes),slot),Is.EqualTo(level),slot);
            }
        }
        [Test,Category("REQ_AUD_05")]
        public void NeitherStudyBRoleCanStartFromAnUnheldOrPartnersLevel()
        {
            byte[] active=History("B-C01-M1",.3f,false),yoked=History("B-C01-M2",.2f,false);
            Assert.That(Assert.Throws<SessionFault>(()=>JoinedVisitArtifacts.HeldGain(Name("B-C01-M1"),active,PcmWave.Hash(active),"B-C01-M1")).Code,Is.EqualTo("JOIN_GAIN_NOT_HELD"));
            Assert.That(Assert.Throws<SessionFault>(()=>JoinedVisitArtifacts.HeldGain(Name("B-C01-M2"),yoked,PcmWave.Hash(yoked),"B-C01-M2")).Code,Is.EqualTo("JOIN_GAIN_NOT_HELD"));
            new ComfortableGainStore(directory).Hold("B-C01-M1",Visit,4);active=File.ReadAllBytes(Path.Combine(directory,Name("B-C01-M1")));
            Assert.That(JoinedVisitArtifacts.HeldGain(Name("B-C01-M1"),active,PcmWave.Hash(active),"B-C01-M1"),Is.EqualTo(.3f));
            Assert.That(Assert.Throws<SessionFault>(()=>JoinedVisitArtifacts.HeldGain(Name("B-C01-M1"),active,PcmWave.Hash(active),"B-C01-M2")).Code,Is.EqualTo("JOIN_GAIN_BINDING"),
                "The assigned partner never plays at the active partner's level");
            Assert.Throws<AudioFault>(()=>JoinedVisitArtifacts.HeldGain(Name("B-C01-M2"),active,PcmWave.Hash(active),"B-C01-M2"));
        }
    }
}
