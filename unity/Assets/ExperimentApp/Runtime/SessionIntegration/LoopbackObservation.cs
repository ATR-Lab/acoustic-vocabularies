using System;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
namespace AcousticVocab.SessionIntegration
{
    // A station's loopback capture of the participant audio route, analysed for
    // one audio request. It is the only evidence that can confirm silence on the
    // route; software delivery outcomes cannot. Times are on the session's
    // monotonic clock in seconds. Levels are dBFS of the captured route
    // (negative infinity for digital zero).
    public sealed class LoopbackObservation
    {
        // Provisional silence thresholds: a capture is silent only when its peak
        // and its RMS are both strictly below these levels.
        public const double SilencePeakDbfs=-60,SilenceRmsDbfs=-70;
        public const string HashFormat="loopback-observation-v1";
        static readonly Regex Hash=new Regex(@"\A[0-9a-f]{64}\z"),Id=new Regex(@"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,79}\z");
        public string AudioRequestId{get;}public string CaptureDeviceId{get;}
        public double CaptureStartMonoSeconds{get;}public double CaptureEndMonoSeconds{get;}
        public double PeakDbfs{get;}public double RmsDbfs{get;}
        // SHA-256 of the captured samples, kept by the loopback source.
        public string CaptureSha256{get;}
        // SHA-256 of Canonical(): binds every field above.
        public string EvidenceSha256{get;}
        public bool Silent=>PeakDbfs<SilencePeakDbfs&&RmsDbfs<SilenceRmsDbfs;
        public LoopbackObservation(string audioRequestId,string captureDeviceId,double captureStartMonoSeconds,double captureEndMonoSeconds,double peakDbfs,double rmsDbfs,string captureSha256)
        {
            if(audioRequestId==null||!Id.IsMatch(audioRequestId))throw new ArgumentException("loopback request id",nameof(audioRequestId));
            if(captureDeviceId==null||!Id.IsMatch(captureDeviceId))throw new ArgumentException("loopback capture device id",nameof(captureDeviceId));
            if(!Finite(captureStartMonoSeconds)||!Finite(captureEndMonoSeconds)||captureEndMonoSeconds<=captureStartMonoSeconds)throw new ArgumentException("loopback capture window");
            if(double.IsNaN(peakDbfs)||double.IsNaN(rmsDbfs)||peakDbfs>0||rmsDbfs>0||rmsDbfs>peakDbfs)throw new ArgumentException("loopback levels");
            if(captureSha256==null||!Hash.IsMatch(captureSha256))throw new ArgumentException("loopback capture hash",nameof(captureSha256));
            AudioRequestId=audioRequestId;CaptureDeviceId=captureDeviceId;CaptureStartMonoSeconds=captureStartMonoSeconds;CaptureEndMonoSeconds=captureEndMonoSeconds;
            PeakDbfs=peakDbfs;RmsDbfs=rmsDbfs;CaptureSha256=captureSha256;EvidenceSha256=Sha256(Canonical());
        }
        // One field per line, numbers in invariant round-trip form.
        public string Canonical()=>string.Join("\n",HashFormat,AudioRequestId,CaptureDeviceId,R(CaptureStartMonoSeconds),R(CaptureEndMonoSeconds),R(PeakDbfs),R(RmsDbfs),CaptureSha256)+"\n";
        // True when the capture belongs to this request and spans from the
        // request hand-off to the missing-playback observation, which lies after
        // the end of the scheduled window.
        public bool Covers(string audioRequestId,double requestMonoSeconds,double observedMonoSeconds)=>
            string.Equals(AudioRequestId,audioRequestId,StringComparison.Ordinal)&&CaptureStartMonoSeconds<=requestMonoSeconds&&CaptureEndMonoSeconds>=observedMonoSeconds;
        internal static string Sha256(string text)
        {using var sha=SHA256.Create();var bytes=sha.ComputeHash(Encoding.UTF8.GetBytes(text));var s=new StringBuilder(64);foreach(byte b in bytes)s.Append(b.ToString("x2",CultureInfo.InvariantCulture));return s.ToString();}
        static bool Finite(double x)=>!double.IsNaN(x)&&!double.IsInfinity(x);
        static string R(double x)=>x.ToString("R",CultureInfo.InvariantCulture);
    }
    // The station's loopback capture analysis. Find returns the observation for
    // the request, or null when there is none. It is called on the host main
    // thread when missing playback is reported and must not block.
    public interface ILoopbackObservationSource
    {
        LoopbackObservation Find(string audioRequestId);
    }
}
