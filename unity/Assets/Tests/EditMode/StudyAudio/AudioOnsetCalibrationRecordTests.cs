using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace AcousticVocab.Tests.StudyAudio
{
    // Every record here is derived from the committed synthetic example. A
    // "qualified" variant is a test fixture only; no route has been measured.
    public sealed class AudioOnsetCalibrationRecordTests
    {
        const string Device="Fixture earphones on the 3.5 mm jack";
        static string ExamplePath => Path.Combine(PackageLoaderTests.Repository,"apparatus/examples/audio-onset-calibration.example.json");
        static JObject Example() => JObject.Parse(File.ReadAllText(ExamplePath));
        static byte[] Bytes(JObject record) => new UTF8Encoding(false).GetBytes(record.ToString());
        static JObject Qualified()
        {
            var r=Example(); r["status"]="qualified"; r["evidence_kind"]="physical_measurement"; r["full_scene_loaded"]=true;
            r["station_id"]="STATION-01"; r["route"]="route-01"; r["output_device"]=Device;
            r["review"]=new JObject { ["reviewed_by"]="Fixture reviewer",["reviewed_at"]="2027-04-07T10:00:00Z" };
            return r;
        }
        static JObject Station(byte[] record,JObject source)
        {
            return new JObject { ["station_id"]=(string)source["station_id"],["audio"]=new JObject {
                ["route"]=(string)source["route"],["buffer_samples"]=1024,["route_offset_ms"]=source["route_offset_ms"].DeepClone(),
                ["connection_mode"]="wired_3_5mm_earphones",["output_device"]=(string)source["output_device"],
                ["onset_calibration_record_sha256"]=PcmWave.Hash(record) } };
        }
        static AudioRuntimeRoute Runtime(JObject station,int rate=48000,int frames=1024,int count=4) =>
            new AudioRuntimeRoute((string)station["station_id"],(string)station["audio"]["route"],(string)station["audio"]["connection_mode"],
                (string)station["audio"]["output_device"],rate,frames,count);
        static AudioRouteCalibration Load(JObject record,Action<JObject> editStation=null,Func<JObject,AudioRuntimeRoute> runtime=null)
        {
            byte[] bytes=Bytes(record); var station=Station(bytes,record); editStation?.Invoke(station);
            return AudioRouteCalibration.FromStationConfig(station,bytes,(runtime??(s=>Runtime(s)))(station));
        }
        static DspClockMapping Mapping() { var m=new DspClockMapping(); m.Observe(10,20,10.002,.01); return m; }
        static string Code(TestDelegate action) => Assert.Throws<AudioFault>(action).Code;

        [Test] public void QualifiedMatchingRecordSuppliesItsOffsetAndUncertainty()
        {
            var record=Qualified(); byte[] bytes=Bytes(record);
            var route=Load(record);
            Assert.That(route.IsQualified,Is.True); Assert.That(route.NotQualifiedCode,Is.Null);
            Assert.That(route.OffsetMs,Is.EqualTo(37.348956)); Assert.That(route.UncertaintyMs,Is.EqualTo(4.343559));
            Assert.That(route.RecordSha256,Is.EqualTo(PcmWave.Hash(bytes))); Assert.That(route.EvidenceSha256,Is.EqualTo(route.RecordSha256));
            Assert.That(route.RecordStatus,Is.EqualTo("qualified"));
            Assert.That((route.BoundSampleRateHz,route.BoundBufferFrames,route.BoundBufferCount),Is.EqualTo(((int?)48000,(int?)1024,(int?)4)));
            var timing=Mapping().Schedule(10.003,11,route);
            // Estimate = scheduled_onset_mono_ms + measured offset; uncertainty adds the record's value to the DSP mapping bound.
            Assert.That(timing.OnsetEstimateMonoSeconds.Value-timing.ScheduledMonoSeconds,Is.EqualTo(.037348956).Within(1e-12));
            Assert.That(timing.OnsetUncertaintyMs,Is.EqualTo(11+4.343559).Within(1e-8));
            Assert.That(timing.RouteOffsetMs,Is.EqualTo(37.348956));
        }

        [Test] public void SignedResponseTimeQualificationCanQualifyAMissedTarget()
        {
            var record=Qualified(); record["target"]["met"]=false; record["onset_uncertainty_ms"]=25.5;
            record["response_time_qualification"]=new JObject { ["statement"]="Fixture: RT analyses carry +/-25.5 ms onset uncertainty.",
                ["signed_by"]="Fixture protocol owner",["signed_at"]="2027-04-07T11:00:00Z" };
            var route=Load(record);
            Assert.That(route.IsQualified,Is.True); Assert.That(route.UncertaintyMs,Is.EqualTo(25.5));
        }

        static void ExpectNotQualified(AudioRouteCalibration route,string code)
        {
            Assert.That(route.IsQualified,Is.False); Assert.That(route.CanScheduleSoftware,Is.False); Assert.That(route.NotQualifiedCode,Is.EqualTo(code));
            // Existing delivery-evidence rules: no trial scheduling, and the calibration-only path writes no onset.
            Assert.That(Code(()=>Mapping().Schedule(10.003,11,route)),Is.EqualTo("AUDIO_ROUTE_UNCALIBRATED"));
            var timing=Mapping().Schedule(10.003,11,route,calibrationOnly:true);
            Assert.That(timing.OnsetEstimateMonoSeconds,Is.Null); Assert.That(timing.OnsetUncertaintyMs,Is.Null); Assert.That(timing.RouteOffsetMs,Is.Null);
            Assert.That(timing.CalibrationOnly,Is.True);
        }

        [Test] public void CommittedSyntheticExampleIsNeverQualified()
        {
            var record=Example(); record["output_device"]=Device;
            Assert.That((string)record["status"],Is.EqualTo("provisional")); Assert.That((bool)record["target"]["met"],Is.True);
            LogAssert.Expect(LogType.Warning,new System.Text.RegularExpressions.Regex("^AUDIO_CALIBRATION_NOT_QUALIFIED status=provisional evidence=synthetic_fixture"));
            var route=Load(record);
            ExpectNotQualified(route,"AUDIO_CALIBRATION_NOT_QUALIFIED"); Assert.That(route.RecordStatus,Is.EqualTo("provisional"));
            // The unchanged file bytes behave the same.
            byte[] raw=File.ReadAllBytes(ExamplePath); var station=Station(raw,Example());
            LogAssert.Expect(LogType.Warning,new System.Text.RegularExpressions.Regex("^AUDIO_CALIBRATION_NOT_QUALIFIED"));
            ExpectNotQualified(AudioRouteCalibration.FromStationConfig(station,raw,Runtime(station)),"AUDIO_CALIBRATION_NOT_QUALIFIED");
        }

        [Test] public void ProvisionalPhysicalRecordIsNeverQualifiedEvenWhenReviewedAndMatching()
        {
            var record=Qualified(); record["status"]="provisional";
            LogAssert.Expect(LogType.Warning,new System.Text.RegularExpressions.Regex("^AUDIO_CALIBRATION_NOT_QUALIFIED status=provisional evidence=physical_measurement"));
            ExpectNotQualified(Load(record),"AUDIO_CALIBRATION_NOT_QUALIFIED");
        }

        [TestCase("evidence_kind","synthetic_fixture")]
        [TestCase("capture_kind","electrical_loopback")]
        [TestCase("play_mode","plain")]
        [TestCase("full_scene_loaded",false)]
        [TestCase("review",null)]
        public void QualifiedStatusWithoutItsEvidenceIsRefused(string key,object value)
        {
            var record=Qualified(); record[key]=value==null?JValue.CreateNull():JToken.FromObject(value);
            Assert.That(Code(()=>Load(record)),Is.EqualTo("AUDIO_CALIBRATION_RECORD_INVALID"));
        }

        [TestCase("station","AUDIO_CALIBRATION_STATION_MISMATCH")]
        [TestCase("route","AUDIO_CALIBRATION_ROUTE_MISMATCH")]
        [TestCase("connection","AUDIO_CALIBRATION_CONNECTION_MISMATCH")]
        [TestCase("connection_undeclared","AUDIO_CALIBRATION_CONNECTION_MISMATCH")]
        [TestCase("device","AUDIO_CALIBRATION_DEVICE_MISMATCH")]
        [TestCase("sample_rate","AUDIO_CALIBRATION_SAMPLE_RATE_MISMATCH")]
        [TestCase("buffer_frames","AUDIO_CALIBRATION_BUFFER_MISMATCH")]
        [TestCase("buffer_count","AUDIO_CALIBRATION_BUFFER_MISMATCH")]
        [TestCase("station_buffer","AUDIO_CALIBRATION_BUFFER_MISMATCH")]
        [TestCase("station_offset","AUDIO_CALIBRATION_OFFSET_MISMATCH")]
        [TestCase("station_offset_null","AUDIO_CALIBRATION_OFFSET_MISMATCH")]
        public void RuntimeMismatchInvalidatesWithLoggedFault(string change,string code)
        {
            var record=Qualified();
            Action<JObject> station=s=>{
                if(change=="station_buffer") s["audio"]["buffer_samples"]=512;
                if(change=="station_offset") s["audio"]["route_offset_ms"]=37.35;
                if(change=="station_offset_null") s["audio"]["route_offset_ms"]=null;
                if(change=="connection_undeclared") ((JObject)s["audio"]).Remove("connection_mode");
            };
            Func<JObject,AudioRuntimeRoute> runtime=s=>{
                var r=Runtime(s,change=="sample_rate"?44100:48000,change=="buffer_frames"?512:1024,change=="buffer_count"?2:4);
                return change switch {
                    "station" => new AudioRuntimeRoute("STATION-02",r.Route,r.ConnectionMode,r.OutputDevice,r.SampleRateHz,r.BufferFrames,r.BufferCount),
                    "route" => new AudioRuntimeRoute(r.StationId,"route-02",r.ConnectionMode,r.OutputDevice,r.SampleRateHz,r.BufferFrames,r.BufferCount),
                    "connection" => new AudioRuntimeRoute(r.StationId,r.Route,"headset_speakers",r.OutputDevice,r.SampleRateHz,r.BufferFrames,r.BufferCount),
                    "device" => new AudioRuntimeRoute(r.StationId,r.Route,r.ConnectionMode,"Other earphones",r.SampleRateHz,r.BufferFrames,r.BufferCount),
                    _ => r };
            };
            LogAssert.Expect(LogType.Error,new System.Text.RegularExpressions.Regex("^AUDIO_CALIBRATION_FAULT "+code+" record_sha256=[0-9a-f]{64}$"));
            var route=Load(record,station,runtime);
            ExpectNotQualified(route,code); Assert.That(route.RecordStatus,Is.EqualTo("qualified"));
        }

        [Test] public void StationConfigRouteMustMatchTheRecord()
        {
            var record=Qualified(); byte[] bytes=Bytes(record); var station=Station(bytes,record);
            station["audio"]["route"]="route-02";
            LogAssert.Expect(LogType.Error,new System.Text.RegularExpressions.Regex("^AUDIO_CALIBRATION_FAULT AUDIO_CALIBRATION_ROUTE_MISMATCH"));
            ExpectNotQualified(AudioRouteCalibration.FromStationConfig(station,bytes,Runtime(station)),"AUDIO_CALIBRATION_ROUTE_MISMATCH");
        }

        [Test] public void StationPinMustMatchTheRecordFileBytes()
        {
            var record=Qualified(); byte[] bytes=Bytes(record); var station=Station(bytes,record);
            station["audio"]["onset_calibration_record_sha256"]=new string('0',64);
            Assert.That(Code(()=>AudioRouteCalibration.FromStationConfig(station,bytes,Runtime(station))),Is.EqualTo("AUDIO_CALIBRATION_PIN_MISMATCH"));
            ((JObject)station["audio"]).Remove("onset_calibration_record_sha256");
            Assert.That(Code(()=>AudioRouteCalibration.FromStationConfig(station,bytes,Runtime(station))),Is.EqualTo("AUDIO_CALIBRATION_PIN_MISSING"));
            station=Station(bytes,record); byte[] changed=bytes.Concat(new[]{(byte)'\n'}).ToArray();
            Assert.That(Code(()=>AudioRouteCalibration.FromStationConfig(station,changed,Runtime(station))),Is.EqualTo("AUDIO_CALIBRATION_PIN_MISMATCH"));
            Assert.That(Code(()=>AudioRouteCalibration.FromStationConfig(station,null,Runtime(station))),Is.EqualTo("AUDIO_CALIBRATION_INVALID"));
            Assert.That(Code(()=>AudioRouteCalibration.FromStationConfig(station,bytes,null)),Is.EqualTo("AUDIO_CALIBRATION_INVALID"));
        }

        [Test] public void RetiredFiveKeyReportIsRefusedExplicitly()
        {
            var report=new JObject { ["schema_version"]=1,["route"]="route-01",["route_offset_ms"]=42,["onset_uncertainty_ms"]=2,["measurement_sha256"]=new string('a',64) };
            byte[] bytes=Bytes(report);
            var station=new JObject { ["station_id"]="STATION-01",["audio"]=new JObject { ["route"]="route-01",["buffer_samples"]=1024,["route_offset_ms"]=42,
                ["connection_mode"]="wired_3_5mm_earphones",["output_device"]=Device,["onset_calibration_record_sha256"]=PcmWave.Hash(bytes) } };
            Assert.That(Code(()=>AudioRouteCalibration.FromStationConfig(station,bytes,Runtime(station))),Is.EqualTo("AUDIO_CALIBRATION_REPORT_RETIRED"));
        }

        static void Set(JObject record,string path,JToken value)
        {
            var parts=path.Split('/'); JToken node=record;
            foreach(string part in parts.Take(parts.Length-1)) node=node[part];
            if(value==null) ((JObject)node).Remove(parts.Last()); else node[parts.Last()]=value;
        }
        static readonly object[] Refusals={
            new object[]{"reference_field",(JToken)"audio_request_mono_ms"},
            new object[]{"schema_version",(JToken)1.0},
            new object[]{"schema_version",(JToken)2},
            new object[]{"record_kind",(JToken)"other"},
            new object[]{"status",(JToken)"approved"},
            new object[]{"connection_mode",(JToken)"bluetooth"},
            new object[]{"app_sample_rate_hz",(JToken)44100},
            new object[]{"capture_sample_rate_hz",(JToken)32000},
            new object[]{"buffer_frames",(JToken)32},
            new object[]{"buffer_count",(JToken)17},
            new object[]{"buffer_frames",(JToken)1024.0},
            new object[]{"volume/step",(JToken)(-1)},
            new object[]{"full_scene_loaded",(JToken)"true"},
            new object[]{"measured_at",(JToken)"2026-10-07"},
            new object[]{"measured_at",(JToken)"2026-13-40T00:00:00Z"},
            new object[]{"station_id",(JToken)"bad id"},
            new object[]{"output_device",(JToken)"line\nbreak"},
            new object[]{"detector/version",(JToken)"onset-threshold/2"},
            new object[]{"detector/threshold_fs",(JToken)1.0},
            new object[]{"detector/reference_channel",(JToken)64},
            new object[]{"detector/quiet_pre_ms",(JToken)0},
            new object[]{"detector/extra",(JToken)1},
            new object[]{"sync/slope",(JToken)1.5},
            new object[]{"sync/fit_n",(JToken)2},
            new object[]{"sync/bound_ms",(JToken)(-0.1)},
            new object[]{"plays/coverage/P3",null},
            new object[]{"plays/matched",(JToken)(-1)},
            new object[]{"route_offset_ms",(JToken)"37.3"},
            new object[]{"onset_uncertainty_ms",(JToken)(-1)},
            new object[]{"onset_uncertainty_ms",(JToken)20.5},
            new object[]{"plays/matched",(JToken)150},
            new object[]{"route_offset_ms",JValue.CreateNull()},
            new object[]{"target/p95_limit_ms",(JToken)25},
            new object[]{"loopback_reference/channel",(JToken)"1"},
            new object[]{"inputs/capture_wav_sha256",(JToken)new string('0',63)},
            new object[]{"outputs/plays_csv_sha256",(JToken)new string('A',64)},
            new object[]{"review",(JToken)new JObject{["reviewed_by"]="x"}},
            new object[]{"response_time_qualification",(JToken)new JObject{["statement"]="",["signed_by"]="x",["signed_at"]="2027-04-07T11:00:00Z"}},
            new object[]{"unexpected",(JToken)1},
            new object[]{"review",null},
        };
        [TestCaseSource(nameof(Refusals))]
        public void RecordParsingIsSchemaStrict(string path,JToken value)
        {
            var record=Example(); Set(record,path,value);
            Assert.That(Code(()=>AudioOnsetCalibrationRecord.Parse(Bytes(record))),Is.EqualTo("AUDIO_CALIBRATION_RECORD_INVALID"),path);
        }

        [TestCase("{\"schema_version\":1,\"schema_version\":1}")]
        [TestCase("{/*c*/}")]
        [TestCase("{\"a\":NaN}")]
        [TestCase("[]")]
        [TestCase("")]
        public void NonStrictJsonIsRefused(string json)
        { Assert.That(Code(()=>AudioOnsetCalibrationRecord.Parse(Encoding.UTF8.GetBytes(json))),Is.EqualTo("AUDIO_CALIBRATION_RECORD_INVALID")); }

        [Test] public void CommittedExampleParsesWithItsFileHash()
        {
            byte[] raw=File.ReadAllBytes(ExamplePath); var record=AudioOnsetCalibrationRecord.Parse(raw);
            Assert.That(record.Sha256,Is.EqualTo(PcmWave.Hash(raw))); Assert.That(record.Status,Is.EqualTo("provisional"));
            Assert.That(record.EvidenceKind,Is.EqualTo("synthetic_fixture")); Assert.That(record.Qualified,Is.False);
            Assert.That(record.RouteOffsetMs,Is.EqualTo(37.348956)); Assert.That(record.OnsetUncertaintyMs,Is.EqualTo(4.343559));
        }

        [Test] public void ParserKeySetsMatchTheCanonicalSchema()
        {
            var schema=JObject.Parse(File.ReadAllText(Path.Combine(PackageLoaderTests.Repository,"apparatus/schemas/audio-onset-calibration.schema.json")));
            string[] Required(JToken node)=>((JArray)node["required"]).Select(x=>(string)x).OrderBy(x=>x,StringComparer.Ordinal).ToArray();
            string[] Sorted(System.Collections.Generic.IEnumerable<string> keys)=>keys.OrderBy(x=>x,StringComparer.Ordinal).ToArray();
            Assert.That(Sorted(AudioOnsetCalibrationRecord.RecordKeys),Is.EqualTo(Required(schema)));
            Assert.That(Sorted(AudioOnsetCalibrationRecord.DetectorKeys),Is.EqualTo(Required(schema["properties"]["detector"])));
            Assert.That(Sorted(AudioOnsetCalibrationRecord.SyncKeys),Is.EqualTo(Required(schema["properties"]["sync"])));
            Assert.That(Sorted(AudioOnsetCalibrationRecord.PlayKeys),Is.EqualTo(Required(schema["properties"]["plays"])));
            Assert.That(Sorted(AudioOnsetCalibrationRecord.ConnectionModes),Is.EqualTo(schema["properties"]["connection_mode"]["enum"].Select(x=>(string)x).OrderBy(x=>x,StringComparer.Ordinal).ToArray()));
            Assert.That((string)schema["properties"]["reference_field"]["const"],Is.EqualTo(AudioOnsetCalibrationRecord.ReferenceField));
            var station=JObject.Parse(File.ReadAllText(Path.Combine(PackageLoaderTests.Repository,"apparatus/schemas/station.schema.json")));
            Assert.That(Sorted(AudioOnsetCalibrationRecord.ConnectionModes),Is.EqualTo(station["properties"]["audio"]["properties"]["connection_mode"]["enum"].Select(x=>(string)x).OrderBy(x=>x,StringComparer.Ordinal).ToArray()));
        }
    }
}
