using System;
using System.Globalization;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StudyAudio
{
    // Hard ceiling on the comfortable level. The station's coupler measurement
    // (audio.level_calibration) gives the dB(A) peak at the ear for a 0 dBFS
    // peak at gain 0 dB (linear gain 1). The largest allowed gain is then
    // cap - measurement - loudest stimulus peak, in dB. A station without that
    // measurement plays no study sound, and no gain above the cap is used.
    public sealed class LevelCap
    {
        // Decision I7: each person's comfortable level is chosen below a hard
        // cap of 80 dB(A) peak at the ear, verified with the coupler at install.
        public const double CapDbaPeakAtEar=80;
        // Packages do not record a per-file peak, so every stimulus is assumed
        // able to reach full scale. This gives the lowest (safest) gain limit.
        public const double LoudestStimulusPeakDbfs=0;
        public const double MinimumMeasurementDba=40,MaximumMeasurementDba=140;
        // Lowest level the control steps down to, unless the cap is lower.
        public const float MinimumStepGain=.01f;
        const string IdPattern="\\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\\z";

        public string Route { get; }
        public double EarPeakDbaAt0Dbfs { get; }
        public string MeasuredOn { get; }
        public string CouplerId { get; }
        public double MaxGainDb => CapDbaPeakAtEar-EarPeakDbaAt0Dbfs-LoudestStimulusPeakDbfs;
        // The largest float gain whose level in dB does not exceed the cap.
        public float MaxGain { get; }

        public LevelCap(string route,double earPeakDbaAt0Dbfs,string measuredOn,string couplerId)
        {
            if(route==null || !Regex.IsMatch(route,IdPattern) || couplerId==null || !Regex.IsMatch(couplerId,IdPattern) ||
                !AudioRouteCalibration.Finite(earPeakDbaAt0Dbfs) || earPeakDbaAt0Dbfs<MinimumMeasurementDba || earPeakDbaAt0Dbfs>MaximumMeasurementDba ||
                measuredOn==null || !Regex.IsMatch(measuredOn,"\\A[0-9]{4}-[0-9]{2}-[0-9]{2}\\z") ||
                !DateTime.TryParseExact(measuredOn,"yyyy-MM-dd",CultureInfo.InvariantCulture,DateTimeStyles.None,out _))
                throw new AudioFault("LEVEL_CAP_OFFSET_INVALID");
            Route=route;EarPeakDbaAt0Dbfs=earPeakDbaAt0Dbfs;MeasuredOn=measuredOn;CouplerId=couplerId;
            MaxGain=LargestGainAtOrBelow(Math.Min(0,MaxGainDb));
        }

        public static double GainDb(float gain) => 20*Math.Log10(gain);
        static float LargestGainAtOrBelow(double limitDb)
        {
            float gain=(float)Math.Pow(10,limitDb/20);
            while(GainDb(gain)>limitDb) gain=BitConverter.Int32BitsToSingle(BitConverter.SingleToInt32Bits(gain)-1);
            return gain;
        }

        // Reads audio.level_calibration from a station configuration. Missing:
        // LEVEL_CAP_OFFSET_MISSING. Malformed: LEVEL_CAP_OFFSET_INVALID. Measured
        // on another audio route: LEVEL_CAP_ROUTE_MISMATCH (measure again).
        public static LevelCap FromStation(JObject station)
        {
            var audio=station?["audio"] as JObject;
            var record=audio?["level_calibration"];
            if(record==null || record.Type==JTokenType.Null) throw new AudioFault("LEVEL_CAP_OFFSET_MISSING");
            if(record is not JObject value) throw new AudioFault("LEVEL_CAP_OFFSET_INVALID");
            var offset=value["ear_peak_dba_at_0dbfs"];
            if(offset==null || offset.Type==JTokenType.Null) throw new AudioFault("LEVEL_CAP_OFFSET_MISSING");
            if(!value.Properties().Select(p=>p.Name).OrderBy(n=>n,StringComparer.Ordinal).SequenceEqual(new[]{"coupler_id","ear_peak_dba_at_0dbfs","measured_on","route"}) ||
                offset.Type is not (JTokenType.Integer or JTokenType.Float) ||
                value["route"]?.Type!=JTokenType.String || value["measured_on"]?.Type!=JTokenType.String || value["coupler_id"]?.Type!=JTokenType.String ||
                audio["route"]?.Type!=JTokenType.String) throw new AudioFault("LEVEL_CAP_OFFSET_INVALID");
            var cap=new LevelCap((string)value["route"],(double)offset,(string)value["measured_on"],(string)value["coupler_id"]);
            if(cap.Route!=(string)audio["route"]) throw new AudioFault("LEVEL_CAP_ROUTE_MISMATCH");
            return cap;
        }

        public bool Allows(float gain) => !float.IsNaN(gain) && gain>0 && gain<=MaxGain;
        // A stored, carried or held gain above the cap is refused, never lowered:
        // lowering it silently would change the person's level between visits.
        public float Require(float gain)
        {
            if(float.IsNaN(gain) || float.IsInfinity(gain) || gain<=0) throw new AudioFault("AUDIO_GAIN_INVALID");
            if(gain>MaxGain) throw new AudioFault("GAIN_ABOVE_CAP");
            return gain;
        }
        // The level control: one step from the current gain, clamped so it can
        // never be raised beyond the cap.
        public float Step(float current,float delta)
        {
            if(float.IsNaN(current) || float.IsInfinity(current) || float.IsNaN(delta) || float.IsInfinity(delta)) throw new AudioFault("AUDIO_GAIN_INVALID");
            float low=Math.Min(MinimumStepGain,MaxGain);
            return Math.Max(low,Math.Min(MaxGain,current+delta));
        }
        // Starting level for a person with no recorded history: the usual
        // initial gain, or the cap when the cap is lower. Never above the cap.
        public float Initial(float initialGain) => Math.Min(initialGain,MaxGain);
    }
}
