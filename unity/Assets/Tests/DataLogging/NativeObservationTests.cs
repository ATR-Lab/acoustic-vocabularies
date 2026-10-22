using System;
using System.IO;
using System.Linq;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.DataLogging.Tests
{
    public sealed class NativeObservationTests
    {
        static readonly VisitSchedule Schedule=VisitSchedule.SyntheticMockBlock(1);
        static SlotContext Context()=>new SlotContext(Schedule.Blocks[0].Items[0],1000,null);
        static void Load(DataJournal journal,SlotContext context)
        {
            var p=SessionRecordCodec.ToJson(SyntheticData.Session(context.Item.TrialId,"Loaded",audible:"NotRequested",requestId:context.AudioRequestIds[0]));
            p["schedule_sha256"]=Schedule.Sha256;p["scheduled_onset_mono_ms"]=context.OnsetMonoMs;
            new SessionDataJournal(journal).Append(SessionRecordCodec.FromJson(p));
        }
        static string NativeRaw()
        {
            string raw=SyntheticData.Folder("native-observations");double now=0;
            using(var journal=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>now++))
            {
                var context=Context();Load(journal,context);NativeObservationCodec.BindSlot(journal,Schedule,context);
                journal.Append(SyntheticData.Audio(new EventContext(context.OpportunityId,context.Item.TrialId,context.AudioRequestIds[0])));
                journal.Append(SyntheticData.Audio(new EventContext(context.OpportunityId,context.Item.TrialId,context.AudioRequestIds[0]),"AUDIO_PLAYBACK_COMPLETED",true,false));
                journal.Append(SyntheticData.Panel(new EventContext(context.OpportunityId,context.Item.TrialId),"commit"));
            }
            return raw;
        }
        [Test]public void ExecutedSlotRequiresLoadedSourceAndPreservesOpaqueContext()
        {
            string raw=SyntheticData.Folder("native-slot-binding");using var journal=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>1);
            var context=Context();Assert.That(Assert.Throws<DataFault>(()=>NativeObservationCodec.BindSlot(journal,Schedule,context)).Code,Is.EqualTo("DATA_NATIVE_LOADED_REQUIRED"));
            Load(journal,context);NativeObservationCodec.BindSlot(journal,Schedule,context);NativeObservationCodec.BindSlot(journal,Schedule,context);
            var row=journal.Records.Single(x=>x.Kind=="slot_context");var p=row.Payload;
            Assert.That(p["person_slot"].Value<string>(),Is.EqualTo(Schedule.PersonSlot));Assert.That((string)p["content_id"],Is.EqualTo(context.Item.ContentId));
            Assert.That((string)p["dictionary_policy"],Is.EqualTo("prohibited"));Assert.That((string)p["feedback_policy"],Is.EqualTo("prohibited"));
            Assert.That((string)p["loaded_event_sha256"],Is.EqualTo(journal.Records[0].Sha256));
            Assert.That((string)p["observation_clock_epoch"],Is.EqualTo(journal.ClockEpoch));Assert.That((string)p["session_clock_epoch"],Is.EqualTo(new string('2',32)));
            Assert.That(p.Properties().Any(x=>new[]{"intended","target_action","target_referent","exact_correct"}.Contains(x.Name)),Is.False);
            var changed=(JObject)p.DeepClone();changed["intended"]=new JObject();Assert.Throws<DataFault>(()=>new EventDraft("slot_context",row.Context,changed));
        }
        [Test]public void NativeProjectionKeepsSourceHashesResponseTimesAndCallbackBasisSeparate()
        {
            string raw=NativeRaw(),output=SyntheticData.Folder("native-export");var snapshot=DataJournal.Verify(raw,SyntheticData.Identity);
            var bundle=ExportBundle.Create(raw,output,SyntheticData.Identity,ExportHeaders.Provisional());
            var manifest=JObject.Parse(File.ReadAllText(Path.Combine(output,"manifest.json")));var projection=JObject.Parse(File.ReadAllText(Path.Combine(output,NativeObservationExport.FileName)));
            Assert.That((string)manifest["schema_version"],Is.EqualTo(NativeObservationExport.ManifestVersion));Assert.That(bundle.HeadersQualified,Is.False);
            Assert.That((bool)projection["acoustic_authority"],Is.False);Assert.That((string)projection["evidence_level"],Is.EqualTo("software_event_calls"));
            foreach(JObject e in (JArray)projection["events"])
            {
                var source=snapshot.Records.Single(x=>x.Sha256==(string)e["source_event_sha256"]);var envelope=source.ToJson();
                Assert.That((long)e["sequence"],Is.EqualTo(source.Sequence));Assert.That(JToken.DeepEquals(e["payload"],source.Payload),Is.True);
                Assert.That(JToken.DeepEquals(e["clock_epoch"],envelope["clock_epoch"]),Is.True);Assert.That(JToken.DeepEquals(e["host_mono_ms"],envelope["host_mono_ms"]),Is.True);
            }
            var response=projection["events"].Single(x=>(string)x["kind"]=="panel_response");Assert.That((double)response["payload"]["observed_mono_ms"],Is.EqualTo(1300));
            var completion=projection["events"].Single(x=>(string)x["kind"]=="audio_observation");Assert.That((string)completion["payload"]["code"],Is.EqualTo("AUDIO_PLAYBACK_COMPLETED"));
            Assert.That(completion["payload"]["onset_estimate_mono_ms"].Type,Is.EqualTo(JTokenType.Null));Assert.That(completion["payload"]["audio_offset_mono_ms"],Is.Null);
            Assert.That(File.ReadLines(Path.Combine(output,"trial-log.csv")).First(),Is.EqualTo(string.Join(",",DataDeriver.TrialHeaders)));
            ExportBundle.Load(output,bundle.ManifestSha256).VerifyAll();
        }
        [Test]public void UnplayedReplacementContextsRetainBothLoadedBindings()
        {
            string raw=SyntheticData.Folder("native-replaced-unplayed");double now=0;
            using(var journal=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>now++))
            {
                var first=Context();Load(journal,first);NativeObservationCodec.BindSlot(journal,Schedule,first);
                var next=new SlotContext(first.Item,2000,null);Load(journal,next);NativeObservationCodec.BindSlot(journal,Schedule,next);
                Assert.That(journal.Records.Count(x=>x.Kind=="slot_context"),Is.EqualTo(2));
                Assert.That(journal.Records.Where(x=>x.Kind=="slot_context").Select(x=>(string)x.Payload["loaded_event_sha256"]).Distinct().Count(),Is.EqualTo(2));
            }
            Assert.That(DataJournal.Verify(raw,SyntheticData.Identity).Records.Count(x=>x.Kind=="slot_context"),Is.EqualTo(2));
        }
        [Test]public void RehashedNativeProjectionCannotChangeRecordedFactsOrDowngradeFormat()
        {
            string raw=NativeRaw(),output=SyntheticData.Folder("native-tamper");ExportBundle.Create(raw,output,SyntheticData.Identity,ExportHeaders.Provisional());
            string manifestPath=Path.Combine(output,"manifest.json"),nativePath=Path.Combine(output,NativeObservationExport.FileName);
            var projection=JObject.Parse(File.ReadAllText(nativePath));projection["events"][0]["payload"]["technical_fault_code"]="TAMPERED";
            byte[] bytes=DataJson.Bytes(projection);File.WriteAllBytes(nativePath,bytes);var manifest=JObject.Parse(File.ReadAllText(manifestPath));
            var entry=manifest["files"].Single(x=>(string)x["path"]==NativeObservationExport.FileName);entry["sha256"]=DataJson.HashBytes(bytes);entry["bytes"]=bytes.Length;
            byte[] changed=DataJson.Bytes(manifest);File.WriteAllBytes(manifestPath,changed);
            Assert.That(Assert.Throws<DataFault>(()=>ExportBundle.Load(output,DataJson.HashBytes(changed))).Code,Is.EqualTo("DATA_NATIVE_PROJECTION_CHANGED"));
            manifest["schema_version"]="data-export-provisional-1";changed=DataJson.Bytes(manifest);File.WriteAllBytes(manifestPath,changed);
            Assert.That(Assert.Throws<DataFault>(()=>ExportBundle.Load(output,DataJson.HashBytes(changed))).Code,Is.EqualTo("DATA_NATIVE_FORMAT"));
            entry.Remove();File.Delete(nativePath);changed=DataJson.Bytes(manifest);File.WriteAllBytes(manifestPath,changed);
            Assert.That(Assert.Throws<DataFault>(()=>ExportBundle.Load(output,DataJson.HashBytes(changed))).Code,Is.EqualTo("DATA_NATIVE_FORMAT"));
        }
    }
}
