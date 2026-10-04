using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.Foundation
{
    public sealed class ConfigurationFault : Exception
    {
        public ConfigurationFault(string code) : base(code) { }
    }

    // Deliberately bounded JSON Schema vocabulary. Unsupported validation keywords fail closed.
    // The canonical schema and embedded copy are compared before every build and in tests.
    public static class StationConfig
    {
        static readonly string[] Keywords = { "$schema", "$id", "title", "description", "type", "properties",
            "required", "additionalProperties", "enum", "minLength", "maxLength", "pattern", "minimum",
            "maximum", "items", "minItems", "maxItems" };

        public static JObject ParseStrict(string json)
        {
            if (json == null || json.Length > 65536) throw new ConfigurationFault("json_size");
            StrictJsonSyntax.Validate(json);
            try
            {
                using var reader = new JsonTextReader(new StringReader(json)) { DateParseHandling = DateParseHandling.None, MaxDepth = 16 };
                var token = JToken.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error, CommentHandling = CommentHandling.Load });
                if (reader.Read() || token.Type != JTokenType.Object)
                    throw new ConfigurationFault("json_structure");
                return (JObject)token;
            }
            catch (JsonException) { throw new ConfigurationFault("json_syntax"); }
        }

        public static JObject Validate(string json, string schema, string protocolVersion, bool allowExample = false)
        {
            var value = ParseStrict(json);
            CheckSchema(ParseStrict(schema));
            ValidateNode(value, ParseStrict(schema));
            if ((string)value["protocol_version"] != protocolVersion) throw new ConfigurationFault("protocol_mismatch");
            if (!allowExample && (string)value["provisioning_status"] != "provisioned") throw new ConfigurationFault("example_not_provisioned");
            if (!Uri.TryCreate((string)value["isaac_endpoint"], UriKind.Absolute, out var endpoint) ||
                (endpoint.Scheme != "ws" && endpoint.Scheme != "wss") || endpoint.UserInfo.Length != 0 || endpoint.Fragment.Length != 0)
                throw new ConfigurationFault("endpoint_invalid");
            double norm = value["observer_reference"]["rotation_xyzw"].Sum(x => (double)x * (double)x);
            if (Math.Abs(norm - 1) > .0001) throw new ConfigurationFault("reference_quaternion_not_unit");
            return value;
        }

        static void CheckSchema(JObject schema)
        {
            foreach (var property in schema.Properties())
                if (!Keywords.Contains(property.Name)) throw new ConfigurationFault("unsupported_schema_keyword");
            if (schema["properties"] is JObject properties)
                foreach (var property in properties.Properties()) CheckSchema((JObject)property.Value);
            if (schema["items"] is JObject items) CheckSchema(items);
        }

        static string Kind(JToken value) => value.Type switch {
            JTokenType.Object => "object", JTokenType.Array => "array", JTokenType.String => "string",
            JTokenType.Integer => "integer", JTokenType.Float => "number", JTokenType.Null => "null", _ => "unsupported" };

        static void ValidateNode(JToken value, JObject schema)
        {
            var expected = schema["type"] is JArray types ? types.Select(x => (string)x).ToArray() : new[] { (string)schema["type"] };
            string kind = Kind(value);
            if (!expected.Contains(kind) && !(kind == "integer" && expected.Contains("number"))) throw new ConfigurationFault("schema_type");
            if (schema["enum"] is JArray choices && !choices.Any(x => JToken.DeepEquals(x, value))) throw new ConfigurationFault("schema_enum");
            if (value is JObject obj)
            {
                var properties = (JObject)schema["properties"];
                foreach (var key in (JArray)schema["required"])
                    if (!obj.ContainsKey((string)key)) throw new ConfigurationFault("schema_required");
                foreach (var property in obj.Properties())
                {
                    if (properties[property.Name] == null) throw new ConfigurationFault("schema_extra_property");
                    ValidateNode(property.Value, (JObject)properties[property.Name]);
                }
            }
            if (value is JArray array)
            {
                if (array.Count < (int)schema["minItems"] || array.Count > (int)schema["maxItems"]) throw new ConfigurationFault("schema_array_length");
                foreach (var item in array) ValidateNode(item, (JObject)schema["items"]);
            }
            if (kind == "string")
            {
                string text = (string)value;
                if (schema["minLength"] != null && text.Length < (int)schema["minLength"] ||
                    schema["maxLength"] != null && text.Length > (int)schema["maxLength"] ||
                    schema["pattern"] != null && !Regex.IsMatch(text, (string)schema["pattern"], RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(50)))
                    throw new ConfigurationFault("schema_string");
            }
            if (kind == "number" || kind == "integer")
            {
                double number = (double)value;
                if (double.IsNaN(number) || double.IsInfinity(number) ||
                    schema["minimum"] != null && number < (double)schema["minimum"] ||
                    schema["maximum"] != null && number > (double)schema["maximum"])
                    throw new ConfigurationFault("schema_number");
            }
        }

        public static Pose ReferencePose(JObject config)
        {
            var reference = config["observer_reference"];
            var p = reference["position_m"]; var q = reference["rotation_xyzw"];
            return new Pose(new Vector3((float)p[0], (float)p[1], (float)p[2]), new Quaternion((float)q[0], (float)q[1], (float)q[2], (float)q[3]));
        }
    }
}
