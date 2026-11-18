using System;
using System.IO;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
namespace AcousticVocab.SessionIntegration.Tests
{
    // Decision I7 at session start, for every role that plays study sound
    // (Study A learners and both Study B roles). No catalogue ID names the hard
    // cap; REQ-AUD-03 and REQ-AUD-09 are the nearest.
    public sealed class LevelCapRoleTests
    {
        string directory;
        static readonly string Visit=new string('b',32),Receipt=new string('d',64);
        static readonly string[] Roles={"A-P01-L01","B-C01-M1","B-C01-M2"};
        [SetUp]public void Setup(){directory=Path.Combine(Path.GetTempPath(),"av-level-cap-role-"+Guid.NewGuid().ToString("N"));}
        [TearDown]public void Cleanup(){if(Directory.Exists(directory))Directory.Delete(directory,true);}
        static string Name(string codedId)=>PcmWave.Hash(Encoding.ASCII.GetBytes(codedId))+".local.jsonl";
        static JObject Station(double? offset)
        {
            var audio=new JObject{["route"]="route-01",["buffer_samples"]=1024,["route_offset_ms"]=42};
            if(offset.HasValue)audio["level_calibration"]=new JObject{["route"]="route-01",["ear_peak_dba_at_0dbfs"]=offset.Value,["measured_on"]="2026-12-15",["coupler_id"]="coupler-01"};
            return new JObject{["station_id"]="station-01",["audio"]=audio};
        }
        byte[] Held(string slot,float level)
        {
            var store=new ComfortableGainStore(directory);
            store.ChangeForComfort("SCR-"+slot,new string('a',32),level,1,true);store.CarryForward("SCR-"+slot,slot,Receipt,2);store.Hold(slot,Visit,3);
            return File.ReadAllBytes(Path.Combine(directory,Name(slot)));
        }
        float Start(JObject station,string slot,byte[] history)
        {
            var cap=JoinedVisitArtifacts.StationLevelCap(station);
            return JoinedVisitArtifacts.CappedGain(cap,JoinedVisitArtifacts.HeldGain(Name(slot),history,PcmWave.Hash(history),slot));
        }
        static string Code(TestDelegate action)=>Assert.Throws<SessionFault>(action).Code;

        [Test,Category("REQ_AUD_03")]
        public void EveryRoleRefusesToStartWithoutTheStationMeasurement()
        {
            foreach(string slot in Roles)
            {
                byte[] history=Held(slot,.05f);
                Assert.That(Code(()=>Start(Station(null),slot,history)),Is.EqualTo("LEVEL_CAP_OFFSET_MISSING"),slot);
                var malformed=Station(100);malformed["audio"]["level_calibration"]["coupler_id"]="";
                Assert.That(Code(()=>Start(malformed,slot,history)),Is.EqualTo("LEVEL_CAP_OFFSET_INVALID"),slot);
                Assert.That(Start(Station(100),slot,history),Is.EqualTo(.05f),slot);
            }
            Assert.That(Code(()=>JoinedVisitArtifacts.CappedGain(null,.05f)),Is.EqualTo("LEVEL_CAP_OFFSET_MISSING"));
        }

        [Test,Category("REQ_AUD_09"),Category("REQ_AUD_05")]
        public void BothStudyBRolesRefuseAHeldLevelAboveTheCap()
        {
            byte[] active=Held("B-C01-M1",.3f),yoked=Held("B-C01-M2",.2f);
            Assert.That(Code(()=>Start(Station(100),"B-C01-M1",active)),Is.EqualTo("GAIN_ABOVE_CAP"));
            Assert.That(Code(()=>Start(Station(100),"B-C01-M2",yoked)),Is.EqualTo("GAIN_ABOVE_CAP"));
            // Another station with a quieter route allows the same held levels unchanged.
            Assert.That(Start(Station(85),"B-C01-M1",active),Is.EqualTo(.3f));
            Assert.That(Start(Station(85),"B-C01-M2",yoked),Is.EqualTo(.2f));
        }

        [Test,Category("REQ_AUD_09")]
        public void HeldLevelExactlyAtTheCapStartsAndAHundredthOfADecibelAboveIsRefused()
        {
            var cap=LevelCap.FromStation(Station(104.5));
            float over=(float)Math.Pow(10,(cap.MaxGainDb+.01)/20);
            foreach(string slot in Roles)
            {
                Assert.That(Start(Station(104.5),slot,Held(slot,cap.MaxGain)),Is.EqualTo(cap.MaxGain),slot);
                Cleanup();
                byte[] history=Held(slot,over);
                Assert.That(Code(()=>Start(Station(104.5),slot,history)),Is.EqualTo("GAIN_ABOVE_CAP"),slot);
                Cleanup();
            }
        }
    }
}
