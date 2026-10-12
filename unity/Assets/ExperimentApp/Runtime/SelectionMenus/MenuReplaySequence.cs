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
    public sealed class MenuReplayComparison
    {
        public string ActiveLedgerSha256{get;}public string YokedEventSha256{get;}public int Menus{get;}public int AudioPlays{get;}public int ProfilePlays{get;}public int AtomPlays=>AudioPlays-ProfilePlays;
        internal MenuReplayComparison(string active,string yoked,int menus,int plays,int profiles){ActiveLedgerSha256=active;YokedEventSha256=yoked;Menus=menus;AudioPlays=plays;ProfilePlays=profiles;}
    }
    public sealed class MenuReplaySequence : ISlotStartPlan
    {
        internal sealed class Entry
        {internal string Key;internal double Start;internal MenuReplay Replay;internal double Anchor;internal int SegmentFirst;internal bool Planned;}
        Entry[] entries;readonly MenuLedgerBinding activeBinding;readonly Func<double> clock;
        public string LedgerSha256{get;}
        public DateTimeOffset CreatedUtc{get;private set;}
        public double SourceSpanMs=>entries.Last().Start+(entries.Last().Key=="profile"?60000:45000)-entries[0].Start;
        MenuReplaySequence(Entry[] entries,string hash,DateTimeOffset created,double anchor,Func<double> clock,MenuLedgerBinding binding)
        {activeBinding=binding;this.entries=entries;LedgerSha256=hash;CreatedUtc=created;this.clock=clock;foreach(var e in entries){e.Anchor=anchor;e.SegmentFirst=0;e.Planned=false;}}
        static string Key(SlotItem item)=>item.TrialType=="profile_menu"?"profile":item.ContentId;
        public MenuReplay For(SlotItem item)
        {MenuRules.Require(item!=null&&item.Phase=="selection","MENU_REPLAY_SLOT");return Find(Key(item)).Replay;}
        Entry Find(string key){var found=entries.SingleOrDefault(x=>x.Key==key);MenuRules.Require(found!=null,"MENU_REPLAY_SLOT");return found;}
        public double MinimumGapBeforeMs(SlotItem item,double baseOnsetMonoMs)
        {
            MenuRules.Require(item!=null&&item.Phase=="selection"&&MenuRules.Finite(baseOnsetMonoMs)&&baseOnsetMonoMs>=0,"MENU_REPLAY_SLOT");
            var entry=Find(Key(item));MenuRules.Require(MenuRules.Finite(entry.Anchor),"MENU_REPLAY_UNANCHORED");
            double target=entry.Anchor+entry.Start-entries[entry.SegmentFirst].Start,now=clock();
            // No tolerance can move an old cue into the present. A reviewed
            // reconstruction supplies a new explicit anchor and its own record.
            MenuRules.Require(MenuRules.Finite(target)&&MenuRules.Finite(now)&&now>=0&&baseOnsetMonoMs<=target&&now<=target-150,"MENU_REPLAY_ANCHOR_MISSED");
            entry.Planned=true;return target-baseOnsetMonoMs;
        }
        // Maintainer decision (#245): each yoked menu block is anchored at its own
        // boundary resume plus the configured lead. The active run's spacing is
        // replayed exactly within the block; operator pauses between blocks are
        // not. Load's anchor (the yoked start) remains the first block's anchor.
        // A block is re-anchored only while none of its menus has been planned,
        // so a refused resume can be retried and no started menu ever moves.
        int[] BlockIndices(IReadOnlyList<string> keys)
        {
            MenuRules.Require(keys!=null&&keys.Count>0&&keys.Distinct().Count()==keys.Count,"MENU_REPLAY_BLOCK_ANCHOR");
            var indices=keys.Select(k=>Array.IndexOf(entries,Find(k))).OrderBy(i=>i).ToArray();
            MenuRules.Require(indices.Last()-indices[0]==indices.Length-1,"MENU_REPLAY_BLOCK_ANCHOR");return indices;
        }
        public bool BlockAnchorable(IReadOnlyList<string> keys)=>!BlockIndices(keys).Any(i=>entries[i].Planned);
        public bool AnchorBlock(IReadOnlyList<string> keys,double anchorMonoMs)
        {
            var indices=BlockIndices(keys);MenuRules.Require(MenuRules.Finite(anchorMonoMs)&&anchorMonoMs>=clock(),"MENU_REPLAY_BLOCK_ANCHOR");
            if(indices.Any(i=>entries[i].Planned))return false;
            foreach(int i in indices){entries[i].Anchor=anchorMonoMs;entries[i].SegmentFirst=indices[0];}
            return true;
        }
        // Maintainer decision (#247): the yoked start anchors an already fully
        // verified sequence. Preload does the whole ledger, asset and receipt
        // verification during the idle preflight and leaves every menu
        // unanchored (MENU_REPLAY_UNANCHORED) until AnchorStart.
        public static MenuReplaySequence Preload(string path,string expectedFileSha256,MenuLedgerBinding expectedActiveBinding,MenuLedgerVerification verification,DateTimeOffset nowUtc,Func<double> clock)
            =>LoadCore(path,expectedFileSha256,expectedActiveBinding,verification,nowUtc,double.NaN,clock);
        public bool Anchored=>entries.All(e=>MenuRules.Finite(e.Anchor));
        public void AnchorStart(double anchorMonoMs)
        {
            MenuRules.Require(MenuRules.Finite(anchorMonoMs)&&anchorMonoMs>=clock()&&entries.All(e=>!MenuRules.Finite(e.Anchor)&&!e.Planned),"MENU_REPLAY_START_ANCHOR");
            foreach(var e in entries){e.Anchor=anchorMonoMs;e.SegmentFirst=0;}
        }
        // A re-verification of the same pinned ledger replaces the verified
        // entries in place, so a factory already holding this sequence stays bound.
        public void AdoptVerified(MenuReplaySequence fresh)
        {
            MenuRules.Require(fresh!=null&&fresh!=this&&fresh.LedgerSha256==LedgerSha256&&JToken.DeepEquals(fresh.activeBinding.Json(),activeBinding.Json())&&!Anchored&&!fresh.Anchored&&entries.All(e=>!e.Planned)&&fresh.entries.Select(e=>e.Key).SequenceEqual(entries.Select(e=>e.Key)),"MENU_REPLAY_REVERIFY");
            entries=fresh.entries;CreatedUtc=fresh.CreatedUtc;
        }
        public static MenuReplaySequence Load(string path,string expectedFileSha256,MenuLedgerBinding expectedActiveBinding,MenuLedgerVerification verification,DateTimeOffset nowUtc,double sessionAnchorMonoMs,Func<double> clock)
        {
            MenuRules.Require(MenuRules.Finite(sessionAnchorMonoMs),"MENU_REPLAY_BINDING");
            return LoadCore(path,expectedFileSha256,expectedActiveBinding,verification,nowUtc,sessionAnchorMonoMs,clock);
        }
        static MenuReplaySequence LoadCore(string path,string expectedFileSha256,MenuLedgerBinding expectedActiveBinding,MenuLedgerVerification verification,DateTimeOffset nowUtc,double sessionAnchorMonoMs,Func<double> clock)
        {
            MenuRules.Require(MenuRules.Hash(expectedFileSha256)&&expectedActiveBinding!=null&&expectedActiveBinding.Role=="active"&&verification!=null&&nowUtc.Offset==TimeSpan.Zero&&(double.IsNaN(sessionAnchorMonoMs)||sessionAnchorMonoMs>=0)&&clock!=null,"MENU_REPLAY_BINDING");
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
            return new MenuReplaySequence(entries,expectedFileSha256,created,sessionAnchorMonoMs,clock,expectedActiveBinding);
        }
        internal static Entry[] ValidateRecords(IReadOnlyList<JObject> records,MenuLedgerBinding binding,MenuLedgerVerification verification,bool assigned=false,Entry[] replayPlan=null)
        {
            MenuRules.Require(binding!=null&&(assigned?binding.Role=="yoked":binding.Role=="active")&&verification!=null&&records.Count>0,"MENU_LEDGER_BINDING");
            var ids=new HashSet<string>(StringComparer.Ordinal);var audioIds=new HashSet<string>(StringComparer.Ordinal);double last=-1;var groups=new List<List<JObject>>();var attempts=new Dictionary<string,List<JObject>>(StringComparer.Ordinal);
            foreach(var row in records)
            {
                MenuJson.CheckEvent(row);double now=MenuJson.Number(row["mono_ms"]);MenuRules.Require(now>=last&&ids.Add((string)row["event_id"])&&row["yoked_source_event_id"].Type==JTokenType.Null,"MENU_LEDGER_SEQUENCE");last=now;
                string attemptId=(string)row["attempt_id"];
                if((string)row["kind"]=="menu_start") {MenuRules.Require(!attempts.ContainsKey(attemptId),"MENU_LEDGER_SEQUENCE");var group=new List<JObject>();groups.Add(group);attempts.Add(attemptId,group);}
                MenuRules.Require(groups.Count>0&&groups.Count<=binding.MenuKeys.Count&&attempts.TryGetValue(attemptId,out var selectedGroup),"MENU_LEDGER_SEQUENCE");attempts[attemptId].Add(row);
            }
            MenuRules.Require(groups.Count==binding.MenuKeys.Count,"MENU_LEDGER_INCOMPLETE");var entries=new List<Entry>();double previousEnd=-1;
            // Boundary events (display transitions, final choice) are frame-
            // quantized observations: active and yoked seals apply the bound
            // MenuTimeline enforces live, each against its own scheduled times.
            // Audio onsets below stay tight (exact plan, onset uncertainty).
            double observation=MenuRules.BoundaryObservationMs;
            for(int g=0;g<groups.Count;g++)
            {
                var rows=groups[g];var first=rows[0];string key=binding.MenuKeys[g],attempt=(string)first["attempt_id"],opportunity=(string)first["opportunity_id"];double start=MenuJson.Number(first["slot_start_mono_ms"]);bool profile=key=="profile";
                MenuRules.Require(start>=previousEnd&&rows.All(x=>(string)x["menu_key"]==key&&(string)x["meaning_display_id"]==verification.Meaning(key)&&(string)x["attempt_id"]==attempt&&(string)x["opportunity_id"]==opportunity&&MenuJson.Number(x["slot_start_mono_ms"])==start)&&MenuJson.Number(first["mono_ms"])<=start&&MenuJson.Number(first["expected_mono_ms"])==start&&!rows.Any(x=>(string)x["kind"]=="menu_interrupted"),"MENU_LEDGER_SEQUENCE");
                previousEnd=start+(profile?60000:45000);var options=verification.Options(key);
                MenuRules.Require(options!=null&&options.Length==3&&options.All(x=>x!=null)&&options.Select(x=>x.CandidateId).Distinct().Count()==3&&options.Select(x=>x.Wave.PcmSha256).Distinct().Count()==3&&options.All(x=>profile?x.Wave.SampleCount==96000:new[]{21600,28800,36000,43200}.Contains(x.Wave.SampleCount)),"MENU_LEDGER_ASSETS");
                double choiceOpen=start+(profile?30000:22000),deadline=start+(profile?45000:32000);
                var choice=One(rows,"choice_final");int selected=MenuJson.Integer(choice["selected_index"],1,3);MenuRules.Require(choice["defaulted"].Type==JTokenType.Boolean,"MENU_LEDGER_CHOICE");bool defaulted=(bool)choice["defaulted"];
                MenuRules.Require(MenuJson.Number(choice["expected_mono_ms"])==deadline&&MenuJson.Number(choice["mono_ms"])>=deadline&&MenuJson.Number(choice["mono_ms"])<=deadline+observation,"MENU_LEDGER_TIMING");
                var revisions=rows.Where(x=>(string)x["kind"]=="choice_revised").ToArray();foreach(var revision in revisions)MenuRules.Require(MenuJson.Number(revision["mono_ms"])>=choiceOpen&&MenuJson.Number(revision["mono_ms"])<deadline,"MENU_LEDGER_CHOICE");
                MenuRules.Require(assigned?revisions.Length==0:defaulted?revisions.Length==0&&selected==1:revisions.Length>0&&MenuJson.Integer(revisions.Last()["selected_index"],1,3)==selected,"MENU_LEDGER_CHOICE");
                var receipt=One(rows,"selection_verified");MenuRules.Require(MenuJson.Integer(receipt["selected_index"],1,3)==selected&&receipt["defaulted"].Type==JTokenType.Boolean&&(bool)receipt["defaulted"]==defaulted&&MenuJson.Number(receipt["mono_ms"])>=MenuJson.Number(choice["mono_ms"])&&MenuRules.Hash((string)receipt["receipt_sha256"])&&verification.Receipt(key,selected,(string)receipt["receipt_sha256"]),"MENU_LEDGER_RECEIPT");
                double[] nominal=replayPlan?[g].Replay.OffsetsMs??(profile?new[]{6500d,10500,14500,18500,22500,26500,50000,54000}:new[]{5000d,8000,11000,14000,17000,20000,35000,38000});var offsets=new double[8];var source=new string[8];double previousComplete=-1;
                for(int n=1;n<=8;n++)
                {
                    var play=rows.Where(x=>x["presentation_index"].Type!=JTokenType.Null&&(int)x["presentation_index"]==n).ToList();MenuRules.Require(play.Count==3,"MENU_LEDGER_PLAY_COUNT");
                    var request=One(play,"play_request");var onset=One(play,"onset_authority");var complete=One(play,"play_complete");var option=options[n<=6?(n-1)/2:selected-1];string audio=(string)request["audio_request_id"];
                    MenuRules.Require(audioIds.Add(audio)&&play.All(x=>(string)x["audio_request_id"]==audio&&(string)x["candidate_id"]==option.CandidateId&&(string)x["pcm_sha256"]==option.Wave.PcmSha256&&(string)x["file_sha256"]==option.Wave.FileSha256),"MENU_LEDGER_ASSETS");
                    double requested=MenuJson.Number(request["mono_ms"]),planned=MenuJson.Number(request["expected_mono_ms"]),actual=MenuJson.Number(onset["expected_mono_ms"]),observed=MenuJson.Number(onset["mono_ms"]),done=MenuJson.Number(complete["mono_ms"]);
                    MenuRules.Require(planned==start+nominal[n-1]&&requested>=previousComplete&&requested>=planned-750&&requested<=planned-150&&Math.Abs(actual-planned)<=MenuJson.Number(onset["onset_uncertainty_ms"])+.000001&&observed>=requested&&observed<=planned+250&&done>=observed&&done>=actual+option.Wave.SampleCount/48d-20&&done<=previousEnd&&(n<=6||MenuJson.Number(receipt["mono_ms"])<=requested),"MENU_LEDGER_TIMING");
                    offsets[n-1]=actual-start;source[n-1]=(string)request["event_id"];previousComplete=done;
                }
                var phases=new[]{MenuPhase.Instructions,MenuPhase.Audition,MenuPhase.Choice,MenuPhase.Selected,MenuPhase.Neutral,MenuPhase.Ended};double[] boundaries=MenuRules.DisplayOffsets(profile);
                var displays=rows.Where(x=>(string)x["kind"] is "display_request" or "display_changed").ToArray();MenuRules.Require(displays.Length==12,"MENU_LEDGER_DISPLAY");
                for(int d=0;d<6;d++)for(int edge=0;edge<2;edge++)
                {var display=displays[d*2+edge];double stamp=MenuJson.Number(display["mono_ms"]);MenuRules.Require((string)display["kind"]==(edge==0?"display_request":"display_changed")&&(string)display["phase"]==phases[d].ToString()&&stamp>=start+boundaries[d]&&stamp<=start+boundaries[d]+observation&&(phases[d]==MenuPhase.Selected?MenuJson.Integer(display["selected_index"],1,3)==selected:display["selected_index"].Type==JTokenType.Null),"MENU_LEDGER_DISPLAY");}
                MenuRules.Require(ReferenceEquals(rows.Last(),displays.Last()),"MENU_LEDGER_SEQUENCE");entries.Add(new Entry{Key=key,Start=start,Replay=new MenuReplay(offsets,source,selected,defaulted,(string)choice["event_id"],(string)receipt["receipt_sha256"],displays.Where((x,i)=>i%2==1).Select(x=>MenuJson.Number(x["mono_ms"])-start).ToArray(),displays.Where((x,i)=>i%2==0).Select(x=>(string)x["event_id"]).ToArray(),displays.Where((x,i)=>i%2==1).Select(x=>(string)x["event_id"]).ToArray(),(string)first["event_id"],(string)receipt["event_id"])});
            }
            return entries.ToArray();
        }
        public MenuReplayComparison CompareYoked(IReadOnlyList<MenuEvent> events,MenuLedgerBinding yokedBinding,MenuLedgerVerification verification)
        {MenuRules.Require(events!=null,"MENU_YOKED_INCOMPLETE");return CompareYokedRecords(events.Select(MenuJson.Event).ToList(),yokedBinding,verification);}
        internal MenuReplayComparison CompareYokedRecords(IReadOnlyList<JObject> rows,MenuLedgerBinding binding,MenuLedgerVerification verification)
        {
            MenuRules.Require(binding!=null&&binding.Role=="yoked"&&binding.MenuKeys.SequenceEqual(entries.Select(x=>x.Key))&&binding.PackageSha256==activeBinding.PackageSha256&&binding.BankSha256==activeBinding.BankSha256&&binding.AllocationSha256==activeBinding.AllocationSha256&&binding.UnitBindingSha256==activeBinding.UnitBindingSha256&&binding.ReviewSha256==activeBinding.ReviewSha256&&binding.Visit==activeBinding.Visit,"MENU_YOKED_BINDING");
            var normalized=new List<JObject>();var segmentStarts=new Dictionary<int,double>();
            foreach(var sourceRow in rows)
            {
                MenuJson.CheckEvent(sourceRow);var row=(JObject)sourceRow.DeepClone();var entry=Find((string)row["menu_key"]);var source=entry.Replay;double start=MenuJson.Number(row["slot_start_mono_ms"]);string kind=(string)row["kind"];
                // Relative spacing is exact within each anchored block; the gap
                // between blocks is the yoked run's own boundary resume.
                var first=entries[entry.SegmentFirst];
                if(kind=="menu_start"&&entry==first){MenuRules.Require(!segmentStarts.ContainsKey(entry.SegmentFirst),"MENU_YOKED_SEQUENCE");segmentStarts[entry.SegmentFirst]=start;}
                MenuRules.Require(segmentStarts.TryGetValue(entry.SegmentFirst,out double segmentStart)&&Math.Abs((start-segmentStart)-(entry.Start-first.Start))<.000001,"MENU_YOKED_PAUSE_MISMATCH");
                string expectedSource=null;
                if(kind=="menu_start")expectedSource=source.StartEventId;
                else if(kind is "play_request" or "onset_authority" or "play_complete")
                {
                    int n=MenuJson.Integer(row["presentation_index"],1,8)-1;expectedSource=source.SourceEvents[n];
                    if(kind=="play_request")
                    {
                        MenuRules.Require(Math.Abs(MenuJson.Number(row["expected_mono_ms"])-start-source.OffsetsMs[n])<.000001,"MENU_YOKED_TIMING");

                    }
                    if(kind=="onset_authority")MenuRules.Require(Math.Abs(MenuJson.Number(row["expected_mono_ms"])-start-source.OffsetsMs[n])<=MenuJson.Number(row["onset_uncertainty_ms"])+.000001,"MENU_YOKED_TIMING");
                }
                else if(kind is "display_request" or "display_changed")
                {
                    MenuRules.Require(Enum.TryParse((string)row["phase"],out MenuPhase phase)&&phase>=MenuPhase.Instructions&&phase<=MenuPhase.Ended,"MENU_YOKED_DISPLAY");int n=(int)phase-1;
                    expectedSource=kind=="display_request"?source.DisplayRequestEvents[n]:source.DisplayChangedEvents[n];
                    // Against the yoked run's own scheduled boundary, not the active
                    // run's observed frame time: two frame-late observations of the
                    // same deterministic schedule need not agree within 20 ms.
                    double scheduled=MenuRules.DisplayOffsets(entry.Key=="profile")[n],late=MenuJson.Number(row["mono_ms"])-start-scheduled;
                    MenuRules.Require(late>=0&&late<=MenuRules.BoundaryObservationMs,"MENU_YOKED_DISPLAY");
                }
                else if(kind is "choice_final" or "selection_verified")
                {expectedSource=kind=="choice_final"?source.SelectionEventId:source.VerificationEventId;MenuRules.Require(MenuJson.Integer(row["selected_index"],1,3)==source.SelectedIndex&&(bool)row["defaulted"]==source.Defaulted&&(kind!="selection_verified"||(string)row["receipt_sha256"]==source.SelectionReceiptSha256),"MENU_YOKED_CHOICE");}
                MenuRules.Require(expectedSource!=null&&(string)row["yoked_source_event_id"]==expectedSource,"MENU_YOKED_SOURCE_EVENT");row["yoked_source_event_id"]=JValue.CreateNull();normalized.Add(row);
            }
            ValidateRecords(normalized,binding,verification,true,entries);
            return new MenuReplayComparison(LedgerSha256,PcmWave.Hash(MenuJson.Bytes(new JArray(rows))),entries.Length,entries.Length*8,entries.Any(x=>x.Key=="profile")?8:0);
        }
        static JObject One(IEnumerable<JObject> rows,string kind)
        {var matches=rows.Where(x=>(string)x["kind"]==kind).ToArray();MenuRules.Require(matches.Length==1,"MENU_LEDGER_EVENT_COUNT");return matches[0];}
    }
}

