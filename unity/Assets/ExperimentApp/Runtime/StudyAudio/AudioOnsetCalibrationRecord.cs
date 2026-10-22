using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.StudyAudio
{
    // Strict reader for the #80 record written by tools/onset_calibration.py and
    // described by apparatus/schemas/audio-onset-calibration.schema.json. It
    // accepts nothing that schema refuses. It is stricter in two places:
    // integer fields must be JSON integers (1.0 is refused), and text length is
    // counted in UTF-16 code units. Any refusal is AUDIO_CALIBRATION_RECORD_INVALID.
    public sealed class AudioOnsetCalibrationRecord
    {
        public const string ReferenceField="scheduled_onset_mono_ms";
        public static readonly IReadOnlyList<string> RecordKeys=Array.AsReadOnly(new[]{"schema_version","record_kind","status","evidence_kind","station_id","route",
            "connection_mode","play_mode","app_build_id","output_device","capture_interface","capture_kind","app_sample_rate_hz","capture_sample_rate_hz",
            "buffer_frames","buffer_count","volume","full_scene_loaded","reference_field","measured_at","detector","sync","plays","route_offset_ms",
            "offset_sd_ms","residual_abs_p95_ms","residual_abs_max_ms","onset_uncertainty_ms","target","loopback_reference","inputs","outputs",
            "response_time_qualification","review"});
        public static readonly IReadOnlyList<string> DetectorKeys=Array.AsReadOnly(new[]{"version","tool","measure_channel","reference_channel","threshold_fs",
            "consecutive_samples","quiet_pre_ms","search_start_ms","search_end_ms","threshold_source","quantile_method"});
        public static readonly IReadOnlyList<string> SyncKeys=Array.AsReadOnly(new[]{"model","fit_n","check_n","slope","intercept_s","heldout_p95_ms",
            "heldout_max_ms","external_bound_ms","bound_ms","evidence"});
        public static readonly IReadOnlyList<string> PlayKeys=Array.AsReadOnly(new[]{"requested","matched","missing","clipped","contaminated","outside_capture","coverage"});
        public static readonly IReadOnlyList<string> ConnectionModes=Array.AsReadOnly(new[]{"headset_speakers","wired_3_5mm_earphones","link_pc_headphones"});

        public string Sha256 { get; private set; }
        public string Status { get; private set; }
        public string EvidenceKind { get; private set; }
        public string StationId { get; private set; }
        public string Route { get; private set; }
        public string ConnectionMode { get; private set; }
        public string PlayMode { get; private set; }
        public string OutputDevice { get; private set; }
        public string CaptureKind { get; private set; }
        public int AppSampleRateHz { get; private set; }
        public int BufferFrames { get; private set; }
        public int BufferCount { get; private set; }
        public bool FullSceneLoaded { get; private set; }
        public double? RouteOffsetMs { get; private set; }
        public double? OnsetUncertaintyMs { get; private set; }
        public bool TargetMet { get; private set; }
        public bool Reviewed { get; private set; }
        public bool ResponseTimeQualified { get; private set; }
        // Only a reviewed physical acoustic full-scene scheduled record is qualified.
        // The schema already enforces this; it is repeated so that no other path can
        // turn a provisional or synthetic record into a calibrated route.
        public bool Qualified => Status=="qualified" && EvidenceKind=="physical_measurement" && CaptureKind=="acoustic_coupler" &&
            FullSceneLoaded && PlayMode=="scheduled" && Reviewed && RouteOffsetMs.HasValue && OnsetUncertaintyMs.HasValue && (TargetMet || ResponseTimeQualified);

        AudioOnsetCalibrationRecord() { }

        public static AudioOnsetCalibrationRecord Parse(byte[] bytes)
        {
            try
            {
                Need(bytes!=null && bytes.Length>0 && bytes.Length<=65536);
                return Parse(PackageRules.Json(bytes),PcmWave.Hash(bytes));
            }
            catch(Exception) { throw new AudioFault("AUDIO_CALIBRATION_RECORD_INVALID"); }
        }

        internal static AudioOnsetCalibrationRecord Parse(JObject v,string sha256)
        {
            Keys(v,RecordKeys);
            Need(Integer(v["schema_version"],1,1)==1);
            Need(Text(v["record_kind"])=="audio_onset_calibration");
            var r=new AudioOnsetCalibrationRecord { Sha256=sha256 };
            r.Status=OneOf(v["status"],"provisional","qualified");
            r.EvidenceKind=OneOf(v["evidence_kind"],"physical_measurement","synthetic_fixture");
            r.StationId=Id(v["station_id"]); r.Route=Id(v["route"]);
            r.ConnectionMode=OneOf(v["connection_mode"],ConnectionModes.ToArray());
            r.PlayMode=OneOf(v["play_mode"],"plain","scheduled");
            Text(v["app_build_id"],512); r.OutputDevice=Text(v["output_device"],512); Text(v["capture_interface"],512);
            r.CaptureKind=OneOf(v["capture_kind"],"acoustic_coupler","electrical_loopback");
            r.AppSampleRateHz=(int)Integer(v["app_sample_rate_hz"],48000,48000);
            Need(new long[]{44100,48000,88200,96000,192000}.Contains(Integer(v["capture_sample_rate_hz"],0,192000)));
            r.BufferFrames=(int)Integer(v["buffer_frames"],64,8192); r.BufferCount=(int)Integer(v["buffer_count"],1,16);
            Keys(v["volume"],"step","max_step"); Integer(v["volume"]["step"],0,10000); Integer(v["volume"]["max_step"],1,10000);
            r.FullSceneLoaded=Bool(v["full_scene_loaded"]);
            Need(Text(v["reference_field"])==ReferenceField);
            Timestamp(v["measured_at"]);

            var d=v["detector"]; Keys(d,DetectorKeys);
            Need(Text(d["version"])=="onset-threshold/1" && Text(d["tool"])=="tools/onset_calibration.py" && Text(d["quantile_method"])=="linear_interpolation_hyndman_fan_7");
            Integer(d["measure_channel"],0,63); if(d["reference_channel"].Type!=JTokenType.Null) Integer(d["reference_channel"],0,63);
            double threshold=Number(d["threshold_fs"]); Need(threshold>0 && threshold<1);
            Integer(d["consecutive_samples"],1,64);
            double quiet=Number(d["quiet_pre_ms"]); Need(quiet>0 && quiet<=2000);
            foreach(string key in new[]{"search_start_ms","search_end_ms"}) { double x=Number(d[key]); Need(x>=-1000 && x<=5000); }
            Text(d["threshold_source"],512);

            var s=v["sync"]; Keys(s,SyncKeys);
            Need(Text(s["model"])=="affine_capture_s_from_host_ms/1");
            Integer(s["fit_n"],3,long.MaxValue); Integer(s["check_n"],3,long.MaxValue);
            double slope=Number(s["slope"]); Need(slope>=.99 && slope<=1.01); Number(s["intercept_s"]);
            foreach(string key in new[]{"heldout_p95_ms","heldout_max_ms","external_bound_ms","bound_ms"}) NonNegative(s[key]);
            Text(s["evidence"],512);

            var p=v["plays"]; Keys(p,PlayKeys);
            Integer(p["requested"],1,long.MaxValue);
            long matched=Integer(p["matched"],0,long.MaxValue);
            foreach(string key in new[]{"missing","clipped","contaminated","outside_capture"}) Integer(p[key],0,long.MaxValue);
            Keys(p["coverage"],"P1","P2","P3");
            foreach(string profile in new[]{"P1","P2","P3"}) { Keys(p["coverage"][profile],"atom","message"); Integer(p["coverage"][profile]["atom"],0,long.MaxValue); Integer(p["coverage"][profile]["message"],0,long.MaxValue); }

            r.RouteOffsetMs=NullableNumber(v["route_offset_ms"]);
            foreach(string key in new[]{"offset_sd_ms","residual_abs_p95_ms","residual_abs_max_ms"}) NullableNonNegative(v[key]);
            r.OnsetUncertaintyMs=NullableNonNegative(v["onset_uncertainty_ms"]);

            var t=v["target"]; Keys(t,"rule","p95_limit_ms","min_plays","met");
            Need(Text(t["rule"])=="onset-target/1" && Integer(t["p95_limit_ms"],20,20)==20 && Integer(t["min_plays"],200,200)==200);
            r.TargetMet=Bool(t["met"]);

            var loop=v["loopback_reference"];
            if(loop.Type!=JTokenType.Null)
            {
                Keys(loop,"channel","matched","median_delta_ms","delta_abs_deviation_p95_ms");
                Integer(loop["channel"],0,63); Integer(loop["matched"],0,long.MaxValue);
                NullableNumber(loop["median_delta_ms"]); NullableNonNegative(loop["delta_abs_deviation_p95_ms"]);
            }
            Keys(v["inputs"],"capture_wav_sha256","events_csv_sha256","sync_csv_sha256","settings_json_sha256");
            foreach(var property in ((JObject)v["inputs"]).Properties()) Hash(property.Value);
            Keys(v["outputs"],"plays_csv_sha256","histogram_csv_sha256");
            foreach(var property in ((JObject)v["outputs"]).Properties()) Hash(property.Value);

            var rtq=v["response_time_qualification"];
            if(rtq.Type!=JTokenType.Null)
            {
                Keys(rtq,"statement","signed_by","signed_at");
                Need(rtq["statement"].Type==JTokenType.String && ((string)rtq["statement"]).Length>=1 && ((string)rtq["statement"]).Length<=8000);
                Text(rtq["signed_by"],512); Timestamp(rtq["signed_at"]); r.ResponseTimeQualified=true;
            }
            var review=v["review"];
            if(review.Type!=JTokenType.Null) { Keys(review,"reviewed_by","reviewed_at"); Text(review["reviewed_by"],512); Timestamp(review["reviewed_at"]); r.Reviewed=true; }

            // The schema's allOf rules.
            if(r.EvidenceKind=="synthetic_fixture") Need(r.Status=="provisional");
            if(r.TargetMet) Need(r.RouteOffsetMs.HasValue && r.OnsetUncertaintyMs.HasValue && r.OnsetUncertaintyMs.Value<=20 && matched>=200);
            if(r.Status=="qualified") Need(r.Qualified);
            return r;
        }

        static void Need(bool condition) { if(!condition) throw new AudioFault("AUDIO_CALIBRATION_RECORD_INVALID"); }
        static void Keys(JToken value,IEnumerable<string> keys)
        { Need(value is JObject o && new HashSet<string>(o.Properties().Select(x=>x.Name),StringComparer.Ordinal).SetEquals(keys)); }
        static void Keys(JToken value,params string[] keys) => Keys(value,(IEnumerable<string>)keys);
        static string Text(JToken value) { Need(value?.Type==JTokenType.String); return (string)value; }
        static string Text(JToken value,int maximum)
        { string text=Text(value); Need(Regex.IsMatch(text,"\\A[^\\x00-\\x1f\\x7f]{1,"+maximum+"}\\z",RegexOptions.CultureInvariant)); return text; }
        static string Id(JToken value) { string text=Text(value); Need(Regex.IsMatch(text,"\\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\\z",RegexOptions.CultureInvariant)); return text; }
        static string OneOf(JToken value,params string[] choices) { string text=Text(value); Need(choices.Contains(text)); return text; }
        static void Hash(JToken value) => Need(PackageRules.IsHash(Text(value)));
        static bool Bool(JToken value) { Need(value?.Type==JTokenType.Boolean); return (bool)value; }
        static long Integer(JToken value,long minimum,long maximum)
        { Need(value?.Type==JTokenType.Integer); long n=(long)value; Need(n>=minimum && n<=maximum); return n; }
        static double Number(JToken value)
        {
            Need(value?.Type is JTokenType.Integer or JTokenType.Float); double n=(double)value;
            Need(!double.IsNaN(n) && !double.IsInfinity(n)); return n;
        }
        static double NonNegative(JToken value) { double n=Number(value); Need(n>=0); return n; }
        static double? NullableNumber(JToken value) => value?.Type==JTokenType.Null?(double?)null:Number(value);
        static double? NullableNonNegative(JToken value) => value?.Type==JTokenType.Null?(double?)null:NonNegative(value);
        static readonly Regex TimestampPattern=new Regex("\\A([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\\.[0-9]{1,6})?(?:Z|[+-]([0-9]{2}):([0-9]{2}))\\z",RegexOptions.CultureInvariant);
        static void Timestamp(JToken value)
        {
            var match=TimestampPattern.Match(Text(value)); Need(match.Success);
            Need(DateTime.TryParseExact(match.Groups[1].Value,"yyyy-MM-dd'T'HH:mm:ss",CultureInfo.InvariantCulture,DateTimeStyles.None,out _));
            if(match.Groups[2].Success) Need(int.Parse(match.Groups[2].Value,CultureInfo.InvariantCulture)<=23 && int.Parse(match.Groups[3].Value,CultureInfo.InvariantCulture)<=59);
        }
    }

    // The audio setup the app is actually running with. Station, route,
    // connection mode and output device are declared in station configuration
    // (Unity cannot observe the transducer); sample rate and DSP buffers are
    // read from the audio device.
    public sealed class AudioRuntimeRoute
    {
        public string StationId { get; }
        public string Route { get; }
        public string ConnectionMode { get; }
        public string OutputDevice { get; }
        public int SampleRateHz { get; }
        public int BufferFrames { get; }
        public int BufferCount { get; }
        public AudioRuntimeRoute(string stationId,string route,string connectionMode,string outputDevice,int sampleRateHz,int bufferFrames,int bufferCount)
        {
            StationId=stationId; Route=route; ConnectionMode=connectionMode; OutputDevice=outputDevice;
            SampleRateHz=sampleRateHz; BufferFrames=bufferFrames; BufferCount=bufferCount;
        }
        public static AudioRuntimeRoute Observe(JObject station)
        {
            AudioSettings.GetDSPBufferSize(out int frames,out int count);
            var audio=station?["audio"] as JObject;
            return new AudioRuntimeRoute(station?["station_id"]?.Type==JTokenType.String?(string)station["station_id"]:null,
                audio?["route"]?.Type==JTokenType.String?(string)audio["route"]:null,
                audio?["connection_mode"]?.Type==JTokenType.String?(string)audio["connection_mode"]:null,
                audio?["output_device"]?.Type==JTokenType.String?(string)audio["output_device"]:null,
                AudioSettings.outputSampleRate,frames,count);
        }
    }
}
