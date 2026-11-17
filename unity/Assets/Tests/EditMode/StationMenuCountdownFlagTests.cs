using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Foundation.Tests
{
    // Station flag for the optional visible selection-menu countdown.
    public class StationMenuCountdownFlagTests
    {
        const string Flag = "menu_countdown_visible";
        string Schema => File.ReadAllText("Assets/ExperimentApp/Resources/StationConfigSchema.json");
        JObject Provisioned { get { var value = JObject.Parse(File.ReadAllText("Assets/Tests/EditMode/StationExample.json")); value["provisioning_status"] = "provisioned"; return value; } }
        JObject Check(JObject value) => StationConfig.Validate(value.ToString(), Schema, (string)value["protocol_version"]);

        [Test, Category("REQ_MENU_08"), NUnit.Framework.Property("Requirement", "REQ-MENU-08")]
        public void FlagIsAnOptionalBooleanThatIsOffByDefault()
        {
            var schema = JObject.Parse(Schema); var spec = (JObject)schema["properties"][Flag];
            Assert.That((string)spec["type"], Is.EqualTo("boolean"));
            Assert.That(spec["default"].Type, Is.EqualTo(JTokenType.Boolean)); Assert.That((bool)spec["default"], Is.False);
            Assert.That(((JArray)schema["required"]).Select(x => (string)x), Does.Not.Contain(Flag));
            Assert.That(Provisioned.ContainsKey(Flag), Is.False, "The public example keeps the default by omission");
            var config = Check(Provisioned);
            Assert.That(config.ContainsKey(Flag), Is.False, "Validation must not insert the annotation default");
            Assert.That(StationConfig.MenuCountdownVisible(config), Is.False);
        }

        [TestCase(true)] [TestCase(false)]
        [Category("REQ_MENU_08"), NUnit.Framework.Property("Requirement", "REQ-MENU-08")]
        public void ExplicitFlagValidatesAndIsReadExactly(bool visible)
        {
            var value = Provisioned; value[Flag] = visible;
            Assert.That(StationConfig.MenuCountdownVisible(Check(value)), Is.EqualTo(visible));
        }

        [Test, Category("REQ_MENU_08"), NUnit.Framework.Property("Requirement", "REQ-MENU-08")]
        public void NonBooleanFlagFailsClosed()
        {
            foreach (var bad in new JToken[] { "true", "false", 1, 0, JValue.CreateNull(), new JArray(), new JObject() })
            {
                var value = Provisioned; value[Flag] = bad;
                Assert.Throws<ConfigurationFault>(() => Check(value), bad.ToString());
                Assert.Throws<ConfigurationFault>(() => StationConfig.MenuCountdownVisible(new JObject { [Flag] = bad.DeepClone() }), bad.ToString());
            }
            var nested = Provisioned; nested["audio"][Flag] = true;
            Assert.Throws<ConfigurationFault>(() => Check(nested), "The flag exists only at the top level");
        }

        [Test, Category("REQ_MENU_08"), NUnit.Framework.Property("Requirement", "REQ-MENU-08")]
        public void BooleanSupportDoesNotLoosenOtherFields()
        {
            foreach (var field in new[] { "station_id", "refresh_hz", "input_method" })
            { var value = Provisioned; value[field] = true; Assert.Throws<ConfigurationFault>(() => Check(value), field); }
        }
    }
}
