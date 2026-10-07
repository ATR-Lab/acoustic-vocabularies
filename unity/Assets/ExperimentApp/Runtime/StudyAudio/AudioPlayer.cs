using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using UnityEngine;
using UnityEngine.Profiling;

namespace AcousticVocab.StudyAudio
{
    public sealed class AudioPlaybackEvent
    {
        public string Code { get; }
        public string AudioId { get; }
        public string PcmSha256 { get; }
        public string ActionPcmSha256 { get; }
        public string ReferentPcmSha256 { get; }
        public double? FirstOutputCallbackDspSeconds { get; }
        public AudioScheduleTiming Timing { get; }
        public double ObservedMonoSeconds { get; }
        public long DeliveredSamples { get; }
        public long CallbackCount { get; }
        internal AudioPlaybackEvent(string code,string id,string hash,string actionHash,string referentHash,AudioScheduleTiming timing,double observed,long samples,long callbacks,double? callbackDsp)
        { Code=code;AudioId=id;PcmSha256=hash;ActionPcmSha256=actionHash;ReferentPcmSha256=referentHash;Timing=timing;ObservedMonoSeconds=observed;DeliveredSamples=samples;CallbackCount=callbacks;FirstOutputCallbackDspSeconds=callbackDsp; }
    }

    [DisallowMultipleComponent,RequireComponent(typeof(AudioSource))]
    public sealed class AudioPlayer : MonoBehaviour
    {
        sealed class Prepared { public AudioClip Clip;public PcmWave Wave; }
        sealed class Ticket
        {
            public string Id,Hash,ActionHash,ReferentHash;public AudioScheduleTiming Timing;public AudioDelivery Delivery;
            public int Samples;public bool OnsetReported;public AudioClip Clip;
        }
        readonly Dictionary<string,Prepared> prepared=new Dictionary<string,Prepared>(StringComparer.Ordinal);
        readonly DspClockMapping mapping=new DspClockMapping();
        AudioSource output;
        Ticket current;
        Func<bool> gate;
        AudioRouteCalibration route;
        bool configured,failed,scheduling;
        int outputRate,bufferFrames,bufferCount;
        float gain=.1f;
        public bool Ready => isActiveAndEnabled && configured && !failed && !scheduling && prepared.Count>0 && current==null;
        public bool Playing => current!=null;
        public bool TrialReady => Ready && route.CanScheduleSoftware;
        public float CurrentGain => gain;
        public long EstimatedPreloadBytes { get; private set; }
        public long UnityAllocatedBytes { get; private set; }
        public event Action<AudioPlaybackEvent> Event;
        public static double Now => (double)Stopwatch.GetTimestamp()/Stopwatch.Frequency;
        public static void ConfigureSimulationDevice(AcousticVocab.Foundation.SimulationTestAuthority authority)
        {
            if(authority==null||!AcousticVocab.Foundation.SimulationTestAuthority.CompiledCapability)throw new AudioFault("AUDIO_SIMULATION_AUTHORITY");
            var request=AudioSettings.GetConfiguration();request.sampleRate=48000;request.dspBufferSize=512;
            if(!AudioSettings.Reset(request))throw new AudioFault("AUDIO_SIMULATION_DEVICE_REQUEST");
            AudioSettings.GetDSPBufferSize(out int frames,out int count);
            if(AudioSettings.outputSampleRate!=48000||frames<=0||frames>512||count<=0)throw new AudioFault("AUDIO_SIMULATION_DEVICE_FORMAT");
            UnityEngine.Debug.Log("SIMULATION_AUDIO_DEVICE sample_rate="+AudioSettings.outputSampleRate+" dsp_frames="+frames+" dsp_count="+count+" acoustic_qualified=false");
        }

