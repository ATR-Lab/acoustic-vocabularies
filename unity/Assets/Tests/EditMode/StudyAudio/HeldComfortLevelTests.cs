using System;
using System.IO;
using System.Text;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests.StudyAudio
{
    // Decision A6: the level is set at screening and visit 1, then held constant
    // across visits. No change is ever written inside or after a held visit.
    public sealed class HeldComfortLevelTests
    {
        string directory;
        static readonly string Screening=new string('a',32),VisitOne=new string('b',32),VisitTwo=new string('c',32);
        static readonly string Receipt=new string('d',64);
        [SetUp] public void Setup() { directory=Path.Combine(Path.GetTempPath(),"av-held-gain-"+Guid.NewGuid().ToString("N")); }
        [TearDown] public void Cleanup() { if(Directory.Exists(directory)) Directory.Delete(directory,true); }
        string SlotFile(string codedId)=>Path.Combine(directory,PcmWave.Hash(Encoding.ASCII.GetBytes(codedId))+".local.jsonl");
        ComfortableGainStore Allocated(string slot,float screening=.3f,float visitOne=.25f)
        {
            var store=new ComfortableGainStore(directory);
            store.ChangeForComfort("SCR-"+slot,Screening,screening,1,true);
            store.CarryForward("SCR-"+slot,slot,Receipt,2);
            if(visitOne!=screening) store.ChangeForComfort(slot,VisitOne,visitOne,3,true);
            return store;
        }

        [Test, Category("REQ_AUD_05")]
        public void HeldLevelRefusesEveryLaterComfortChange()
        {
            var store=Allocated("A-P01-L01");
            Assert.That(store.IsHeld("A-P01-L01"),Is.False,"Visit 1 may still set the level");
            Assert.That(store.Hold("A-P01-L01",VisitOne,4),Is.EqualTo(.25f));
            Assert.That(store.IsHeld("A-P01-L01"),Is.True);
            string before=File.ReadAllText(SlotFile("A-P01-L01"));
            foreach(string visit in new[]{VisitOne,VisitTwo})
                Assert.That(Assert.Throws<AudioFault>(()=>store.ChangeForComfort("A-P01-L01",visit,.4f,5,true)).Code,Is.EqualTo("GAIN_HELD_CONSTANT"));
            Assert.That(File.ReadAllText(SlotFile("A-P01-L01")),Is.EqualTo(before),"A refused change writes nothing");
            Assert.That(new ComfortableGainStore(directory).Restore("A-P01-L01"),Is.EqualTo(.25f));
        }

        [Test, Category("REQ_AUD_05")]
        public void NextVisitRestoresTheSameHeldLevelThroughEveryPath()
        {
            var store=Allocated("A-P01-L01");store.Hold("A-P01-L01",VisitOne,4);
            Assert.That(store.Hold("A-P01-L01",VisitTwo,9),Is.EqualTo(.25f),"Holding again is idempotent");
            Assert.That(File.ReadAllLines(SlotFile("A-P01-L01")).Length,Is.EqualTo(3),"carried, changed, held");
            byte[] pinned=File.ReadAllBytes(SlotFile("A-P01-L01"));
            Assert.That(ComfortableGainStore.RestoreHeldVerified(pinned,PcmWave.Hash(pinned),"A-P01-L01"),Is.EqualTo(.25f));
            Assert.That(ComfortableGainStore.RestoreVerified(pinned,PcmWave.Hash(pinned),"A-P01-L01"),Is.EqualTo(.25f));
            Assert.That(new ComfortableGainStore(directory).RestoreOrCarry("SCR-A-P01-L01","A-P01-L01",Receipt,10),Is.EqualTo(.25f));
            Assert.That(File.ReadAllBytes(SlotFile("A-P01-L01")),Is.EqualTo(pinned),"Restoring never rewrites the history");
        }

        [Test, Category("REQ_AUD_05")]
        public void UnheldLevelCannotStartAVisit()
        {
            Allocated("A-P01-L01");
            byte[] bytes=File.ReadAllBytes(SlotFile("A-P01-L01"));
            Assert.That(Assert.Throws<AudioFault>(()=>ComfortableGainStore.RestoreHeldVerified(bytes,PcmWave.Hash(bytes),"A-P01-L01")).Code,Is.EqualTo("GAIN_NOT_HELD"));
            Assert.That(ComfortableGainStore.RestoreVerified(bytes,PcmWave.Hash(bytes),"A-P01-L01"),Is.EqualTo(.25f),"Calibration can still read it");
        }

        [Test, Category("REQ_AUD_05")]
        public void HoldNeverInventsALevelAndTamperedHoldsAreRefused()
        {
            var store=new ComfortableGainStore(directory);
            Assert.That(Assert.Throws<AudioFault>(()=>store.Hold("A-P01-L01",VisitOne,1)).Code,Is.EqualTo("GAIN_HOLD_SOURCE_MISSING"));
            Assert.That(File.Exists(SlotFile("A-P01-L01")),Is.False);
            store=Allocated("A-P01-L01");
            Assert.That(Assert.Throws<AudioFault>(()=>store.Hold("A-P01-L01","not-a-visit",1)).Code,Is.EqualTo("GAIN_HOLD_INVALID"));
            Assert.That(Assert.Throws<AudioFault>(()=>store.Hold("A-P01-L01",VisitOne,double.NaN)).Code,Is.EqualTo("GAIN_HOLD_INVALID"));
            store.Hold("A-P01-L01",VisitOne,4);
            string path=SlotFile("A-P01-L01"),original=File.ReadAllText(path),held=File.ReadAllLines(path)[2];
            var change=new JObject{["event"]="comfort_gain_changed",["coded_id"]="A-P01-L01",["visit_id"]=VisitTwo,["old_gain"]=.25f,["new_gain"]=.5f,["mono_s"]=6.0};
            File.WriteAllText(path,original+change.ToString(Newtonsoft.Json.Formatting.None)+"\n");
            Assert.That(Assert.Throws<AudioFault>(()=>store.Restore("A-P01-L01")).Code,Is.EqualTo("GAIN_HISTORY_INVALID"),"A change written after a hold");
            File.WriteAllText(path,original.Replace(held,held.Replace("\"gain\":0.25","\"gain\":0.5")));
            Assert.That(Assert.Throws<AudioFault>(()=>store.Restore("A-P01-L01")).Code,Is.EqualTo("GAIN_HISTORY_INVALID"),"A hold that differs from the level");
            File.WriteAllText(path,held+"\n");
            Assert.That(Assert.Throws<AudioFault>(()=>store.Restore("A-P01-L01")).Code,Is.EqualTo("GAIN_HISTORY_INVALID"),"A hold cannot open a history");
            File.WriteAllText(path,original);Assert.That(store.Restore("A-P01-L01"),Is.EqualTo(.25f));
        }

        [Test, Category("REQ_AUD_05")]
        public void ScreeningLevelIsCarriedWhenVisitOneKeepsIt()
        {
            var store=Allocated("B-C01-M1",.3f,.3f);
            Assert.That(store.Hold("B-C01-M1",VisitOne,4),Is.EqualTo(.3f));
            Assert.That(store.Restore("SCR-B-C01-M1"),Is.EqualTo(.3f));
            Assert.That(store.IsHeld("SCR-B-C01-M1"),Is.False,"Screening histories are not held by the slot");
        }
    }
}
