using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.StateSources;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.Orientation
{
    public static class ReceiptCanonical
    {
        static JToken Sorted(JToken value)=>value is JObject o?new JObject(o.Properties().OrderBy(p=>p.Name,StringComparer.Ordinal).Select(p=>new JProperty(p.Name,Sorted(p.Value)))):
            value is JArray a?new JArray(a.Select(Sorted)):value.DeepClone();
        public static byte[] Bytes(JToken value)
        {
            var text=new StringBuilder();using(var writer=new JsonTextWriter(new StringWriter(text,System.Globalization.CultureInfo.InvariantCulture)) {Formatting=Formatting.None,StringEscapeHandling=StringEscapeHandling.EscapeNonAscii})Sorted(value).WriteTo(writer);
            return Encoding.ASCII.GetBytes(text.ToString());
        }
        public static string Hash(JToken value)=>SceneRegistry.Hash(Bytes(value));
        public static JObject Seal(JObject payload){var copy=(JObject)payload.DeepClone();if(copy.ContainsKey("receipt_sha256"))throw new OrientationFault("RECEIPT_ALREADY_SEALED");copy["receipt_sha256"]=Hash(copy);return copy;}
        public static bool Valid(JObject receipt){if(receipt==null||receipt["receipt_sha256"]?.Type!=JTokenType.String)return false;var copy=(JObject)receipt.DeepClone();string pin=(string)copy["receipt_sha256"];copy.Remove("receipt_sha256");return pin==Hash(copy);}
    }
    // Created only from the journal that has already fsynced the Flow outcome.
    // It is eligibility evidence, never consent, allocation or audio authority.
    public sealed class OrientationReceipt
    {
        readonly JObject value;
        public JObject Json=>(JObject)value.DeepClone();
        public string Sha256=>(string)value["receipt_sha256"];
        public string ScreeningId=>(string)value["screening_id"];
        public string OrientationId=>(string)value["orientation_id"];
        public bool Eligible=>(bool)value["eligible"];
        internal OrientationReceipt(JObject payload){value=ReceiptCanonical.Seal(payload);}
    }
}
