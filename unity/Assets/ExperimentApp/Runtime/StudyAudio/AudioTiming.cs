using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using AcousticVocab.Foundation;
using Debug = UnityEngine.Debug;

namespace AcousticVocab.StudyAudio
{
    public sealed class AudioRouteCalibration
    {
        public string Route { get; }
        public string EvidenceSha256 { get; }
        public double OffsetMs { get; }
        public double UncertaintyMs { get; }
        public bool IsQualified { get; }
        public bool SimulationOnly { get; private set; }
        public bool CanScheduleSoftware => IsQualified || SimulationOnly;
        // Set only by FromStationConfig. A record that loaded but cannot qualify
        // this route leaves the route unmeasured and names the reason here.
        public string NotQualifiedCode { get; private set; }
        public string RecordSha256 { get; private set; }
        public string RecordStatus { get; private set; }
        // The device format the qualified record was measured with. AudioPlayer
        // refuses to configure a qualified route on any other format.
        public int? BoundSampleRateHz { get; private set; }
        public int? BoundBufferFrames { get; private set; }
        public int? BoundBufferCount { get; private set; }
        public static AudioRouteCalibration ForSimulation(SimulationTestAuthority authority)
        { if(authority==null || !SimulationTestAuthority.CompiledCapability)throw new AudioFault("AUDIO_SIMULATION_AUTHORITY");return new AudioRouteCalibration("simulation-software-output"){SimulationOnly=true}; }
        public AudioRouteCalibration(string route,double offsetMs,double uncertaintyMs,string evidenceSha256)
        {
            if(route==null || !Regex.IsMatch(route,"\\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\\z") || !Finite(offsetMs) || Math.Abs(offsetMs)>1000 ||
                !Finite(uncertaintyMs) || uncertaintyMs<0 || uncertaintyMs>1000 ||
                evidenceSha256==null || !Regex.IsMatch(evidenceSha256,"\\A[0-9a-f]{64}\\z")) throw new AudioFault("AUDIO_CALIBRATION_INVALID");
            Route=route; OffsetMs=offsetMs; UncertaintyMs=uncertaintyMs; EvidenceSha256=evidenceSha256;IsQualified=true;
        }
        AudioRouteCalibration(string route)
        {
            if(route==null || !Regex.IsMatch(route,"\\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\\z")) throw new AudioFault("AUDIO_CALIBRATION_INVALID");
            Route=route;IsQualified=false;
        }
        public static AudioRouteCalibration Unmeasured(string route) => new AudioRouteCalibration(route);
        static readonly string[] RetiredReportKeys={"schema_version","route","route_offset_ms","onset_uncertainty_ms","measurement_sha256"};

