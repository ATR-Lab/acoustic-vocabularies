using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.DataLogging.Tests
{
    public sealed class AdapterIntegrationTests
    {
        static T Construct<T>(params object[] values)=>(T)Activator.CreateInstance(typeof(T),BindingFlags.Instance|BindingFlags.NonPublic,null,values,null);
        [Test] public void ActualAudioEventAdapterPreservesClockUnitsWithoutInferringAcousticOnset()
        {
            string raw=SyntheticData.Folder("audio-adapter");var go=new GameObject("Synthetic audio adapter");go.SetActive(false);var player=go.AddComponent<AudioPlayer>();
            try
            {
                using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>100000))
                using(var adapter=new AudioDataAdapter(writer,player,_=>new AudioRequestContext(new EventContext("t","t",new string('5',32)),"synthetic",new string('c',64))))
                {
                    var timing=Construct<AudioScheduleTiming>(10d,11d,100d,(double?)11.02,(double?)4d,(double?)20d);
                    AudioPlaybackEvent Event(string code,long samples,long callbacks,double? dsp)=>Construct<AudioPlaybackEvent>(code,"synthetic",new string('d',64),null,null,timing,12d,samples,callbacks,dsp);
                    adapter.Record(Event("AUDIO_REQUESTED",0,0,null));adapter.Record(Event("AUDIO_PLAYBACK_COMPLETED",48000,100,99.98));
                    Assert.That((double)writer.Records[0].Payload["request_mono_ms"],Is.EqualTo(10000));Assert.That((double)writer.Records[1].Payload["first_callback_dsp_s"],Is.EqualTo(99.98));
                }
                var row=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity).Exposures.Single();Assert.That(row["audible_status"],Is.EqualTo("estimated"));Assert.That(row["audio_onset_estimate_mono_ms"],Is.EqualTo("11020"));Assert.That(row["onset_uncertainty_ms"],Is.EqualTo("4"));
            }
            finally{UnityEngine.Object.DestroyImmediate(go);}
        }
        [Test] public void GainDeviceFreezeAndSignedDeviationReferencesRemainTyped()
        {
            string raw=SyntheticData.Folder("device-gain");var c=new EventContext("t","t");
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>10))
            {
                var gains=new ComfortableGainStore(SyntheticData.Folder("gain-profile"));writer.Append(DataObservations.Gain(gains.ChangeForComfort("SYNTHETIC",new string('8',32),.2f,15,true)));
                writer.Append(DataObservations.Device(c,"focus",1,false));writer.Append(DataObservations.Device(c,"frame_freeze",2,durationMs:75));writer.Append(DataObservations.Device(c,"controller_exception",3,code:"INPUT_LOST"));writer.Append(DataObservations.DeviationReference(c,"synthetic-deviation",new string('f',64),4));
                Assert.That((double)writer.Records[0].Payload["observed_mono_ms"],Is.EqualTo(15000));
            }
            var row=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity).Trials.Single();Assert.That(row["focus_ok"],Is.EqualTo("false"));Assert.That(row["frame_freeze_ms"],Is.EqualTo("75"));Assert.That(row["deviation_id"],Is.EqualTo("synthetic-deviation"));Assert.That(row["technical_fault_code"],Does.Contain("INPUT_LOST"));
        }
        [Test] public void ForeignThreadAppendLatchesWithoutWriting()
        {
            using var writer=new DataJournal(SyntheticData.Folder("thread"),SyntheticData.Identity,new string('3',32),()=>0);
            Task.Run(()=>Assert.Throws<DataFault>(()=>writer.Append(DataObservations.Device(null,"focus",1,true)))).GetAwaiter().GetResult();Assert.That(writer.Failed,Is.True);Assert.That(writer.Records,Is.Empty);
        }
        [TestCase("choice")][TestCase("audio_evidence")][TestCase("audio_observation")]
        public void ExistingPlaybackCannotBeReassignedToAnotherAttempt(string kind)
        {
            string raw=SyntheticData.Folder("wrong-context");var correct=new EventContext("t","t",new string('5',32));var wrong=new EventContext("other","other",new string('5',32));
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>0))
            {writer.Append(SyntheticData.Audio(correct));writer.Append(kind=="choice"?DataObservations.Choice(wrong,"candidate","rejected",null,0,null):kind=="audio_evidence"?DataObservations.TrustedAudioEvidence(wrong,"confirmed_no_onset",new string('f',64),"trusted_delivery_evidence",1):SyntheticData.Audio(wrong,"AUDIO_PLAYBACK_COMPLETED",true,true));}
            Assert.Throws<DataFault>(()=>DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity));
        }
        sealed class Clock : ISessionClock {internal double Time;public double NowMs=>Time;}
        sealed class Factory : ISlotContentFactory
        {
            readonly DataJournal journal;internal int Plays;internal Factory(DataJournal journal){this.journal=journal;}
            public ISlotContent Create(SlotItem item)=>new Content(this,journal);
        }
        sealed class Content : ISlotContent
        {
            readonly Factory owner;readonly DataJournal journal;internal Content(Factory owner,DataJournal journal){this.owner=owner;this.journal=journal;}
            public SlotReadiness Readiness=>new SlotReadiness(true,true,true,true,true,true,true,true);
            public bool ResetComplete{get;private set;}
            public void Prepare(SlotContext c){}
            public void RequestCue(SlotContext c,INovelSlotAuthorization permit){journal.Append(SyntheticData.Audio(new EventContext(c.OpportunityId,c.Item.TrialId,c.AudioRequestIds[0])));owner.Plays++;}
            public void OpenResponse(SlotContext c){} public void CloseResponse(SlotContext c){}public void RequestReset(SlotContext c){ResetComplete=true;}public void Interrupt(string code){}
        }
        [Test] public void RealSessionEngineProducesThirtySixDurableAttemptsAndOneRowPerRequestedPlay()
        {
            string raw=SyntheticData.Folder("engine-36");var clock=new Clock();
            using(var writer=new DataJournal(raw,SyntheticData.Identity,new string('3',32),()=>clock.Time))
            {
                var items=Enumerable.Range(0,36).Select(i=>Construct<SlotItem>("SYNTHETIC-"+i,"trained","synthetic-content",null,null,"protected",false,14,1,1)).ToArray();
                var block=Construct<ScheduleBlock>("trained",items);var schedule=Construct<VisitSchedule>(new string('b',64),new string('c',64),"SYNTHETIC","DEMO",true,new[]{block},"A","A",null);var factory=new Factory(writer);var engine=new FixedSlotEngine(schedule,clock,new SessionDataJournal(writer),factory);
                foreach(var item in items)writer.Append(DataObservations.Opportunity(item.TrialId,"primary","synthetic-masked",schedule.Sha256));
                engine.ConfirmResume();engine.Tick();string responded=null;
                while(engine.Status==SessionState.Running&&clock.Time<505000){clock.Time+=50;if(engine.CurrentTrialId=="SYNTHETIC-35"&&engine.CurrentState==ItemState.ResponseOpen&&clock.Time==750+35*14000+12000)engine.RecordResponse("timeout");engine.Tick();if(engine.CurrentState==ItemState.ResponseOpen&&responded!=engine.CurrentTrialId&&engine.CurrentTrialId!="SYNTHETIC-35"){responded=engine.CurrentTrialId;engine.RecordResponse("commit");}}
                Assert.That(engine.Status,Is.EqualTo(SessionState.Complete));Assert.That(factory.Plays,Is.EqualTo(36));Assert.That(clock.Time,Is.EqualTo(504750));
            }
            var tables=DataDeriver.Derive(DataJournal.Verify(raw,SyntheticData.Identity),SyntheticData.Identity);Assert.That(tables.Trials.Count,Is.EqualTo(36));Assert.That(tables.Exposures.Count,Is.EqualTo(36));Assert.That(tables.Trials.Last()["response_code"],Is.EqualTo("timeout"));Assert.That(tables.Exposures.All(x=>x["audible_status"]=="uncertain"&&x["exposure_consumed"]=="true"),Is.True);
            string export=raw+"-export";var bundle=ExportBundle.Create(raw,export,SyntheticData.Identity,ExportHeaders.Provisional());File.WriteAllText(raw+"-manifest-hash.txt",bundle.ManifestSha256+"\n");
        }
    }
}
