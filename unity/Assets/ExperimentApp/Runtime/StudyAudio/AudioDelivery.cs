using System;
using System.Threading;

namespace AcousticVocab.StudyAudio
{
    // One immutable schedule, one audio-thread producer. It observes unmodified
    // output callback coverage; it cannot prove speaker/earphone delivery.
    public sealed class AudioDelivery
    {
        public const int Pending=0, Complete=1, Underrun=2, InvalidCallback=3;
        readonly double start;
        readonly int samples, rate;
        long covered, callbacks;
        int status;
        public long CoveredSamples => Interlocked.Read(ref covered);
        public long CallbackCount => Interlocked.Read(ref callbacks);
        public int Status => Volatile.Read(ref status);
        public AudioDelivery(double scheduledDspSeconds,int sampleCount,int sampleRate=48000)
        {
            if(!AudioRouteCalibration.Finite(scheduledDspSeconds) || scheduledDspSeconds<0 || sampleCount<=0 ||
                sampleCount>480000 || sampleRate!=48000) throw new AudioFault("AUDIO_DELIVERY_CONFIG");
            start=scheduledDspSeconds; samples=sampleCount; rate=sampleRate;
        }
        // No allocation, logging, file I/O, locks or Unity object access here.
        public void Observe(double callbackDspSeconds,int frameCount,int outputRate)
        {
            if(Status!=Pending) return;
            if(!AudioRouteCalibration.Finite(callbackDspSeconds) || callbackDspSeconds<0 || frameCount<=0 ||
                Math.Abs(callbackDspSeconds-start)>60 || frameCount>65536 || outputRate!=rate)
            { Volatile.Write(ref status,InvalidCallback); return; }
            long from=(long)Math.Round((callbackDspSeconds-start)*rate);
            long to=from+frameCount;
            if(to<=0 || from>=samples)
            {
                if(from>=samples && CoveredSamples<samples) Volatile.Write(ref status,Underrun);
                return;
            }
            from=Math.Max(0,from); to=Math.Min(samples,to);
            long prior=CoveredSamples;
            if(from>prior+1) { Volatile.Write(ref status,Underrun); return; }
            if(to<=prior) { Volatile.Write(ref status,InvalidCallback); return; }
            Interlocked.Increment(ref callbacks);
            Interlocked.Exchange(ref covered,to);
            if(to>=samples) Volatile.Write(ref status,Complete);
        }
        public string DeadlineFault()
        {
            if(Status==Complete) return null;
            if(Status==InvalidCallback) return "AUDIO_CALLBACK_INVALID";
            return CallbackCount==0 ? "AUDIO_MISSING_PLAYBACK" : "AUDIO_UNDERRUN";
        }
    }
}
