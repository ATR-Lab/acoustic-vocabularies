using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.SelectionMenus
{
    // Independent assets and append-only store receipts supplied by the trusted
    // owner. The ledger cannot authorize its own candidate bytes or choices.
    public sealed class MenuLedgerVerification
    {
        internal readonly Func<string,MenuOption[]> Options;
        internal readonly Func<string,int,string,bool> Receipt;
        internal readonly Func<string,string> Meaning;
        public MenuLedgerVerification(Func<string,MenuOption[]> options,Func<string,int,string,bool> receipt,Func<string,string> meaning)
        {Options=options??throw new ArgumentNullException(nameof(options));Receipt=receipt??throw new ArgumentNullException(nameof(receipt));Meaning=meaning??throw new ArgumentNullException(nameof(meaning));}
    }
    public sealed class MenuReplaySequence : ISlotStartPlan
    {
        internal sealed class Entry
        {internal string Key;internal double Start;internal MenuReplay Replay;}
        readonly Entry[] entries;readonly double anchor;readonly Func<double> clock;
        public string LedgerSha256{get;}
        public DateTimeOffset CreatedUtc{get;}
        public double SourceSpanMs=>entries.Last().Start+(entries.Last().Key=="profile"?60000:45000)-entries[0].Start;
        MenuReplaySequence(Entry[] entries,string hash,DateTimeOffset created,double anchor,Func<double> clock)
        {this.entries=entries;LedgerSha256=hash;CreatedUtc=created;this.anchor=anchor;this.clock=clock;}
        static string Key(SlotItem item)=>item.TrialType=="profile_menu"?"profile":item.ContentId;
        public MenuReplay For(SlotItem item)
        {MenuRules.Require(item!=null&&item.Phase=="selection","MENU_REPLAY_SLOT");return Find(Key(item)).Replay;}
        Entry Find(string key){var found=entries.SingleOrDefault(x=>x.Key==key);MenuRules.Require(found!=null,"MENU_REPLAY_SLOT");return found;}
        public double MinimumGapBeforeMs(SlotItem item,double baseOnsetMonoMs)
        {
            MenuRules.Require(item!=null&&item.Phase=="selection"&&MenuRules.Finite(baseOnsetMonoMs)&&baseOnsetMonoMs>=0,"MENU_REPLAY_SLOT");
            double target=anchor+Find(Key(item)).Start-entries[0].Start,now=clock();
            // No tolerance can move an old cue into the present. A reviewed
            // reconstruction supplies a new explicit anchor and its own record.
            MenuRules.Require(MenuRules.Finite(target)&&MenuRules.Finite(now)&&now>=0&&baseOnsetMonoMs<=target&&now<=target-150,"MENU_REPLAY_ANCHOR_MISSED");return target-baseOnsetMonoMs;
        }
        public static MenuReplaySequence Load(string path,string expectedFileSha256,MenuLedgerBinding expectedActiveBinding,MenuLedgerVerification verification,DateTimeOffset nowUtc,double sessionAnchorMonoMs,Func<double> clock)
        {
            MenuRules.Require(MenuRules.Hash(expectedFileSha256)&&expectedActiveBinding!=null&&expectedActiveBinding.Role=="active"&&verification!=null&&nowUtc.Offset==TimeSpan.Zero&&MenuRules.Finite(sessionAnchorMonoMs)&&sessionAnchorMonoMs>=0&&clock!=null,"MENU_REPLAY_BINDING");
            MenuJson.NoLinks(path);var info=new FileInfo(path);MenuRules.Require(info.Exists&&info.Length>0&&info.Length<=32*1024*1024,"MENU_LEDGER_SIZE");byte[] bytes=File.ReadAllBytes(path);
            MenuRules.Require(PcmWave.Hash(bytes)==expectedFileSha256&&bytes[bytes.Length-1]==10,"MENU_LEDGER_HASH");
            var lines=new UTF8Encoding(false,true).GetString(bytes).Split('\n');MenuRules.Require(lines.Length>=4&&lines.Length<=10001&&lines.Last()=="","MENU_LEDGER_LINE");
            var records=new List<JObject>();string previous=new string('0',64);
            for(int i=0;i<lines.Length-1;i++)
            {
                byte[] line=new UTF8Encoding(false,true).GetBytes(lines[i]);var env=MenuJson.Parse(line);MenuJson.Keys(env,"version","sequence","previous_sha256","record","sha256");
                MenuRules.Require(MenuJson.Integer(env["version"],1,1)==1&&MenuJson.Integer(env["sequence"],0,9999)==i&&env["previous_sha256"].Type==JTokenType.String&&(string)env["previous_sha256"]==previous&&env["sha256"].Type==JTokenType.String&&MenuRules.Hash((string)env["sha256"])&&env["record"] is JObject,"MENU_LEDGER_CHAIN");
                MenuRules.Require(line.SequenceEqual(MenuJson.Bytes(env)),"MENU_LEDGER_CANONICAL");string hash=(string)env["sha256"];env.Remove("sha256");MenuRules.Require(hash==PcmWave.Hash(MenuJson.Bytes(env)),"MENU_LEDGER_CHAIN");previous=hash;records.Add((JObject)env["record"]);
            }
            var header=records[0];MenuJson.Keys(header,"kind","format","binding","created_utc","clock_epoch");
            MenuRules.Require(header["kind"].Type==JTokenType.String&&(string)header["kind"]=="header"&&header["format"].Type==JTokenType.String&&(string)header["format"]=="av-menu-ledger/1"&&header["clock_epoch"].Type==JTokenType.String&&MenuRules.Guid((string)header["clock_epoch"])&&JToken.DeepEquals(header["binding"],expectedActiveBinding.Json())&&header["created_utc"].Type==JTokenType.String,"MENU_LEDGER_BINDING");
            MenuRules.Require(DateTimeOffset.TryParseExact((string)header["created_utc"],"yyyy-MM-dd'T'HH:mm:ss.fff'Z'",CultureInfo.InvariantCulture,DateTimeStyles.AssumeUniversal|DateTimeStyles.AdjustToUniversal,out var created)&&nowUtc>=created&&nowUtc-created<=TimeSpan.FromHours(24),"MENU_REPLAY_AGE");
            var seal=records.Last();MenuJson.Keys(seal,"kind","menu_count");MenuRules.Require(seal["kind"].Type==JTokenType.String&&(string)seal["kind"]=="sealed"&&MenuJson.Integer(seal["menu_count"],4,9)==expectedActiveBinding.MenuKeys.Count,"MENU_LEDGER_UNSEALED");
            var entries=ValidateRecords(records.Skip(1).Take(records.Count-2).ToList(),expectedActiveBinding,verification);
            return new MenuReplaySequence(entries,expectedFileSha256,created,sessionAnchorMonoMs,clock);
        }
        internal static Entry[] ValidateRecords(IReadOnlyList<JObject> records,MenuLedgerBinding binding,MenuLedgerVerification verification)
        {
            MenuRules.Require(binding!=null&&binding.Role=="active"&&verification!=null&&records.Count>0,"MENU_LEDGER_BINDING");
            var ids=new HashSet<string>(StringComparer.Ordinal);var audioIds=new HashSet<string>(StringComparer.Ordinal);double last=-1;var groups=new List<List<JObject>>();
            foreach(var row in records)
            {
                MenuJson.CheckEvent(row);double now=MenuJson.Number(row["mono_ms"]);MenuRules.Require(now>=last&&ids.Add((string)row["event_id"])&&row["yoked_source_event_id"].Type==JTokenType.Null,"MENU_LEDGER_SEQUENCE");last=now;
                if((string)row["kind"]=="menu_start")groups.Add(new List<JObject>());
                MenuRules.Require(groups.Count>0&&groups.Count<=binding.MenuKeys.Count,"MENU_LEDGER_SEQUENCE");groups.Last().Add(row);
            }
            MenuRules.Require(groups.Count==binding.MenuKeys.Count,"MENU_LEDGER_INCOMPLETE");var entries=new List<Entry>();double previousEnd=-1;
            for(int g=0;g<groups.Count;g++)
            {
                var rows=groups[g];var first=rows[0];string key=binding.MenuKeys[g],attempt=(string)first["attempt_id"],opportunity=(string)first["opportunity_id"];double start=MenuJson.Number(first["slot_start_mono_ms"]);bool profile=key=="profile";
                MenuRules.Require(start>=previousEnd&&rows.All(x=>(string)x["menu_key"]==key&&(string)x["meaning_display_id"]==verification.Meaning(key)&&(string)x["attempt_id"]==attempt&&(string)x["opportunity_id"]==opportunity&&MenuJson.Number(x["slot_start_mono_ms"])==start)&&MenuJson.Number(first["mono_ms"])<=start&&MenuJson.Number(first["expected_mono_ms"])==start&&!rows.Any(x=>(string)x["kind"]=="menu_interrupted"),"MENU_LEDGER_SEQUENCE");
                previousEnd=start+(profile?60000:45000);var options=verification.Options(key);
                MenuRules.Require(options!=null&&options.Length==3&&options.All(x=>x!=null)&&options.Select(x=>x.CandidateId).Distinct().Count()==3&&options.Select(x=>x.Wave.PcmSha256).Distinct().Count()==3&&options.All(x=>profile?x.Wave.SampleCount==96000:new[]{21600,28800,36000,43200}.Contains(x.Wave.SampleCount)),"MENU_LEDGER_ASSETS");
                double choiceOpen=start+(profile?30000:22000),deadline=start+(profile?45000:32000);
                var choice=One(rows,"choice_final");int selected=MenuJson.Integer(choice["selected_index"],1,3);MenuRules.Require(choice["defaulted"].Type==JTokenType.Boolean,"MENU_LEDGER_CHOICE");bool defaulted=(bool)choice["defaulted"];
                MenuRules.Require(MenuJson.Number(choice["expected_mono_ms"])==deadline&&MenuJson.Number(choice["mono_ms"])>=deadline&&MenuJson.Number(choice["mono_ms"])<=deadline+20,"MENU_LEDGER_TIMING");
                var revisions=rows.Where(x=>(string)x["kind"]=="choice_revised").ToArray();foreach(var revision in revisions)MenuRules.Require(MenuJson.Number(revision["mono_ms"])>=choiceOpen&&MenuJson.Number(revision["mono_ms"])<deadline,"MENU_LEDGER_CHOICE");
                MenuRules.Require(defaulted?revisions.Length==0&&selected==1:revisions.Length>0&&MenuJson.Integer(revisions.Last()["selected_index"],1,3)==selected,"MENU_LEDGER_CHOICE");
                var receipt=One(rows,"selection_verified");MenuRules.Require(MenuJson.Integer(receipt["selected_index"],1,3)==selected&&receipt["defaulted"].Type==JTokenType.Boolean&&(bool)receipt["defaulted"]==defaulted&&MenuJson.Number(receipt["mono_ms"])>=MenuJson.Number(choice["mono_ms"])&&MenuRules.Hash((string)receipt["receipt_sha256"])&&verification.Receipt(key,selected,(string)receipt["receipt_sha256"]),"MENU_LEDGER_RECEIPT");
                double[] nominal=profile?new[]{6500d,10500,14500,18500,22500,26500,50000,54000}:new[]{5000d,8000,11000,14000,17000,20000,35000,38000};var offsets=new double[8];var source=new string[8];double previousComplete=-1;
                for(int n=1;n<=8;n++)
                {
                    var play=rows.Where(x=>x["presentation_index"].Type!=JTokenType.Null&&(int)x["presentation_index"]==n).ToList();MenuRules.Require(play.Count==3,"MENU_LEDGER_PLAY_COUNT");
                    var request=One(play,"play_request");var onset=One(play,"onset_authority");var complete=One(play,"play_complete");var option=options[n<=6?(n-1)/2:selected-1];string audio=(string)request["audio_request_id"];
                    MenuRules.Require(audioIds.Add(audio)&&play.All(x=>(string)x["audio_request_id"]==audio&&(string)x["candidate_id"]==option.CandidateId&&(string)x["pcm_sha256"]==option.Wave.PcmSha256&&(string)x["file_sha256"]==option.Wave.FileSha256),"MENU_LEDGER_ASSETS");
                    double requested=MenuJson.Number(request["mono_ms"]),planned=MenuJson.Number(request["expected_mono_ms"]),actual=MenuJson.Number(onset["expected_mono_ms"]),observed=MenuJson.Number(onset["mono_ms"]),done=MenuJson.Number(complete["mono_ms"]);
                    MenuRules.Require(planned==start+nominal[n-1]&&requested>=previousComplete&&requested>=planned-750&&requested<=planned-150&&Math.Abs(actual-planned)<=20&&observed>=requested&&observed<=planned+250&&done>=observed&&done>=actual+option.Wave.SampleCount/48d-20&&done<=previousEnd&&(n<=6||MenuJson.Number(receipt["mono_ms"])<=requested),"MENU_LEDGER_TIMING");
                    offsets[n-1]=actual-start;source[n-1]=(string)request["event_id"];previousComplete=done;
                }
                var phases=new[]{MenuPhase.Instructions,MenuPhase.Audition,MenuPhase.Choice,MenuPhase.Selected,MenuPhase.Neutral,MenuPhase.Ended};double[] boundaries=profile?new[]{0d,6000,30000,45000,58000,60000}:new[]{0d,4000,22000,32000,40000,45000};
                var displays=rows.Where(x=>(string)x["kind"] is "display_request" or "display_changed").ToArray();MenuRules.Require(displays.Length==12,"MENU_LEDGER_DISPLAY");
                for(int d=0;d<6;d++)for(int edge=0;edge<2;edge++)
                {var display=displays[d*2+edge];double stamp=MenuJson.Number(display["mono_ms"]);MenuRules.Require((string)display["kind"]==(edge==0?"display_request":"display_changed")&&(string)display["phase"]==phases[d].ToString()&&stamp>=start+boundaries[d]&&stamp<=start+boundaries[d]+20&&(phases[d]==MenuPhase.Selected?MenuJson.Integer(display["selected_index"],1,3)==selected:display["selected_index"].Type==JTokenType.Null),"MENU_LEDGER_DISPLAY");}
                MenuRules.Require(ReferenceEquals(rows.Last(),displays.Last()),"MENU_LEDGER_SEQUENCE");entries.Add(new Entry{Key=key,Start=start,Replay=new MenuReplay(offsets,source,selected,defaulted,(string)choice["event_id"],(string)receipt["receipt_sha256"])});
            }
            return entries.ToArray();
        }
        static JObject One(IEnumerable<JObject> rows,string kind)
        {var matches=rows.Where(x=>(string)x["kind"]==kind).ToArray();MenuRules.Require(matches.Length==1,"MENU_LEDGER_EVENT_COUNT");return matches[0];}
    }
}