        void Awake()
        {
            output=GetComponent<AudioSource>(); SetSource();
            AudioSettings.OnAudioConfigurationChanged+=OnAudioConfigurationChanged;
        }
        void SetSource()
        {
            output.playOnAwake=false;output.loop=false;output.spatialBlend=0;output.panStereo=0;
            output.dopplerLevel=0;output.pitch=1;output.volume=gain;output.priority=0;
            output.spatialize=false;output.spatializePostEffects=false;
            // Keep the observational OnAudioFilterRead callback enabled. No
            // effect component is allowed beside this non-mutating observer.
            output.bypassEffects=false;output.bypassListenerEffects=true;output.bypassReverbZones=true;
        }
        public void Configure(AudioRouteCalibration calibration,Func<bool> exposureGate)
        {
            if(scheduling || current!=null || calibration==null || exposureGate==null || Event==null) throw new AudioFault("AUDIO_CONFIGURATION_INVALID");
            outputRate=AudioSettings.outputSampleRate;AudioSettings.GetDSPBufferSize(out bufferFrames,out bufferCount);
            if(outputRate!=48000 || bufferFrames<1 || bufferFrames>8192 || bufferCount<1) throw new AudioFault("AUDIO_DEVICE_FORMAT");
            route=calibration;gate=exposureGate;configured=true;failed=false;mapping.Reset();
            SetSource();
        }
        public void SetComfortableGain(float value)
        {
            if(scheduling || current!=null || float.IsNaN(value) || float.IsInfinity(value) || value<=0 || value>1)
                throw new AudioFault("AUDIO_GAIN_INVALID");
            gain=value;if(output!=null) output.volume=value;
        }
        public void Preload(IReadOnlyDictionary<string,PcmWave> waves,long budgetBytes)
        {
            if(!configured || failed || scheduling || current!=null || waves==null || waves.Count==0 || waves.Count>256 || budgetBytes<=0 || budgetBytes>64*1024*1024)
                throw new AudioFault("AUDIO_PRELOAD_INVALID");
            ClearPrepared();long bytes=0;
            try
            {
                foreach(var entry in waves)
                {
                    if(string.IsNullOrEmpty(entry.Key) || entry.Key.Length>80 || entry.Value==null) throw new AudioFault("AUDIO_PRELOAD_INVALID");
                    bytes=checked(bytes+entry.Value.SampleCount*10L); // PCM + float copy + native clip estimate.
                    if(bytes>budgetBytes) throw new AudioFault("AUDIO_MEMORY_BUDGET");
                    var clip=AudioClip.Create("verified-audio",entry.Value.SampleCount,1,48000,false);
                    if(clip==null) throw new AudioFault("AUDIO_ALLOCATION_FAILED");
                    try
                    {
                        if(!clip.SetData(entry.Value.CopySamples(),0)) throw new AudioFault("AUDIO_PRELOAD_FAILED");
                        prepared.Add(entry.Key,new Prepared { Clip=clip,Wave=entry.Value });
                    }
                    catch { Destroy(clip);throw; }
                }
                EstimatedPreloadBytes=bytes;UnityAllocatedBytes=Profiler.GetTotalAllocatedMemoryLong();
                if(SystemInfo.systemMemorySize>0 && UnityAllocatedBytes>SystemInfo.systemMemorySize*1024L*1024L*3/4)
                    throw new AudioFault("AUDIO_MEMORY_HEADROOM");
            }
            catch { ClearPrepared();failed=true;throw; }
        }
        public void Schedule(string audioId,double requestedOnsetMonoSeconds) => ScheduleCore(audioId,requestedOnsetMonoSeconds,false);
        public void ScheduleCalibration(string audioId,double requestedStartMonoSeconds) => ScheduleCore(audioId,requestedStartMonoSeconds,true);
        void ScheduleCore(string audioId,double requestedOnsetMonoSeconds,bool calibrationOnly)
        {
            if(!Ready || Event==null || !gate() || !prepared.TryGetValue(audioId,out var item)) throw new AudioFault("AUDIO_NOT_READY");
            scheduling=true;
            try
            {
                CheckSource();
                double before=Now,dsp=AudioSettings.dspTime,after=Now;
                mapping.Observe(before,dsp,after,(double)bufferFrames/outputRate);
                var timing=mapping.Schedule(Now,requestedOnsetMonoSeconds,route,Math.Max(.1,(double)bufferFrames*bufferCount/outputRate),calibrationOnly);
                var ticket=new Ticket { Id=audioId,Hash=item.Wave.PcmSha256,ActionHash=item.Wave.ActionPcmSha256,ReferentHash=item.Wave.ReferentPcmSha256,Timing=timing,Samples=item.Wave.SampleCount,Clip=item.Clip,
                    Delivery=new AudioDelivery(timing.ScheduledDspSeconds,item.Wave.SampleCount) };
                // A request is persisted before scheduling. Returning from this
                // API never counts as delivery or completion.
                Emit("AUDIO_REQUESTED",ticket);
                if(failed || !isActiveAndEnabled || !gate()) throw new AudioFault("AUDIO_EXPOSURE_BLOCKED");
                CheckSource();
                output.clip=item.Clip;Volatile.Write(ref current,ticket);output.PlayScheduled(timing.ScheduledDspSeconds);
            }
            catch { Abort("AUDIO_SCHEDULE_FAILED");throw; }
            finally { scheduling=false; }
        }
        void CheckSource()
        {
            if(!output.enabled || output.mute || !output.gameObject.activeInHierarchy || current!=null && output.clip!=current.Clip ||
                output.spatialBlend!=0 || output.panStereo!=0 || output.dopplerLevel!=0 || output.pitch!=1 || output.loop ||
                output.spatialize || output.volume!=gain || output.outputAudioMixerGroup!=null ||
                output.bypassEffects || !output.bypassListenerEffects || !output.bypassReverbZones)
                throw new AudioFault("AUDIO_PATH_CHANGED");
            foreach(var component in GetComponents<Component>())
                if(component is not Transform && component is not AudioSource && component is not AudioPlayer)
                    throw new AudioFault("AUDIO_PATH_CHANGED");
        }
        void Update()
        {
            var ticket=current;if(ticket==null) return;
            try
            {
                if(!gate()) { Abort("AUDIO_EXPOSURE_INTERRUPTED");return; }
                CheckSource();
                if(ticket.Delivery.Status==AudioDelivery.Underrun || ticket.Delivery.Status==AudioDelivery.InvalidCallback)
                { Abort(ticket.Delivery.DeadlineFault());return; }
                if(!ticket.OnsetReported && ticket.Delivery.CallbackCount>0)
                { Emit(ticket.Timing.SimulationOnly?"SIMULATION_DELIVERY_OBSERVED":ticket.Timing.CalibrationOnly?"CALIBRATION_DELIVERY_OBSERVED":"AUDIO_ONSET_ESTIMATED",ticket);ticket.OnsetReported=true; }
                // Processing ahead in the DSP does not mean the audible interval
                // has elapsed; completion is withheld through estimated offset.
                double end=(ticket.Timing.OnsetEstimateMonoSeconds??ticket.Timing.ScheduledMonoSeconds)+(double)ticket.Samples/48000;
                if(ticket.Delivery.Status==AudioDelivery.Complete && Now>=end)
                { Emit("AUDIO_PLAYBACK_COMPLETED",ticket);Volatile.Write(ref current,null);output.Stop(); }
                else if(Now>end+Math.Max(.25,(double)bufferFrames*bufferCount/outputRate)) Abort(ticket.Delivery.DeadlineFault());
            }
            catch(AudioFault fault) { Abort(fault.Code); }
            catch { Abort("AUDIO_EVIDENCE_FAILED"); }
        }
        void OnAudioFilterRead(float[] data,int channels)
        {
            var ticket=Volatile.Read(ref current);if(ticket==null) return;
            int count=channels>0 && data.Length%channels==0 ? data.Length/channels : 0;
            ticket.Delivery.Observe(AudioSettings.dspTime,count,outputRate);
            // Observational only: never write to data or synthesize samples.
        }
        void Emit(string code,Ticket ticket)
        {
            var sink=Event;if(sink==null) throw new AudioFault("AUDIO_EVIDENCE_UNAVAILABLE");
            sink(new AudioPlaybackEvent(new AudioFault(code).Code,ticket.Id,ticket.Hash,ticket.ActionHash,ticket.ReferentHash,ticket.Timing,Now,ticket.Delivery.CoveredSamples,ticket.Delivery.CallbackCount,ticket.Delivery.FirstOutputCallbackDspSeconds));
        }
        public void Abort(string code="AUDIO_CANCELLED")
        {
            var ticket=current;Volatile.Write(ref current,null);failed=true;mapping.Reset();if(output!=null) output.Stop();
            if(ticket!=null) { try { Emit(code??"AUDIO_UNDERRUN",ticket); } catch { UnityEngine.Debug.LogError("AUDIO_EVIDENCE_FAILED"); } }
        }
        void OnAudioConfigurationChanged(bool _) { if(configured) Abort("AUDIO_DEVICE_CHANGED"); }
        void OnApplicationPause(bool paused) { if(paused && configured) Abort("AUDIO_APPLICATION_PAUSED"); }
        void OnApplicationFocus(bool focused) { if(!focused && configured) Abort("AUDIO_FOCUS_LOST"); }
        void OnDisable() { if(configured) Abort("AUDIO_COMPONENT_DISABLED"); }
        // Release the source's reference before destroying any clip, so the
        // audio thread never sees a destroyed clip still assigned at teardown.
        void ClearPrepared()
        {
            if(output!=null) { output.Stop();output.clip=null; }
            foreach(var item in prepared.Values) if(item.Clip!=null) Destroy(item.Clip);prepared.Clear();EstimatedPreloadBytes=0;
        }
        void OnDestroy() { AudioSettings.OnAudioConfigurationChanged-=OnAudioConfigurationChanged;Abort("AUDIO_SHUTDOWN");ClearPrepared(); }
    }
}
