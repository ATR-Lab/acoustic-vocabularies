using System;
using AcousticVocab.Foundation;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.ResponsePanel
{
    public sealed class PanelSettings
    {
        public string InputMethod { get; private set; }
        public bool LeftHand { get; private set; }
        public float Distance { get; private set; }
        public float VerticalOffset { get; private set; }
        public float TextAngleDegrees { get; private set; }
        public float ButtonWidth { get; private set; }
        public float ButtonHeight { get; private set; }
        public float Gap { get; private set; }
        public string EngineeringMode { get; private set; }
        public PanelRole EngineeringRole { get; private set; }
        public double PracticeWindowMs { get; private set; }
        public JObject RecordedConfiguration { get; private set; }
        public static PanelSettings Parse(string json, string schema, string protocol)
        {
            var value = StationConfig.ValidateDocument(json, schema);
            if ((string)value["protocol_version"] != protocol || (string)value["configuration_status"] != "provisioned_engineering") throw new ConfigurationFault("panel_not_provisioned_for_protocol");
            var settings = new PanelSettings { InputMethod = (string)value["input_method"], LeftHand = (string)value["hand"] == "left",
                Distance = (float)value["distance_m"], VerticalOffset = (float)value["vertical_offset_m"], TextAngleDegrees = (float)value["text_angle_deg"],
                ButtonWidth = (float)value["button_width_m"], ButtonHeight = (float)value["button_height_m"], Gap = (float)value["gap_m"],
                EngineeringMode = (string)value["engineering_mode"], EngineeringRole = Role((string)value["engineering_role"]),
                PracticeWindowMs = (double)value["practice_window_ms"], RecordedConfiguration = (JObject)value.DeepClone() };
            if (settings.EngineeringMode != "disabled") settings.EngineeringRequest(0);
            return settings;
        }
        static PanelRole Role(string value) => value == "command" ? PanelRole.Command : value == "action" ? PanelRole.Action : PanelRole.Target;
        public static PanelMode Mode(string value) => value switch { "full_message" => PanelMode.FullMessage, "atomic_probe" => PanelMode.AtomicProbe,
            "lesson_atomic" => PanelMode.LessonAtomic, "lesson_message" => PanelMode.LessonMessage, "practice" => PanelMode.Practice, _ => throw new ArgumentException("Unknown engineering mode") };
        public PanelRequest EngineeringRequest(double now) => new PanelRequest("engineering-one-shot", Mode(EngineeringMode), EngineeringRole, now,
            EngineeringMode == "practice" ? PracticeWindowMs : (double?)null);
        public void VerifyStationInput(string method)
        { if (method != (InputMethod == "controller_ray" ? "controllers" : "hands")) throw new ConfigurationFault("panel_station_input_mismatch"); }
    }
}
