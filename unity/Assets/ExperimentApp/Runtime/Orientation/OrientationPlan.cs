using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Orientation
{
    public sealed class OrientationFault : Exception { public OrientationFault(string code) : base(code) { } }
    public sealed class MeaningCard
    {
        public string Id { get; }
        public string Title { get; }
        public string Meaning { get; }
        internal MeaningCard(string id,string title,string meaning) { Id=id;Title=title;Meaning=meaning; }
    }
    public sealed class PracticeItem
    {
        public string Id { get; }
        public string Target { get; }
        public string Action { get; }
        public string Request { get; }
        internal PracticeItem(string id,string target,string action,string request) { Id=id;Target=target;Action=action;Request=request; }
    }
    public sealed class OrientationPlan
    {
        public string ProtocolVersion { get; private set; }
        public bool EngineeringDraft { get; private set; }
        public string ReviewEvidence { get; private set; }
        public string SourceNote { get; private set; }
        public double PracticeWindowMs { get; private set; }
        public IReadOnlyList<MeaningCard> Actions { get; private set; }
        public IReadOnlyList<MeaningCard> Targets { get; private set; }
        public IReadOnlyList<PracticeItem> FirstCheck { get; private set; }
        public IReadOnlyList<PracticeItem> SecondCheck { get; private set; }
        public static OrientationPlan Parse(string json,string protocolVersion)
        {
            var v=StationConfig.ParseStrict(json);
            Keys(v,"version","protocol_version","content_status","review_evidence_sha256","source_note","practice_window_ms","actions","targets","practice_pairs","second_order");
            Require(v["version"].Type==JTokenType.Integer && (int)v["version"]==1,"ORIENTATION_PLAN_VERSION");
            string version=Id(v["protocol_version"]), status=Text(v["content_status"],40);
            Require(version==protocolVersion && (status=="engineering_draft" || status=="protocol_reviewed"),"ORIENTATION_PLAN_IDENTITY");
            bool draft=status=="engineering_draft";
            string evidence=v["review_evidence_sha256"].Type==JTokenType.Null?null:Text(v["review_evidence_sha256"],64);
            Require(draft?evidence==null:Hash(evidence),"ORIENTATION_REVIEW_EVIDENCE");
            double window=Number(v["practice_window_ms"]);Require(window>=1 && window<=600000,"ORIENTATION_PRACTICE_WINDOW");
            var actions=Cards(v["actions"],PublicCommands.Actions);var targets=Cards(v["targets"],PublicCommands.Targets);
            Require(v["practice_pairs"] is JArray pairs && pairs.Count==8,"ORIENTATION_PRACTICE_COVERAGE");
            var items=new List<PracticeItem>();
            foreach(var value in (JArray)v["practice_pairs"])
            {
                var p=value as JObject;Keys(p,"id","target","action","request");
                string target=Id(p["target"]), action=Id(p["action"]);
                Require(PublicCommands.Legal(target,action),"ORIENTATION_ILLEGAL_PAIR");
                items.Add(new PracticeItem(Id(p["id"]),target,action,Text(p["request"],240)));
            }
            Require(items.Select(x=>x.Id).Distinct().Count()==8 && items.Select(x=>x.Target).Distinct().Count()==8 && items.Select(x=>x.Action).Distinct().Count()==8,"ORIENTATION_PRACTICE_COVERAGE");
            Require(v["second_order"] is JArray order && order.Count==8,"ORIENTATION_SECOND_ORDER");
            var ids=((JArray)v["second_order"]).Select(Id).ToArray();
            Require(ids.Distinct().Count()==8 && ids.All(x=>items.Any(y=>y.Id==x)) && !ids.SequenceEqual(items.Select(x=>x.Id)),"ORIENTATION_SECOND_ORDER");
            return new OrientationPlan { ProtocolVersion=version,EngineeringDraft=draft,ReviewEvidence=evidence,SourceNote=Text(v["source_note"],400),PracticeWindowMs=window,
                Actions=Array.AsReadOnly(actions),Targets=Array.AsReadOnly(targets),FirstCheck=Array.AsReadOnly(items.ToArray()),SecondCheck=Array.AsReadOnly(ids.Select(id=>items.Single(x=>x.Id==id)).ToArray()) };
        }
        static MeaningCard[] Cards(JToken token,IReadOnlyList<string> required)
        {
            Require(token is JArray a && a.Count==8,"ORIENTATION_CARD_COVERAGE");var cards=new List<MeaningCard>();
            foreach(var value in (JArray)token) { var v=value as JObject;Keys(v,"id","title","meaning");cards.Add(new MeaningCard(Id(v["id"]),Text(v["title"],50),Text(v["meaning"],240))); }
            Require(cards.Select(x=>x.Id).Distinct().Count()==8 && required.All(x=>cards.Any(y=>y.Id==x)),"ORIENTATION_CARD_COVERAGE");return cards.ToArray();
        }
        internal static void Keys(JObject v,params string[] keys) { Require(v!=null && v.Count==keys.Length && v.Properties().All(p=>keys.Contains(p.Name)),"ORIENTATION_FIELDS"); }
        internal static string Text(JToken t,int max) { Require(t!=null && t.Type==JTokenType.String,"ORIENTATION_TEXT");string s=(string)t;Require(s.Length>0 && s.Length<=max && !s.Any(char.IsControl),"ORIENTATION_TEXT");return s; }
        internal static string Id(JToken t) { string s=Text(t,80);Require(Regex.IsMatch(s,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z"),"ORIENTATION_IDENTIFIER");return s; }
        internal static double Number(JToken t) { Require(t!=null && (t.Type==JTokenType.Float || t.Type==JTokenType.Integer),"ORIENTATION_NUMBER");double n=(double)t;Require(!double.IsNaN(n) && !double.IsInfinity(n),"ORIENTATION_NUMBER");return n; }
        internal static bool Hash(string s) => s!=null && Regex.IsMatch(s,@"\A[0-9a-f]{64}\z");
        internal static void Require(bool pass,string code) { if(!pass) throw new OrientationFault(code); }
    }
}
