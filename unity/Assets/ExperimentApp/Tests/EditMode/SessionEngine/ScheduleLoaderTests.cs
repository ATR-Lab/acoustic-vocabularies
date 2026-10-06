using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.SessionEngine.Tests
{
    public sealed class ScheduleLoaderTests
    {
        static string Repo()
        {
            for(var path=new DirectoryInfo(Directory.GetCurrentDirectory());path!=null;path=path.Parent)
                if(File.Exists(Path.Combine(path.FullName,"schedules/schema/visit-schedule.schema.json")))return path.FullName;
            throw new Exception("Repository public schedule fixtures unavailable");
        }
        static (JObject doc,JObject permutation) Fixture(string study="A",string visit="D0")
        {
            string unit=study+"-C01",person=unit+(study=="A"?"-L01":"-M1");
            string root=Path.Combine(Repo(),"schedules/examples/demo",study,unit);
            var doc=SessionJson.Parse(File.ReadAllBytes(Path.Combine(root,"schedules",person,visit+".json")));
            var permutation=SessionJson.Parse(File.ReadAllBytes(Path.Combine(root,"permutation.json")));
            return (doc,permutation);
        }
        static JToken Block(JObject doc,string name)=>((JArray)doc["blocks"]).Single(x=>(string)x["block"]==name);
        static void Schemas(JObject doc,JObject permutation)
        {
            new ScheduleSchema(File.ReadAllBytes(Path.Combine(Repo(),"schedules/schema/visit-schedule.schema.json")),ScheduleLoader.ScheduleSchemaSha256).Validate(doc);
            new ScheduleSchema(File.ReadAllBytes(Path.Combine(Repo(),"schedules/schema/permutation.schema.json")),ScheduleLoader.PermutationSchemaSha256).Validate(permutation);
        }
        [TestCase("A","D0",5)][TestCase("A","D7",4)][TestCase("B","V1",7)][TestCase("B","V2",7)][TestCase("B","V3",7)][TestCase("B","W1",3)][TestCase("B","W4",4)]
        public void ActualPublicProducerVisitContractsPass(string study,string visit,int count)
        {
            var f=Fixture(study,visit);Schemas(f.doc,f.permutation);
            var result=ScheduleLoader.ValidateBlocks(f.doc,f.permutation);
            Assert.That(result.Length,Is.EqualTo(count));
            Assert.That(result.Where(x=>x.Name.Contains("lessons")).SelectMany(x=>x.Items).Any(x=>x.Heldout),Is.False);
        }
        [Test] public void UnknownNestedSchemaFieldsFailClosed()
        {
            var f=Fixture();Block(f.doc,"trained")["unexpected"]=true;
            Assert.Throws<SessionFault>(()=>Schemas(f.doc,f.permutation));
        }
        [Test] public void WrongAssessmentTotalIsNotTrusted()
        {
            var f=Fixture();f.doc["assessment"]["assessment_seconds"]=(int)f.doc["assessment"]["assessment_seconds"]+1;
            Assert.Throws<SessionFault>(()=>ScheduleLoader.ValidateBlocks(f.doc,f.permutation));
        }
        [Test] public void ReorderedBlocksAreRejected()
        {
            var f=Fixture();var blocks=(JArray)f.doc["blocks"];var first=blocks[0].DeepClone();blocks[0]=blocks[1].DeepClone();blocks[1]=first;
            Assert.Throws<SessionFault>(()=>ScheduleLoader.ValidateBlocks(f.doc,f.permutation));
        }
        [Test] public void DuplicateTrainedMessageWithinPassIsRejected()
        {
            var f=Fixture();var items=(JArray)Block(f.doc,"trained")["items"];
            foreach(string key in new[]{"message_id","intended"})items[1][key]=items[0][key].DeepClone();
            Assert.Throws<SessionFault>(()=>ScheduleLoader.ValidateBlocks(f.doc,f.permutation));
        }
        [Test] public void HeldoutLessonCannotBeSmuggledThroughAnIntendedTuple()
        {
            var f=Fixture();var target=Block(f.doc,"message_lessons")["items"][0];var novel=Block(f.doc,"novel")["items"][0];
            foreach(string key in new[]{"message_id","intended","trained_status"})target[key]=novel[key].DeepClone();
            Assert.Throws<SessionFault>(()=>ScheduleLoader.ValidateBlocks(f.doc,f.permutation));
        }
        [Test] public void HeldoutDictionaryIsRejected()
        {
            var f=Fixture();((JArray)f.doc["dictionary_messages"])[0]="K-a1-r2";
            Assert.Throws<SessionFault>(()=>ScheduleLoader.ValidateBlocks(f.doc,f.permutation));
        }
        [Test] public void LessonFamilyAlternationIsCheckedIndependentlyOfPool()
        {
            var f=Fixture();var rows=(JArray)Block(f.doc,"message_lessons")["items"];
            foreach(string key in new[]{"message_id","intended"}){var first=rows[0][key].DeepClone();rows[0][key]=rows[1][key].DeepClone();rows[1][key]=first;}
            Assert.Throws<SessionFault>(()=>ScheduleLoader.ValidateBlocks(f.doc,f.permutation));
        }
        [Test] public void StoredNoCueDrawsMustMatchButMayRepeat()
        {
            var f=Fixture("A","D7");ScheduleLoader.ValidateBlocks(f.doc,f.permutation);
            var values=(JArray)Block(f.doc,"validity")["validity"]["no_cue_targets"];
            values[0]=(string)values[0]=="K-a1-r1"?"Q-a1-r1":"K-a1-r1";
            Assert.Throws<SessionFault>(()=>ScheduleLoader.ValidateBlocks(f.doc,f.permutation));
        }
        [Test] public void SpeechCoverageCannotRepeatOneCommandEightTimes()
        {
            var f=Fixture("A","D7");var rows=((JArray)Block(f.doc,"validity")["items"]).Where(x=>(string)x["trial_type"]=="speech").ToArray();
            foreach(string key in new[]{"speech_id","intended"})rows[1][key]=rows[0][key].DeepClone();
            Assert.Throws<SessionFault>(()=>ScheduleLoader.ValidateBlocks(f.doc,f.permutation));
        }
        [TestCase("{\"x\":1,}")][TestCase("{'x':1}")][TestCase("{\"x\":NaN}")][TestCase("{\"x\":1,\"x\":2}")][TestCase("{\"x\":1} //comment")]
        public void ExtendedOrAmbiguousJsonIsRejected(string text)
        { Assert.Throws<SessionFault>(()=>SessionJson.Parse(System.Text.Encoding.UTF8.GetBytes(text))); }
    }
}
