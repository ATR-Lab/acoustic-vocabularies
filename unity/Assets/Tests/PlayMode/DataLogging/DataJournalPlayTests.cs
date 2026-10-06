using System;
using System.Collections;
using System.IO;
using NUnit.Framework;
using UnityEngine.TestTools;

namespace AcousticVocab.DataLogging.PlayModeTests
{
    public sealed class DataJournalPlayTests
    {
        [UnityTest] public IEnumerator DurableRecordsCrossUnityFramesAndResumeInNewClockEpoch()
        {
            string root=Path.Combine(Environment.CurrentDirectory,".local","data-play-"+Guid.NewGuid().ToString("N"));
            var identity=new DataIdentity(new string('1',32),"SYNTHETIC","DEMO","synthetic-station","engineering-test",new string('a',64));double now=0;
            using(var writer=new DataJournal(root,identity,new string('2',32),()=>now))
                for(int i=0;i<36;i++){now++;writer.Append(DataObservations.Device(null,"focus",now,true));Assert.That(writer.Health.DurableRecordCount,Is.EqualTo(i+1));yield return null;}
            using(var resumed=new DataJournal(root,identity,new string('3',32),()=>0)){Assert.That(resumed.Records.Count,Is.EqualTo(36));resumed.Append(DataObservations.Device(null,"input",0,true));}
            Assert.That(DataJournal.Verify(root,identity).Records.Count,Is.EqualTo(37));
        }
    }
}
