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
        // #80 decision: the record's offset is measured from scheduled_onset_mono_ms,
        // so the logged onset estimate is that scheduled start plus the offset.
        [TestCase(40)] [TestCase(-12.5)] [TestCase(0)]
        public void OnsetEstimateIsScheduledStartPlusRouteOffset(double offset)
        {
            var timing=Mapping().Schedule(10.003,11,Calibrated(offset));
            Assert.That(timing.OnsetEstimateMonoSeconds.Value-timing.ScheduledMonoSeconds,Is.EqualTo(offset/1000).Within(1e-12));
            Assert.That(timing.RouteOffsetMs,Is.EqualTo(offset));
        }
    }
}