        // Load the full #80 record pinned by station configuration
        // (audio.onset_calibration_record_sha256) and bind it to the running
        // audio setup. Integrity failures throw: a missing or wrong pin, a record
        // that is not schema-valid, or the retired five-key report. A valid record
        // that cannot qualify this route returns an unmeasured route, so onset
        // estimates stay null and trial scheduling stays refused:
        //  - provisional or synthetic records (warning AUDIO_CALIBRATION_NOT_QUALIFIED);
        //  - station, route, connection mode, output device, sample rate, DSP
        //    buffer or station offset differing from the record (logged fault).
        public static AudioRouteCalibration FromStationConfig(JObject station,byte[] recordBytes,AudioRuntimeRoute runtime)
        {
            if(station==null || runtime==null || recordBytes==null || recordBytes.Length==0 || recordBytes.Length>65536) throw new AudioFault("AUDIO_CALIBRATION_INVALID");
            string route,stationId,pin; JObject audio;
            try
            {
                audio=(JObject)station["audio"]; route=PackageRules.String(audio["route"]); stationId=PackageRules.String(station["station_id"]);
                pin=audio["onset_calibration_record_sha256"]?.Type==JTokenType.String?(string)audio["onset_calibration_record_sha256"]:null;
            }
            catch(Exception) { throw new AudioFault("AUDIO_CALIBRATION_INVALID"); }
            if(!PackageRules.IsHash(pin)) throw new AudioFault("AUDIO_CALIBRATION_PIN_MISSING");
            string sha=PcmWave.Hash(recordBytes);
            if(sha!=pin) throw new AudioFault("AUDIO_CALIBRATION_PIN_MISMATCH");
            JObject document;
            try { document=PackageRules.Json(recordBytes); }
            catch(Exception) { throw new AudioFault("AUDIO_CALIBRATION_RECORD_INVALID"); }
            if(new HashSet<string>(document.Properties().Select(p=>p.Name),StringComparer.Ordinal).SetEquals(RetiredReportKeys))
                throw new AudioFault("AUDIO_CALIBRATION_REPORT_RETIRED");
            var record=AudioOnsetCalibrationRecord.Parse(recordBytes);

            if(!record.Qualified)
            {
                Debug.LogWarning("AUDIO_CALIBRATION_NOT_QUALIFIED status="+record.Status+" evidence="+record.EvidenceKind+" record_sha256="+sha);
                return NotQualified(route,"AUDIO_CALIBRATION_NOT_QUALIFIED",record);
            }
            string mismatch=
                record.StationId!=stationId || record.StationId!=runtime.StationId ? "AUDIO_CALIBRATION_STATION_MISMATCH" :
                record.Route!=route || record.Route!=runtime.Route ? "AUDIO_CALIBRATION_ROUTE_MISMATCH" :
                record.ConnectionMode!=runtime.ConnectionMode ? "AUDIO_CALIBRATION_CONNECTION_MISMATCH" :
                record.OutputDevice!=runtime.OutputDevice ? "AUDIO_CALIBRATION_DEVICE_MISMATCH" :
                record.AppSampleRateHz!=runtime.SampleRateHz ? "AUDIO_CALIBRATION_SAMPLE_RATE_MISMATCH" :
                record.BufferFrames!=runtime.BufferFrames || record.BufferCount!=runtime.BufferCount ||
                    audio["buffer_samples"]?.Type!=JTokenType.Integer || (long)audio["buffer_samples"]!=record.BufferFrames ? "AUDIO_CALIBRATION_BUFFER_MISMATCH" :
                audio["route_offset_ms"]?.Type is not (JTokenType.Integer or JTokenType.Float) || (double)audio["route_offset_ms"]!=record.RouteOffsetMs.Value ? "AUDIO_CALIBRATION_OFFSET_MISMATCH" :
                null;
            if(mismatch!=null)
            {
                Debug.LogError("AUDIO_CALIBRATION_FAULT "+mismatch+" record_sha256="+sha);
                return NotQualified(route,mismatch,record);
            }
            return new AudioRouteCalibration(route,record.RouteOffsetMs.Value,record.OnsetUncertaintyMs.Value,sha)
            { RecordSha256=sha,RecordStatus=record.Status,BoundSampleRateHz=record.AppSampleRateHz,BoundBufferFrames=record.BufferFrames,BoundBufferCount=record.BufferCount };
        }
        static AudioRouteCalibration NotQualified(string route,string code,AudioOnsetCalibrationRecord record) =>
            new AudioRouteCalibration(route) { NotQualifiedCode=code,RecordSha256=record.Sha256,RecordStatus=record.Status };
        internal static bool Finite(double n) => !double.IsNaN(n) && !double.IsInfinity(n);
    }

