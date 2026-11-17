using System;
using System.IO;
using AcousticVocab.StudyAudio;
using NUnit.Framework;

namespace AcousticVocab.Tests.StudyAudio
{
    public sealed class ComfortableGainTests
    {
        string directory;
        [SetUp] public void Setup() { directory=Path.Combine(Path.GetTempPath(),"av-gain-test-"+Guid.NewGuid().ToString("N")); }
        [TearDown] public void Cleanup() { if(Directory.Exists(directory)) Directory.Delete(directory,true); }
        [Test] public void GainRestoresAcrossVisitsAndDoesNotLeakToAnotherCodedId()
        {
            var store=new ComfortableGainStore(directory);var change=store.ChangeForComfort("CODE-01",new string('a',32),.2f,12,true);
            Assert.That(change.Previous,Is.EqualTo(.1f));Assert.That(change.Current,Is.EqualTo(.2f));
            store=new ComfortableGainStore(directory);Assert.That(store.Restore("CODE-01"),Is.EqualTo(.2f));
            Assert.That(store.Restore("CODE-02"),Is.EqualTo(.1f));
            store.ChangeForComfort("CODE-01",new string('b',32),.15f,1,true);
            Assert.That(store.Restore("CODE-01"),Is.EqualTo(.15f));Assert.That(File.ReadAllLines(Directory.GetFiles(directory)[0]).Length,Is.EqualTo(2));
        }
        [Test] public void TornHistoryBlocksRestoreAndFutureChanges()
        {
            var store=new ComfortableGainStore(directory);store.ChangeForComfort("CODE-01",new string('a',32),.2f,12,true);
            string path=Directory.GetFiles(directory)[0];File.AppendAllText(path,"{\"event\":");
            Assert.Throws<AudioFault>(()=>store.Restore("CODE-01"));
            Assert.Throws<AudioFault>(()=>store.ChangeForComfort("CODE-01",new string('a',32),.3f,15,true));
        }
        [Test] public void ChangedGainChainCannotBeRestored()
        {
            var store=new ComfortableGainStore(directory);store.ChangeForComfort("CODE-01",new string('a',32),.2f,12,true);
            string path=Directory.GetFiles(directory)[0];File.WriteAllText(path,File.ReadAllText(path).Replace("\"old_gain\":0.1","\"old_gain\":0.4"));
            Assert.Throws<AudioFault>(()=>store.Restore("CODE-01"));
        }
        static string Receipt(char c)=>new string(c,64);
        string SlotFile(string codedId)=>Path.Combine(directory,PcmWave.Hash(System.Text.Encoding.ASCII.GetBytes(codedId))+".local.jsonl");
        [Test] public void CarryForwardCopiesTheScreeningGainIntoTheSlotAndLaterChangesChainFromIt()
        {
            var store=new ComfortableGainStore(directory);
            store.ChangeForComfort("SCR-01",new string('a',32),.3f,5,true);store.ChangeForComfort("SCR-01",new string('a',32),.25f,6,true);
            Assert.That(store.CarryForward("SCR-01","A-P01-L01",Receipt('c'),40),Is.EqualTo(.25f));
            Assert.That(new ComfortableGainStore(directory).Restore("A-P01-L01"),Is.EqualTo(.25f));
            Assert.That(store.Restore("SCR-01"),Is.EqualTo(.25f),"The screening history is not changed");
            var change=store.ChangeForComfort("A-P01-L01",new string('b',32),.2f,50,true);
            Assert.That(change.Previous,Is.EqualTo(.25f));Assert.That(store.Restore("A-P01-L01"),Is.EqualTo(.2f));
            var head=Newtonsoft.Json.Linq.JObject.Parse(File.ReadAllLines(SlotFile("A-P01-L01"))[0]);
            Assert.That((string)head["event"],Is.EqualTo("comfort_gain_carried"));Assert.That((string)head["source_coded_id"],Is.EqualTo("SCR-01"));
            Assert.That((string)head["allocation_receipt_sha256"],Is.EqualTo(Receipt('c')));
            Assert.That((string)head["source_history_sha256"],Is.EqualTo(PcmWave.Hash(File.ReadAllBytes(SlotFile("SCR-01")))));
        }
        [Test] public void CarriedHistoryRestoresThroughThePinnedVisitPathOnlyForItsSlot()
        {
            var store=new ComfortableGainStore(directory);store.ChangeForComfort("SCR-01",new string('a',32),.3f,5,true);
            store.CarryForward("SCR-01","B-P01-M2",Receipt('d'),9);
            byte[] bytes=File.ReadAllBytes(SlotFile("B-P01-M2"));
            Assert.That(ComfortableGainStore.RestoreVerified(bytes,PcmWave.Hash(bytes),"B-P01-M2"),Is.EqualTo(.3f));
            Assert.Throws<AudioFault>(()=>ComfortableGainStore.RestoreVerified(bytes,PcmWave.Hash(bytes),"B-P01-M1"));
        }
        [Test] public void CarryNeverInventsOrOverwritesAGain()
        {
            var store=new ComfortableGainStore(directory);
            Assert.That(Assert.Throws<AudioFault>(()=>store.CarryForward("SCR-01","A-P01-L01",Receipt('c'),1)).Code,Is.EqualTo("GAIN_CARRY_SOURCE_MISSING"));
            Assert.That(File.Exists(SlotFile("A-P01-L01")),Is.False);
            store.ChangeForComfort("SCR-01",new string('a',32),.3f,5,true);store.ChangeForComfort("A-P01-L01",new string('a',32),.4f,6,true);
            Assert.That(Assert.Throws<AudioFault>(()=>store.CarryForward("SCR-01","A-P01-L01",Receipt('c'),7)).Code,Is.EqualTo("GAIN_CARRY_TARGET_EXISTS"));
            Assert.That(store.Restore("A-P01-L01"),Is.EqualTo(.4f));
            Assert.Throws<AudioFault>(()=>store.CarryForward("SCR-01","SCR-01",Receipt('c'),7));
            Assert.Throws<AudioFault>(()=>store.CarryForward("SCR-01","A-P01-L02","not-a-receipt",7));
            Assert.Throws<AudioFault>(()=>store.CarryForward("SCR-01","A-P01-L02",Receipt('c'),double.NaN));
            Assert.Throws<AudioFault>(()=>store.CarryForward("../SCR","A-P01-L02",Receipt('c'),7));
            Assert.That(File.Exists(SlotFile("A-P01-L02")),Is.False);
        }
        [Test] public void RestoreOrCarryIsIdempotentAndRefusesAnotherSourceOrReceipt()
        {
            var store=new ComfortableGainStore(directory);store.ChangeForComfort("SCR-01",new string('a',32),.3f,5,true);
            Assert.That(store.RestoreOrCarry("SCR-01","A-P01-L01",Receipt('c'),1),Is.EqualTo(.3f));
            Assert.That(store.RestoreOrCarry("SCR-01","A-P01-L01",Receipt('c'),2),Is.EqualTo(.3f));
            Assert.That(File.ReadAllLines(SlotFile("A-P01-L01")).Length,Is.EqualTo(1));
            Assert.That(Assert.Throws<AudioFault>(()=>store.RestoreOrCarry("SCR-02","A-P01-L01",Receipt('c'),3)).Code,Is.EqualTo("GAIN_CARRY_MISMATCH"));
            Assert.That(Assert.Throws<AudioFault>(()=>store.RestoreOrCarry("SCR-01","A-P01-L01",Receipt('e'),3)).Code,Is.EqualTo("GAIN_CARRY_MISMATCH"));
            store.ChangeForComfort("A-P01-L09",new string('a',32),.5f,6,true);
            Assert.That(Assert.Throws<AudioFault>(()=>store.RestoreOrCarry("SCR-01","A-P01-L09",Receipt('c'),3)).Code,Is.EqualTo("GAIN_CARRY_MISMATCH"));
        }
        [Test] public void CarriedRowMustBeFirstCanonicalAndBoundToItsSlot()
        {
            var store=new ComfortableGainStore(directory);store.ChangeForComfort("SCR-01",new string('a',32),.3f,5,true);
            store.CarryForward("SCR-01","A-P01-L01",Receipt('c'),9);string path=SlotFile("A-P01-L01"),line=File.ReadAllLines(path)[0];
            store.ChangeForComfort("A-P01-L01",new string('b',32),.2f,10,true);string original=File.ReadAllText(path);
            File.WriteAllText(path,original+line+"\n");Assert.Throws<AudioFault>(()=>store.Restore("A-P01-L01"),"A carried row after the first row");
            File.WriteAllText(path,original.Replace("\"gain\":0.3","\"gain\": 0.3"));Assert.Throws<AudioFault>(()=>store.Restore("A-P01-L01"),"Non-canonical row");
            File.WriteAllText(path,original.Replace("\"gain\":0.3","\"gain\":0.9"));Assert.Throws<AudioFault>(()=>store.Restore("A-P01-L01"),"Next row no longer chains");
            File.WriteAllText(path,original.Replace("\"coded_id\":\"A-P01-L01\",\"source","\"coded_id\":\"A-P01-L02\",\"source"));Assert.Throws<AudioFault>(()=>store.Restore("A-P01-L01"));
            File.WriteAllText(path,original.Replace(Receipt('c'),"c"));Assert.Throws<AudioFault>(()=>store.Restore("A-P01-L01"));
            File.WriteAllText(path,original);Assert.That(store.Restore("A-P01-L01"),Is.EqualTo(.2f));
        }
        [Test] public void PlayingOrInvalidGainCannotBeWritten()
        {
            var store=new ComfortableGainStore(directory);
            Assert.Throws<AudioFault>(()=>store.ChangeForComfort("CODE-01",new string('a',32),.2f,12,false));
            Assert.Throws<AudioFault>(()=>store.ChangeForComfort("CODE-01",new string('a',32),float.NaN,12,true));
            Assert.Throws<AudioFault>(()=>store.ChangeForComfort("../CODE",new string('a',32),.2f,12,true));
            Assert.That(Directory.GetFiles(directory),Is.Empty);
        }
        [Test] public void StoredProfileOrderRequiresBothCompletePlaysBeforeEachAnswer()
        {
            string[] order={"P2","P1","P3"};var sequence=new ProfileComfortSequence(order);order[0]="P3";
            int answers=0;sequence.Answer+=(profile,answer,time)=>{ answers++;Assert.That(profile,Is.EqualTo(new[]{"P2","P1","P3"}[answers-1])); };
            var wave=PackageLoaderTests.PatternWave(96000,3,17);
            for(int i=0;i<3;i++)
            {
                sequence.StartProfile(10*i,wave);Assert.That(sequence.SecondOnsetMonoSeconds,Is.EqualTo(10*i+4));
                Assert.Throws<AudioFault>(()=>sequence.Respond(true,10*i+6));
                sequence.ConfirmPlayback(0,10*i+2);Assert.That(sequence.CanAnswer,Is.False);
                sequence.ConfirmPlayback(1,10*i+6);sequence.Respond(true,10*i+6);
            }
            Assert.That(sequence.Finished,Is.True);Assert.That(answers,Is.EqualTo(3));
            Assert.Throws<AudioFault>(()=>sequence.StartProfile(40,wave));
        }
        [Test] public void ResponseCannotPrecedeDelayedPlaybackEvidence()
        {
            var sequence=new ProfileComfortSequence(new[]{"P1","P2","P3"});sequence.Answer+=(_,__,___)=>{};
            sequence.StartProfile(0,PackageLoaderTests.PatternWave(96000,3,17));
            Assert.Throws<AudioFault>(()=>sequence.ConfirmPlayback(1,6));
            sequence.ConfirmPlayback(0,2);sequence.ConfirmPlayback(1,7);
            Assert.Throws<AudioFault>(()=>sequence.Respond(true,6));sequence.Respond(true,7);
        }
        [Test] public void MissingDurableAnswerSinkDoesNotAdvanceProfile()
        {
            var sequence=new ProfileComfortSequence(new[]{"P1","P2","P3"});sequence.StartProfile(0,PackageLoaderTests.PatternWave(96000,3,17));
            sequence.ConfirmPlayback(0,2);sequence.ConfirmPlayback(1,6);Assert.Throws<AudioFault>(()=>sequence.Respond(true,6));
            Assert.That(sequence.Profile,Is.EqualTo("P1"));
        }
    }
}
