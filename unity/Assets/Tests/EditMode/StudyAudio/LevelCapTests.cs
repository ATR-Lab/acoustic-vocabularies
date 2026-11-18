using System;
using System.IO;
using System.Text;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests.StudyAudio
{
    // Decision I7: each person's comfortable level stays below 80 dB(A) peak at
    // the ear. No catalogue ID names the hard cap; the nearest are REQ-AUD-09
    // (gain to level at the ear per station), REQ-AUD-03 (no study audio on an
    // uncalibrated or changed route) and REQ-AUD-05 (stored gain reapplied unchanged).
    public sealed class LevelCapTests
    {
        string directory;
        static readonly string Visit=new string('a',32),Receipt=new string('d',64);
        [SetUp] public void Setup() { directory=Path.Combine(Path.GetTempPath(),"av-level-cap-"+Guid.NewGuid().ToString("N")); }
        [TearDown] public void Cleanup() { if(Directory.Exists(directory)) Directory.Delete(directory,true); }

        internal static JObject Station(JToken offset,string route="route-01",string measuredRoute="route-01")
        {
            var audio=new JObject { ["route"]=route,["buffer_samples"]=1024,["route_offset_ms"]=null };
            if(offset!=null) audio["level_calibration"]=new JObject { ["route"]=measuredRoute,["ear_peak_dba_at_0dbfs"]=offset,["measured_on"]="2026-12-15",["coupler_id"]="coupler-01" };
            return new JObject { ["station_id"]="station-01",["audio"]=audio };
        }
        static LevelCap Cap(double offset) => LevelCap.FromStation(Station(offset));
        static string Code(TestDelegate action) => Assert.Throws<AudioFault>(action).Code;
        static float Above(float value) => BitConverter.Int32BitsToSingle(BitConverter.SingleToInt32Bits(value)+1);

        [Test,Category("REQ_AUD_09")]
        public void CapIsEightyDbaPeakAtTheEarMinusTheStationMeasurement()
        {
            Assert.That(LevelCap.CapDbaPeakAtEar,Is.EqualTo(80));
            Assert.That(LevelCap.LoudestStimulusPeakDbfs,Is.EqualTo(0),"Packages carry no peak; full scale is assumed");
            var cap=Cap(100);
            Assert.That(cap.MaxGainDb,Is.EqualTo(-20));Assert.That(cap.MaxGain,Is.EqualTo(.1f).Within(1e-6));
            Assert.That(cap.EarPeakDbaAt0Dbfs,Is.EqualTo(100));Assert.That(cap.MeasuredOn,Is.EqualTo("2026-12-15"));Assert.That(cap.CouplerId,Is.EqualTo("coupler-01"));
            Assert.That(Cap(75).MaxGain,Is.EqualTo(1f),"A quiet route is still limited to full scale");
        }

        [Test,Category("REQ_AUD_09")]
        public void LevelControlClampsAtTheCapAndCannotBeRaisedBeyondIt()
        {
            var cap=Cap(106);
            float level=.01f;
            for(int i=0;i<40;i++) { level=cap.Step(level,.05f);Assert.That(level,Is.LessThanOrEqualTo(cap.MaxGain)); }
            Assert.That(level,Is.EqualTo(cap.MaxGain));
            Assert.That(cap.Step(cap.MaxGain,.05f),Is.EqualTo(cap.MaxGain),"Louder at the cap stays at the cap");
            Assert.That(cap.Step(cap.MaxGain,-.05f),Is.LessThan(cap.MaxGain));
            Assert.That(cap.Step(.02f,-.05f),Is.EqualTo(LevelCap.MinimumStepGain));
            var low=Cap(130);
            Assert.That(low.Step(low.MaxGain,-.05f),Is.EqualTo(low.MaxGain),"The floor never lifts a step above a lower cap");
            Assert.That(low.Step(.5f,0),Is.EqualTo(low.MaxGain));
            Assert.That(Code(()=>cap.Step(float.NaN,.05f)),Is.EqualTo("AUDIO_GAIN_INVALID"));
        }

        [TestCase(90.0)] [TestCase(100.0)] [TestCase(107.3)] [TestCase(112.5)] [TestCase(126.04)]
        [Category("REQ_AUD_09")]
        public void ExactlyAtTheCapIsAllowedAndAHundredthOfADecibelAboveIsRefused(double offset)
        {
            var cap=Cap(offset);
            Assert.That(cap.Require(cap.MaxGain),Is.EqualTo(cap.MaxGain));Assert.That(cap.Allows(cap.MaxGain),Is.True);
            Assert.That(LevelCap.GainDb(cap.MaxGain),Is.LessThanOrEqualTo(cap.MaxGainDb).And.GreaterThan(cap.MaxGainDb-1e-5));
            float over=(float)Math.Pow(10,(cap.MaxGainDb+.01)/20);
            Assert.That(Code(()=>cap.Require(over)),Is.EqualTo("GAIN_ABOVE_CAP"));Assert.That(cap.Allows(over),Is.False);
            Assert.That(Code(()=>cap.Require(Above(cap.MaxGain))),Is.EqualTo("GAIN_ABOVE_CAP"),"Not even one float step above");
        }

        [Test,Category("REQ_AUD_03")]
        public void MissingOrMalformedStationMeasurementIsRefused()
        {
            Assert.That(Code(()=>LevelCap.FromStation(null)),Is.EqualTo("LEVEL_CAP_OFFSET_MISSING"));
            Assert.That(Code(()=>LevelCap.FromStation(Station(null))),Is.EqualTo("LEVEL_CAP_OFFSET_MISSING"));
            var station=Station(100);station["audio"]["level_calibration"]=null;
            Assert.That(Code(()=>LevelCap.FromStation(station)),Is.EqualTo("LEVEL_CAP_OFFSET_MISSING"));
            Assert.That(Code(()=>LevelCap.FromStation(Station(JValue.CreateNull()))),Is.EqualTo("LEVEL_CAP_OFFSET_MISSING"));
            station=Station(100);((JObject)station["audio"]["level_calibration"]).Remove("ear_peak_dba_at_0dbfs");
            Assert.That(Code(()=>LevelCap.FromStation(station)),Is.EqualTo("LEVEL_CAP_OFFSET_MISSING"));
            foreach(var malformed in new Action<JObject>[]{
                c=>c["ear_peak_dba_at_0dbfs"]="100",c=>c["ear_peak_dba_at_0dbfs"]=20,c=>c["ear_peak_dba_at_0dbfs"]=200,
                c=>c["measured_on"]="2026-02-30",c=>c["measured_on"]="15/12/2026",c=>c.Remove("measured_on"),
                c=>c["coupler_id"]="",c=>c.Remove("coupler_id"),c=>c["extra"]=1,c=>c.Remove("route")})
            {
                station=Station(100);malformed((JObject)station["audio"]["level_calibration"]);
                Assert.That(Code(()=>LevelCap.FromStation(station)),Is.EqualTo("LEVEL_CAP_OFFSET_INVALID"),station.ToString());
            }
            station=Station(100);station["audio"]["level_calibration"]=new JArray(100);
            Assert.That(Code(()=>LevelCap.FromStation(station)),Is.EqualTo("LEVEL_CAP_OFFSET_INVALID"));
            Assert.That(Code(()=>LevelCap.FromStation(Station(100,"route-02","route-01"))),Is.EqualTo("LEVEL_CAP_ROUTE_MISMATCH"),
                "A measurement taken on another audio route does not cap this one");
        }

        [Test,Category("REQ_AUD_05"),Category("REQ_AUD_09")]
        public void StoredCarriedOrHeldGainAboveTheCapIsRefusedAtLoad()
        {
            var store=new ComfortableGainStore(directory);var cap=Cap(100);
            store.ChangeForComfort("SCR-01",Visit,.3f,1,true);
            Assert.That(Code(()=>store.RestoreWithinCap("SCR-01",null,null,2,cap)),Is.EqualTo("GAIN_ABOVE_CAP"));
            Assert.That(Code(()=>store.RestoreWithinCap("A-P01-L01","SCR-01",Receipt,2,cap)),Is.EqualTo("GAIN_ABOVE_CAP"));
            Assert.That(store.HasHistory("A-P01-L01"),Is.False,"No carried row is written for a level above the cap");
            Assert.That(store.RestoreWithinCap("A-P01-L01","SCR-01",Receipt,2,Cap(85)),Is.EqualTo(.3f));
            store.Hold("A-P01-L01",Visit,3);
            Assert.That(Code(()=>store.RestoreWithinCap("A-P01-L01","SCR-01",Receipt,4,cap)),Is.EqualTo("GAIN_ABOVE_CAP"));
            byte[] bytes=File.ReadAllBytes(Path.Combine(directory,PcmWave.Hash(Encoding.ASCII.GetBytes("A-P01-L01"))+".local.jsonl"));
            float held=ComfortableGainStore.RestoreHeldVerified(bytes,PcmWave.Hash(bytes),"A-P01-L01");
            Assert.That(Code(()=>cap.Require(held)),Is.EqualTo("GAIN_ABOVE_CAP"));
            Assert.That(Code(()=>store.RestoreWithinCap("SCR-01",null,null,2,null)),Is.EqualTo("LEVEL_CAP_OFFSET_MISSING"));
        }

        [Test,Category("REQ_AUD_09")]
        public void FirstCalibrationStartsAtTheInitialGainOrTheCapWhicheverIsLower()
        {
            var store=new ComfortableGainStore(directory);
            Assert.That(store.RestoreWithinCap("SCR-01",null,null,1,Cap(90)),Is.EqualTo(ComfortableGainStore.InitialGain));
            var low=Cap(112);
            Assert.That(store.RestoreWithinCap("SCR-01",null,null,1,low),Is.EqualTo(low.MaxGain));
            Assert.That(store.HasHistory("SCR-01"),Is.False,"Starting below the initial gain records nothing");
        }
    }
}
