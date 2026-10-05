using System;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using AcousticVocab.Foundation;

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
        // Read station configuration plus a separately provisioned #80 report.
        // A station offset alone never supplies uncertainty or qualification.
        public static AudioRouteCalibration FromStationConfig(JObject station,byte[] calibrationBytes)
        {
            try
            {
                if(station==null || calibrationBytes==null || calibrationBytes.Length>16384) throw new AudioFault("AUDIO_CALIBRATION_INVALID");
                var report=PackageRules.Json(calibrationBytes);
                PackageRules.Keys(report,"schema_version","route","route_offset_ms","onset_uncertainty_ms","measurement_sha256");
                if(PackageRules.Integer(report["schema_version"])!=1) throw new AudioFault("AUDIO_CALIBRATION_INVALID");
                string route=PackageRules.String(station["audio"]?["route"]);
                if(PackageRules.String(report["route"])!=route ||
                    station["audio"]?["route_offset_ms"]?.Type is not (JTokenType.Integer or JTokenType.Float) ||
                    report["route_offset_ms"]?.Type is not (JTokenType.Integer or JTokenType.Float) ||
                    report["onset_uncertainty_ms"]?.Type is not (JTokenType.Integer or JTokenType.Float)) throw new AudioFault("AUDIO_CALIBRATION_INVALID");
                double offset=(double)report["route_offset_ms"];
                if((double)station["audio"]["route_offset_ms"]!=offset) throw new AudioFault("AUDIO_CALIBRATION_INVALID");
                return new AudioRouteCalibration(route,offset,(double)report["onset_uncertainty_ms"],PackageRules.String(report["measurement_sha256"]));
            }
            catch(Exception) { throw new AudioFault("AUDIO_CALIBRATION_INVALID"); }
        }
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
