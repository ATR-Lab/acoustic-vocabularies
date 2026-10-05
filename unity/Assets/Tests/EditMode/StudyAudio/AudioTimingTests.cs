using System;
using AcousticVocab.StudyAudio;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using System.Text;

namespace AcousticVocab.Tests.StudyAudio
{
    public sealed class AudioTimingTests
    {
        static AudioRouteCalibration Calibrated(double offset=40) => new AudioRouteCalibration("route-01",offset,3,new string('a',64));
        static DspClockMapping Mapping()
        { var mapping=new DspClockMapping();mapping.Observe(10,20,10.002,.01);return mapping; }
        [Test] public void RouteOffsetSchedulesEarlyAndUncertaintyIncludesDspQuantum()
        {
            var timing=Mapping().Schedule(10.003,11,Calibrated());
            Assert.That(timing.ScheduledMonoSeconds,Is.EqualTo(10.96).Within(1e-10));
            Assert.That(timing.ScheduledDspSeconds,Is.EqualTo(20.959).Within(1e-10));
            Assert.That(timing.OnsetEstimateMonoSeconds,Is.EqualTo(11));
            Assert.That(timing.OnsetUncertaintyMs,Is.EqualTo(14).Within(1e-8));
        }
        [Test] public void UnmeasuredRouteCannotScheduleStudyOrInventOnset()
        {
            var mapping=Mapping();var route=AudioRouteCalibration.Unmeasured("SIMULATOR_UNMEASURED");
            Assert.That(Assert.Throws<AudioFault>(()=>mapping.Schedule(10.003,11,route)).Code,Is.EqualTo("AUDIO_ROUTE_UNCALIBRATED"));
            var timing=mapping.Schedule(10.003,11,route,calibrationOnly:true);
            Assert.That(timing.CalibrationOnly,Is.True);Assert.That(timing.OnsetEstimateMonoSeconds,Is.Null);
            Assert.That(timing.OnsetUncertaintyMs,Is.Null);Assert.That(timing.RouteOffsetMs,Is.Null);
        }
        [TestCase(10.001)] [TestCase(10.253)] [TestCase(double.NaN)]
        public void StaleOrBackwardRequestCannotUseMapping(double request)
        { Assert.Throws<AudioFault>(()=>Mapping().Schedule(request,11,Calibrated())); }
        [Test] public void DeviceResetInvalidatesPriorMapping()
        { var mapping=Mapping();mapping.Reset();Assert.Throws<AudioFault>(()=>mapping.Schedule(10.003,11,Calibrated())); }
        [TestCase(9,21,9.002)] [TestCase(11,19,11.002)] [TestCase(11,21,11.006)]
        public void InvalidClockObservationRevokesExistingMapping(double before,double dsp,double after)
        {
            var mapping=Mapping();Assert.Throws<AudioFault>(()=>mapping.Observe(before,dsp,after,.01));
            Assert.Throws<AudioFault>(()=>mapping.Schedule(11.01,12,Calibrated()));
        }
        [TestCase(10.14)] [TestCase(41)] [TestCase(double.PositiveInfinity)]
        public void LeadTimeAndHorizonAreEnforced(double onset)
        { Assert.Throws<AudioFault>(()=>Mapping().Schedule(10.003,onset,Calibrated())); }
        [TestCase("route\nprivate")] [TestCase("")] [TestCase("route with spaces")]
        public void CalibrationIdentifiersAreBounded(string route)
        { Assert.Throws<AudioFault>(()=>new AudioRouteCalibration(route,0,1,new string('b',64))); }
        [Test] public void StationOffsetNeedsMatchingMeasuredRouteAndUncertainty()
        {
            var station=new JObject { ["audio"]=new JObject { ["route"]="route-01",["route_offset_ms"]=42 } };
            var report=new JObject { ["schema_version"]=1,["route"]="route-01",["route_offset_ms"]=42,["onset_uncertainty_ms"]=2,["measurement_sha256"]=new string('a',64) };
            byte[] Bytes()=>Encoding.UTF8.GetBytes(report.ToString());
            Assert.That(AudioRouteCalibration.FromStationConfig(station,Bytes()).OffsetMs,Is.EqualTo(42));
            report["route"]="route-02";Assert.Throws<AudioFault>(()=>AudioRouteCalibration.FromStationConfig(station,Bytes()));
            report["route"]="route-01";station["audio"]["route_offset_ms"]=null;
            Assert.Throws<AudioFault>(()=>AudioRouteCalibration.FromStationConfig(station,Bytes()));
            station["audio"]["route_offset_ms"]=42;report.Remove("onset_uncertainty_ms");
            Assert.Throws<AudioFault>(()=>AudioRouteCalibration.FromStationConfig(station,Bytes()));
        }
    }
}