    // A bounded sampled mapping, not an acoustic-onset measurement. The route
    // offset/uncertainty still requires the independent #80 calibration.
    public sealed class DspClockMapping
    {
        double dsp, mono, uncertainty, observed;
        bool valid;
        public void Reset() { valid=false; }
        public void Observe(double beforeMonoSeconds,double dspSeconds,double afterMonoSeconds,double quantumSeconds)
        {
            bool finite=AudioRouteCalibration.Finite(beforeMonoSeconds) && AudioRouteCalibration.Finite(afterMonoSeconds) &&
                AudioRouteCalibration.Finite(dspSeconds) && AudioRouteCalibration.Finite(quantumSeconds);
            if(!finite || beforeMonoSeconds<0 || dspSeconds<0 || afterMonoSeconds<beforeMonoSeconds ||
                afterMonoSeconds-beforeMonoSeconds>.005 || quantumSeconds<=0 || quantumSeconds>.2)
            { valid=false; throw new AudioFault("AUDIO_CLOCK_INVALID"); }
            if(valid && (beforeMonoSeconds<observed || dspSeconds<dsp))
            { valid=false; throw new AudioFault("AUDIO_CLOCK_REGRESSED"); }
            // Include a whole DSP quantum because the sampled DSP clock can be
            // stepped at buffer boundaries; bracket width alone is insufficient.
            uncertainty=(afterMonoSeconds-beforeMonoSeconds)/2+quantumSeconds;
            mono=(beforeMonoSeconds+afterMonoSeconds)/2; dsp=dspSeconds;
            observed=afterMonoSeconds; valid=true;
        }
        public AudioScheduleTiming Schedule(double requestMonoSeconds,double requestedOnsetMonoSeconds,
            AudioRouteCalibration calibration,double minimumLeadSeconds=.1,bool calibrationOnly=false)
        {
            if(!valid || !AudioRouteCalibration.Finite(requestMonoSeconds) || requestMonoSeconds<observed ||
                requestMonoSeconds-observed>.25 || !AudioRouteCalibration.Finite(requestedOnsetMonoSeconds) ||
                !AudioRouteCalibration.Finite(minimumLeadSeconds) || minimumLeadSeconds<.02)
                throw new AudioFault("AUDIO_CLOCK_UNAVAILABLE");
            if(calibration==null || !calibrationOnly && !calibration.CanScheduleSoftware) throw new AudioFault("AUDIO_ROUTE_UNCALIBRATED");
            double scheduledMono=requestedOnsetMonoSeconds-(calibrationOnly?0:calibration.OffsetMs/1000);
            if(scheduledMono-requestMonoSeconds<minimumLeadSeconds+uncertainty || scheduledMono-requestMonoSeconds>30)
                throw new AudioFault("AUDIO_SCHEDULE_LATE");
            return new AudioScheduleTiming(requestMonoSeconds,scheduledMono,dsp+scheduledMono-mono,
                calibrationOnly||calibration.SimulationOnly?(double?)null:requestedOnsetMonoSeconds,
                calibrationOnly||calibration.SimulationOnly?(double?)null:(uncertainty*1000)+calibration.UncertaintyMs,
                calibrationOnly||calibration.SimulationOnly?(double?)null:calibration.OffsetMs,
                calibration.SimulationOnly&&!calibrationOnly?requestedOnsetMonoSeconds:(double?)null,
                calibration.SimulationOnly&&!calibrationOnly?uncertainty*1000:(double?)null);
        }
    }

    public sealed class AudioScheduleTiming
    {
        public double RequestMonoSeconds { get; }
        public double ScheduledMonoSeconds { get; }
        public double ScheduledDspSeconds { get; }
        public double? OnsetEstimateMonoSeconds { get; }
        public double? OnsetUncertaintyMs { get; }
        public double? RouteOffsetMs { get; }
        public double? SoftwareOutputEstimateMonoSeconds { get; }
        public double? SoftwareOutputUncertaintyMs { get; }
        public bool SimulationOnly => SoftwareOutputEstimateMonoSeconds.HasValue;
        public bool CalibrationOnly => !OnsetEstimateMonoSeconds.HasValue&&!SimulationOnly;
        public double? PresentationAnchorMonoSeconds=>OnsetEstimateMonoSeconds??SoftwareOutputEstimateMonoSeconds;
        public double? PresentationUncertaintyMs=>OnsetUncertaintyMs??SoftwareOutputUncertaintyMs;
        internal AudioScheduleTiming(double request,double mono,double dsp,double? onset,double? uncertainty,double? offset)
            :this(request,mono,dsp,onset,uncertainty,offset,null,null){}
        internal AudioScheduleTiming(double request,double mono,double dsp,double? onset,double? uncertainty,double? offset,double? software,double? softwareUncertainty)
        { RequestMonoSeconds=request;ScheduledMonoSeconds=mono;ScheduledDspSeconds=dsp;OnsetEstimateMonoSeconds=onset;OnsetUncertaintyMs=uncertainty;RouteOffsetMs=offset;SoftwareOutputEstimateMonoSeconds=software;SoftwareOutputUncertaintyMs=softwareUncertainty; }
    }
}
