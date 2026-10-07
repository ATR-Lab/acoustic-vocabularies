using System;
using System.IO;
using System.Linq;
using AcousticVocab.Assessment;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.DataLogging.Tests
{
    public sealed class AssessmentAdapterTests
    {
        static readonly string Schedule=new string('b',64),Epoch=new string('c',32);
        static AssessmentRecord Record(string kind="rating",double now=1,string stage="forms",string item="pleasantness",int? value=7,string outcome=null,string epoch=null)
            =>new AssessmentRecord(kind,Schedule,now,stage,item,value,outcome,epoch??Epoch);
        [Test] public void StageRowsReopenAndExportSeparatelyFromTrialAndExposureRows()
        {
            string raw=SyntheticData.Folder("assessment-stages"),output=SyntheticData.Folder("assessment-export");
            using(var journal=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>100))
            {
                SyntheticData.Trial(journal,"protected-before-forms");var stage=new AssessmentDataJournal(journal,Schedule);
                stage.Append(Record());stage.Append(Record("optional_help",2,"post_w4_optional","K-a1",null,"requested"));
                stage.Append(Record("optional_help",3,"post_w4_optional","K-a1",null,"completed"));
            }
            using(var reopened=new DataJournal(raw,SyntheticData.Identity,new string('4',32),()=>0))
            {var adapter=new AssessmentDataJournal(reopened,Schedule);Assert.That(adapter.Records.Count,Is.EqualTo(3));Assert.That(adapter.Records[0].Value,Is.EqualTo(7));Assert.That(adapter.Records[2].ClockEpoch,Is.EqualTo(Epoch));}
            var tables=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity);
            Assert.That(tables.Trials.Count,Is.EqualTo(1));Assert.That(tables.Exposures.Count,Is.EqualTo(1));
            var bundle=ExportBundle.Create(raw,output,SyntheticData.Identity,ExportHeaders.Provisional());
            Assert.That(File.ReadAllLines(Path.Combine(output,AssessmentExport.Table)).Length,Is.EqualTo(4));
            Assert.That(File.ReadAllText(Path.Combine(output,"trial-log.csv")),Does.Not.Contain("pleasantness").And.Not.Contain("optional_help"));
            Assert.That(bundle.HeadersQualified,Is.False);Assert.That(ExportBundle.Load(output,bundle.ManifestSha256).Files.Count,Is.EqualTo(bundle.Files.Count));
            File.AppendAllText(Path.Combine(output,AssessmentExport.Table),"changed");Assert.Throws<DataFault>(()=>bundle.VerifyAll());
        }
        [Test] public void PayloadEpochIsCheckedIndependentlyOfAppendEpoch()
        {
            string raw=SyntheticData.Folder("assessment-clock");
            using(var journal=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>100))
            {
                var adapter=new AssessmentDataJournal(journal,Schedule);adapter.Append(Record(now:10));
                adapter.Append(Record(now:0,epoch:new string('d',32)));
                Assert.Throws<DataFault>(()=>adapter.Append(Record(now:9)));Assert.That(adapter.Records.Count,Is.EqualTo(2));
            }
            Assert.That(DataJournal.Verify(raw,SyntheticData.Identity).Records.Count,Is.EqualTo(2));
        }
        [Test] public void ForeignScheduleAndAttemptContextAreRefused()
        {
            string raw=SyntheticData.Folder("assessment-binding");
            using var journal=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1);
            var adapter=new AssessmentDataJournal(journal,Schedule);adapter.Append(Record());
            Assert.Throws<DataFault>(()=>new AssessmentDataJournal(journal,new string('e',64)));
            Assert.Throws<DataFault>(()=>adapter.Append(new AssessmentRecord("forms_started",new string('e',64),1,"forms")));
            Assert.Throws<DataFault>(()=>new EventDraft("assessment_stage",new EventContext("trial","trial"),AssessmentRecordCodec.ToJson(Record())));
        }
        [Test] public void ClosedCodecRejectsCrossStageUnknownFieldsAndOutOfRangeRatings()
        {
            var payload=AssessmentRecordCodec.ToJson(Record());payload["correct"]=true;Assert.Throws<AssessmentFault>(()=>AssessmentRecordCodec.FromJson(payload));
            payload=AssessmentRecordCodec.ToJson(Record());payload["stage"]="post_w4_optional";Assert.Throws<AssessmentFault>(()=>AssessmentRecordCodec.FromJson(payload));
            payload=AssessmentRecordCodec.ToJson(Record());payload["value"]=8;Assert.Throws<AssessmentFault>(()=>AssessmentRecordCodec.FromJson(payload));
            payload=AssessmentRecordCodec.ToJson(Record());payload["value"]=7.0;Assert.Throws<AssessmentFault>(()=>AssessmentRecordCodec.FromJson(payload));
            Assert.Throws<AssessmentFault>(()=>Record("optional_execution",1,"post_w4_optional","execute_A_SCAN",null,"completed"));
            Assert.Throws<AssessmentFault>(()=>Record("optional_help",1,"post_w4_optional","K-a1-r1",null,"completed"));
        }
        [Test] public void FailedStageFlushNeverAdvancesTheInMemoryRecordList()
        {
            using var journal=new DataJournal(SyntheticData.Folder("assessment-failed-flush"),SyntheticData.Identity,new string('3',32),()=>1);
            var adapter=new AssessmentDataJournal(journal,Schedule);journal.BeforeDurableFlush=()=>throw new IOException("synthetic flush failure");
            Assert.Throws<DataFault>(()=>adapter.Append(Record()));Assert.That(adapter.Records,Is.Empty);Assert.That(journal.Failed,Is.True);
        }
    }
}
