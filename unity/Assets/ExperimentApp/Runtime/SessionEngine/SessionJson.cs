using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.SessionEngine
{
    internal static class SessionJson
    {
        internal static void Require(bool value,string code="SESSION_DATA_INVALID") { if(!value) throw new SessionFault(code); }
        internal static bool Hash(string value) => value!=null && Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        internal static JObject Parse(byte[] bytes,int maximum=2097152)
        {
            try
            {
                Require(bytes!=null && bytes.Length>0 && bytes.Length<=maximum);string text=new UTF8Encoding(false,true).GetString(bytes);
                Syntax.Validate(text);
                using var reader=new JsonTextReader(new StringReader(text)){DateParseHandling=DateParseHandling.None,MaxDepth=32,FloatParseHandling=FloatParseHandling.Decimal};
                var value=JObject.Load(reader,new JsonLoadSettings {DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error,CommentHandling=CommentHandling.Load});
                Require(!reader.Read());return value;
            }
            catch(SessionFault) {throw;} catch {throw new SessionFault("SESSION_JSON_INVALID");}
        }
        internal static void Keys(JToken value,params string[] keys) => Require(value is JObject o && o.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(keys.OrderBy(x=>x,StringComparer.Ordinal)));
        internal static byte[] Bytes(JObject value) => new UTF8Encoding(false).GetBytes(value.ToString(Formatting.None)+"\n");
        sealed class Syntax
        {
            readonly string text;int at;Syntax(string text){this.text=text;}
            internal static void Validate(string text){var p=new Syntax(text);p.Value(0);p.Space();Require(p.at==text.Length);}
            void Space(){while(at<text.Length && " \r\n\t".IndexOf(text[at])>=0)at++;}
            bool Take(char c){Space();if(at<text.Length && text[at]==c){at++;return true;}return false;}
            void Need(char c){Require(Take(c));}
            void Value(int depth)
            {
                Require(depth<=32);Space();Require(at<text.Length);
                if(Take('{')){if(Take('}'))return;do{String();Need(':');Value(depth+1);}while(Take(','));Need('}');return;}
                if(Take('[')){if(Take(']'))return;do{Value(depth+1);}while(Take(','));Need(']');return;}
                if(text[at]=='"'){String();return;}
                int start=at;while(at<text.Length && " \t\r\n,]}".IndexOf(text[at])<0)at++;
                string token=text.Substring(start,at-start);
                Require(token=="true"||token=="false"||token=="null"||Regex.IsMatch(token,@"\A-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?\z"));
            }
            void String()
            {
                Need('"');while(at<text.Length){char c=text[at++];if(c=='"')return;Require(c>=32);if(c!='\\')continue;
                    Require(at<text.Length);char e=text[at++];if(e=='u'){for(int i=0;i<4;i++)Require(at<text.Length && Uri.IsHexDigit(text[at++]));}
                    else Require("\"\\/bfnrt".IndexOf(e)>=0);}
                throw new SessionFault("SESSION_JSON_STRING");
            }
        }
    }
    // Exact finite vocabulary used by the pinned #30 schemas. No network refs,
    // fallback validation or silently ignored future keywords.
    internal sealed class ScheduleSchema
    {
        readonly JObject root;
        static readonly string[] supported={"$schema","$id","$defs","$ref","title","description","type","additionalProperties","required","properties","const","enum","oneOf","allOf","if","then","not","minimum","maximum","minItems","maxItems","minProperties","maxProperties","minLength","maxLength","uniqueItems","items","prefixItems","pattern"};
        internal ScheduleSchema(byte[] bytes,string expectedHash)
        { SessionJson.Require(PcmWave.Hash(bytes)==expectedHash,"SESSION_SCHEMA_HASH");root=SessionJson.Parse(bytes);Check(root); }
        void Check(JToken schema)
        {
            if(schema.Type==JTokenType.Boolean)return;
            SessionJson.Require(schema is JObject);var obj=(JObject)schema;
            SessionJson.Require(obj.Properties().All(p=>supported.Contains(p.Name)),"SESSION_SCHEMA_UNSUPPORTED");
            foreach(string key in new[]{"$defs","properties"})if(obj[key] is JObject children)foreach(var p in children.Properties())Check(p.Value);
            foreach(string key in new[]{"oneOf","allOf","prefixItems"})if(obj[key] is JArray list)foreach(var v in list)Check(v);
            foreach(string key in new[]{"items","additionalProperties","if","then","not"})if(obj[key]!=null)Check(obj[key]);
        }
        internal void Validate(JToken value)=>Node(value,root,0);
        void Node(JToken value,JToken schema,int depth)
        {
            SessionJson.Require(depth<64);
            if(schema.Type==JTokenType.Boolean){SessionJson.Require((bool)schema);return;}
            if(schema["allOf"] is JArray all)foreach(var option in all)Node(value,option,depth+1);
            if(schema["not"]!=null)SessionJson.Require(!Matches(value,schema["not"],depth+1));
            if(schema["if"]!=null && Matches(value,schema["if"],depth+1) && schema["then"]!=null)Node(value,schema["then"],depth+1);
            if(schema["$ref"]!=null)
            {
                string path=(string)schema["$ref"];SessionJson.Require(path.StartsWith("#/$defs/",StringComparison.Ordinal));
                var target=root["$defs"][path.Substring(8)];SessionJson.Require(target!=null);Node(value,target,depth+1);return;
            }
            if(schema["oneOf"] is JArray alternatives)
            { int count=0;foreach(var option in alternatives){try{Node(value,option,depth+1);count++;}catch(SessionFault){}}SessionJson.Require(count==1); }
            if(schema["const"]!=null)SessionJson.Require(JToken.DeepEquals(value,schema["const"]));
            if(schema["enum"] is JArray choices)SessionJson.Require(choices.Any(x=>JToken.DeepEquals(x,value)));
            string kind=value.Type switch {JTokenType.Object=>"object",JTokenType.Array=>"array",JTokenType.String=>"string",JTokenType.Integer=>"integer",JTokenType.Float=>"number",JTokenType.Boolean=>"boolean",JTokenType.Null=>"null",_=>"invalid"};
            if(schema["type"]!=null)
            { var types=schema["type"] is JArray ts?ts.Select(x=>(string)x).ToArray():new[]{(string)schema["type"]};SessionJson.Require(types.Contains(kind)||kind=="integer"&&types.Contains("number")); }
            if(value is JObject obj)
            {
                if(schema["required"] is JArray required)foreach(string key in required)SessionJson.Require(obj.ContainsKey(key));
                Bound(obj.Count,schema,"minProperties","maxProperties");
                foreach(var p in obj.Properties())
                {
                    var child=schema["properties"]?[p.Name]??schema["additionalProperties"];
                    if(child!=null)Node(p.Value,child,depth+1);
                }
            }
            if(value is JArray array)
            {
                Bound(array.Count,schema,"minItems","maxItems");
                if((bool?)schema["uniqueItems"]==true)SessionJson.Require(array.Distinct(new JTokenEqualityComparer()).Count()==array.Count);
                var prefixes=schema["prefixItems"] as JArray;
                for(int i=0;i<array.Count;i++){var child=prefixes!=null && i<prefixes.Count?prefixes[i]:schema["items"];if(child!=null)Node(array[i],child,depth+1);}
            }
            if(kind=="string")
            {
                string s=(string)value;Bound(s.Length,schema,"minLength","maxLength");
                if(schema["pattern"]!=null)
                { string pattern=(string)schema["pattern"];if(pattern.StartsWith("^"))pattern="\\A"+pattern.Substring(1);if(pattern.EndsWith("$"))pattern=pattern.Substring(0,pattern.Length-1)+"\\z";
                    SessionJson.Require(Regex.IsMatch(s,pattern,RegexOptions.CultureInvariant,TimeSpan.FromMilliseconds(50))); }
            }
            if(kind=="integer"||kind=="number"){double n=(double)value;SessionJson.Require(!double.IsNaN(n)&&!double.IsInfinity(n));Bound(n,schema,"minimum","maximum");}
        }
        static void Bound(double value,JToken schema,string minimum,string maximum)
        { SessionJson.Require((schema[minimum]==null || value>=(double)schema[minimum])&&(schema[maximum]==null || value<=(double)schema[maximum])); }
        bool Matches(JToken value,JToken schema,int depth){try{Node(value,schema,depth);return true;}catch(SessionFault){return false;}}
    }
}
