using System;
using System.Linq;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.DataLogging
{
    // Reserved familiarization observations have no trial or exposure identity.
    // Software callback completion does not assert acoustic delivery.
    public static class GrammarStageCodec
    {
        public static void Validate(JObject p)
        {
            DataJson.Keys(p,"version","schedule_sha256","registry_sha256","review_sha256","clock_epoch","host_mono_ms","kind","phase","audio_request_id","operator_command","audio");
            DataJson.Require(DataJson.Integer(p["version"])==1);
            foreach(string key in new[]{"schedule_sha256","registry_sha256","review_sha256"})DataJson.Require(DataJson.Hash(DataJson.Text(p[key])));
            DataJson.Require(DataJson.Guid(DataJson.Text(p["clock_epoch"])));DataJson.Number(p["host_mono_ms"]);
            string kind=DataJson.Text(p["kind"]),phase=DataJson.OptionalText(p["phase"]),id=DataJson.OptionalText(p["audio_request_id"]);
            DataJson.Require(new[]{"started","request","audio","completed","finished","interrupted"}.Contains(kind));
            bool play=kind is "request" or "audio" or "completed";
            DataJson.Require(play?new[]{"ready","clicks"}.Contains(phase)&&DataJson.Guid(id):phase==null&&id==null);
            if(kind=="started")
            {var command=p["operator_command"];DataJson.Keys(command,"session_nonce","request_id","sequence");DataJson.Require(DataJson.Guid(DataJson.Text(command["session_nonce"]))&&DataJson.Guid(DataJson.Text(command["request_id"]))&&DataJson.Integer(command["sequence"])>0);}
            else DataJson.Require(p["operator_command"].Type==JTokenType.Null);
            if(kind=="audio"){DataEventSchema.Audio(p["audio"] as JObject);DataJson.Require((string)p["audio"]["audio_id"]==id);}
            else DataJson.Require(p["audio"].Type==JTokenType.Null);
        }
        public static JObject Audio(AudioPlaybackEvent value,string waveformHash)
        {
            var t=value?.Timing??throw new DataFault("DATA_AUDIO_TIMING");
            var result=new JObject{["code"]=value.Code,["audio_id"]=value.AudioId,["waveform_sha256"]=waveformHash,["pcm_sha256"]=value.PcmSha256,["action_pcm_sha256"]=value.ActionPcmSha256,["referent_pcm_sha256"]=value.ReferentPcmSha256,
                ["observed_mono_ms"]=value.ObservedMonoSeconds*1000,["request_mono_ms"]=t.RequestMonoSeconds*1000,["scheduled_mono_ms"]=t.ScheduledMonoSeconds*1000,["scheduled_dsp_s"]=t.ScheduledDspSeconds,
                ["onset_estimate_mono_ms"]=t.OnsetEstimateMonoSeconds*1000,["onset_uncertainty_ms"]=t.OnsetUncertaintyMs,["first_callback_dsp_s"]=value.FirstOutputCallbackDspSeconds,["delivered_samples"]=value.DeliveredSamples,["callback_count"]=value.CallbackCount};
            if(t.SimulationOnly){result["simulation_test"]=true;result["software_output_estimate_mono_ms"]=t.SoftwareOutputEstimateMonoSeconds*1000;result["software_output_uncertainty_ms"]=t.SoftwareOutputUncertaintyMs;}
            return result;
        }
    }
}
