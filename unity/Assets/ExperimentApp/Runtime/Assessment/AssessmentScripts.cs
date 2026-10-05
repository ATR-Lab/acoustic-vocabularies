using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Assessment
{
    // Exact participant wording is independently reviewed and pinned. No trial,
    // answer, substitution template or private schedule field enters a script.
    public sealed class AssessmentScripts
    {
        static readonly string[] Required={"pre_old","trained","novel","atomic","validity","break","forms","post_w4_optional"};
        readonly Dictionary<string,string> scripts;
        public string Sha256{get;}
        public string ReviewSha256{get;}
        public string MethodologySha256{get;}
        AssessmentScripts(Dictionary<string,string> values,string hash,string review,string methodology)
        {scripts=values;Sha256=hash;ReviewSha256=review;MethodologySha256=methodology;}
        static void Need(bool value){if(!value)throw new AssessmentFault("ASSESSMENT_SCRIPT_REVIEW_INVALID");}
        static bool Hash(string value)=>value!=null&&Regex.IsMatch(value,@"\A[0-9a-f]{64}\z");
        static void Keys(JToken token,params string[] names)=>Need(token is JObject obj&&obj.Properties().Select(x=>x.Name).OrderBy(x=>x,StringComparer.Ordinal).SequenceEqual(names.OrderBy(x=>x,StringComparer.Ordinal)));
        public static AssessmentScripts Load(byte[] bytes,string independentlyPinnedHash,byte[] reviewBytes,string independentlyPinnedReviewHash)
        {
            try
            {
                Need(bytes!=null&&bytes.Length>0&&bytes.Length<=16384&&reviewBytes!=null&&reviewBytes.Length>0&&reviewBytes.Length<=4096&&
                    Hash(independentlyPinnedHash)&&Hash(independentlyPinnedReviewHash)&&PcmWave.Hash(bytes)==independentlyPinnedHash&&PcmWave.Hash(reviewBytes)==independentlyPinnedReviewHash);
                var document=StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));var review=StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(reviewBytes));
                Keys(document,"version","scripts");Need(document["version"].Type==JTokenType.Integer&&(int)document["version"]==1);Keys(document["scripts"],Required);
                Keys(review,"version","approved","scripts_sha256","methodology_sha256");
                Need(review["version"].Type==JTokenType.Integer&&(int)review["version"]==1&&review["approved"].Type==JTokenType.Boolean&&(bool)review["approved"]&&
                    (string)review["scripts_sha256"]==independentlyPinnedHash&&review["methodology_sha256"].Type==JTokenType.String&&Hash((string)review["methodology_sha256"]));
                var result=new Dictionary<string,string>(StringComparer.Ordinal);
                foreach(string name in Required)
                {
                    var token=document["scripts"][name];Need(token.Type==JTokenType.String);string value=(string)token;
                    Need(value.Length>0&&value.Length<=1024&&!value.Any(c=>char.IsControl(c)&&c!='\n')&&!value.Contains("{")&&!value.Contains("}"));result.Add(name,value);
                }
                return new AssessmentScripts(result,independentlyPinnedHash,independentlyPinnedReviewHash,(string)review["methodology_sha256"]);
            }
            catch{throw new AssessmentFault("ASSESSMENT_SCRIPT_REVIEW_INVALID");}
        }
        public string For(string block)
        {if(block==null||!scripts.TryGetValue(block,out var value))throw new AssessmentFault("ASSESSMENT_SCRIPT_UNAVAILABLE");return value;}
    }
}
