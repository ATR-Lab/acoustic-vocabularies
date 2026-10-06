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
