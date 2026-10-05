using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json.Linq;
using AcousticVocab.StudyAudio;

namespace AcousticVocab.SessionEngine
{
    // The run-sheet CSV keeps its producer/template columns. Its manifest binds
    // the schedule manifest; the latter binds the actual private schedule bytes.
    public sealed class RunSheetEvidence
    {
        readonly JObject manifest,schedules;
        readonly byte[] sheet;
        public string ManifestSha256 { get; }
        public RunSheetEvidence(byte[] runSheetManifest,byte[] scheduleManifest,byte[] runSheetCsv,string expectedManifestSha256)
        {
            SessionJson.Require(SessionJson.Hash(expectedManifestSha256)&&PcmWave.Hash(runSheetManifest)==expectedManifestSha256,"RUN_SHEET_MANIFEST_HASH");
            manifest=SessionJson.Parse(runSheetManifest);schedules=SessionJson.Parse(scheduleManifest);sheet=(byte[])runSheetCsv.Clone();ManifestSha256=expectedManifestSha256;
            SessionJson.Require((string)manifest["format"]=="av-schedules/run-sheets-manifest" && (int?)manifest["format_version"]==1 &&
                (string)schedules["format"]=="av-schedules/schedules-manifest" && (int?)schedules["format_version"]==1 &&
                (string)manifest["schedules_manifest_sha256"]==PcmWave.Hash(scheduleManifest) && (int?)manifest["checks"]?["findings"]==0,"RUN_SHEET_CHAIN_INVALID");
            SessionJson.Require(sheet.Length>0&&sheet.Length<=65536);
        }
        internal void Verify(JObject visit,string scheduleHash,string packageHash,ScheduleBlock[] blocks)
        {
            string unit=(string)visit["unit_id"],person=(string)visit["person_id"],name=(string)visit["visit"];
            foreach(string key in new[]{"study","set","demo","seed_label"}) SessionJson.Require(JToken.DeepEquals(visit[key],manifest[key])&&JToken.DeepEquals(visit[key],schedules[key]),"RUN_SHEET_IDENTITY");
            SessionJson.Require((string)schedules["files"]?[unit+"/schedules/"+person+"/"+name+".json"]==scheduleHash,"RUN_SHEET_SCHEDULE_HASH");
            SessionJson.Require((string)manifest["files"]?[unit+"/run-sheets/"+person+"/"+name+".csv"]==PcmWave.Hash(sheet),"RUN_SHEET_FILE_HASH");
            var rows=Csv(new UTF8Encoding(false,true).GetString(sheet));
            string[] header={"participant_id","visit","block","expected_count","actual_count","start_time","end_time","comfort_check","phone_locked","hash_check","deviations","operator_signoff"};
            SessionJson.Require(rows.Count==blocks.Length+1 && rows[0].SequenceEqual(header),"RUN_SHEET_COLUMNS");
            for(int i=0;i<blocks.Length;i++)
            {
                var row=rows[i+1];SessionJson.Require(row.Length==header.Length && row[0]==person&&row[1]==name&&row[2]==blocks[i].Name&&
                    row[3]==blocks[i].Items.Count.ToString(System.Globalization.CultureInfo.InvariantCulture)&&row[9]=="sha256:"+packageHash,"RUN_SHEET_ROW_MISMATCH");
            }
        }
        static List<string[]> Csv(string text)
        {
            var rows=new List<string[]>();var row=new List<string>();var field=new StringBuilder();bool quoted=false,closed=false;
            for(int i=0;i<text.Length;i++)
            {
                char c=text[i];if(quoted){if(c=='"'){if(i+1<text.Length&&text[i+1]=='"'){field.Append('"');i++;}else{quoted=false;closed=true;}}else field.Append(c);continue;}
                if(c=='"'){SessionJson.Require(field.Length==0&&!closed);quoted=true;continue;}
                if(c==','||c=='\r'||c=='\n')
                {
                    row.Add(field.ToString());field.Clear();closed=false;
                    if(c!=','){if(c=='\r'){SessionJson.Require(i+1<text.Length&&text[++i]=='\n');}rows.Add(row.ToArray());row.Clear();}continue;
                }
                SessionJson.Require(!closed);field.Append(c);
            }
            SessionJson.Require(!quoted&&field.Length==0&&row.Count==0,"RUN_SHEET_UNTERMINATED");return rows;
        }
    }
    public static class ScheduleLoader
    {
        public const string ScheduleSchemaSha256="e2ef481ff27d0f7a20563cf552fa194ce7d310c15e2e16aa6b0428fcd2d89159";
        public const string PermutationSchemaSha256="6ec3fadba40716ddd0c4930c08a71b82bfa40d8a09f1927643ab2375086062bd";
        static readonly string[,] matrix={{"1","H-V1","2","H-W1"},{"H-W4","1","H-V2","3"},{"2","H-W1","2","3"},{"H-V3","3","3","H-W4"}};
        static readonly string[] families={"K","Q"};
        static string[] Messages(Func<string,bool> predicate)=>families.SelectMany(f=>Enumerable.Range(1,4).SelectMany(a=>Enumerable.Range(1,4).Select(r=>f+"-a"+a+"-r"+r))).Where(predicate).ToArray();
        static string Cell(string id)=>matrix[id[3]-'1',id[6]-'1'];
        static bool Trained(string id)=>Cell(id).Length==1;
        static string[] TrainedUpTo(int wave)=>Messages(id=>Trained(id)&&int.Parse(Cell(id))<=wave);
        static string[] Atoms(int wave)=>families.SelectMany(f=>new[]{"a","r"}.SelectMany(role=>Enumerable.Range(1,4).Where(i=>(i<3?1:i==3?2:3)<=wave).Select(i=>f+"-"+role+i))).ToArray();
        static readonly Dictionary<string,(string type,int slot,int plays,string phase,string code)> kinds=new Dictionary<string,(string,int,int,string,string)>{
            ["profile_menu"]=("profile_menu",60,8,"selection","PM"),["atom_menus"]=("atom_menu",45,8,"selection","AM"),
            ["atomic_lessons"]=("atomic_lesson",20,3,"teaching","AL"),["message_lessons"]=("message_lesson",24,3,"teaching","ML"),
            ["pre_old"]=("pre_old",14,1,"pre_test","PO"),["trained"]=("trained",14,1,"protected","TR"),
            ["novel"]=("novel",14,1,"protected","NV"),["atomic"]=("atomic",9,1,"protected","AT"),["validity"]=("validity",14,1,"validity","VA")};
        public static VisitSchedule Load(byte[] scheduleBytes,byte[] permutationBytes,byte[] packageManifest,LoadedAudioPackage package,
            RunSheetEvidence evidence,byte[] scheduleSchema,byte[] permutationSchema,bool allowDemo=false)
        {
            try{return Checked(scheduleBytes,permutationBytes,packageManifest,package,evidence,scheduleSchema,permutationSchema,allowDemo);}
            catch(SessionFault){throw;}catch{throw new SessionFault("SESSION_SCHEDULE_INVALID");}
        }
        static VisitSchedule Checked(byte[] bytes,byte[] permutationBytes,byte[] packageBytes,LoadedAudioPackage package,RunSheetEvidence evidence,byte[] scheduleSchema,byte[] permutationSchema,bool allowDemo)
        {
            SessionJson.Require(package!=null&&evidence!=null);var doc=SessionJson.Parse(bytes);var permutation=SessionJson.Parse(permutationBytes);
            new ScheduleSchema(scheduleSchema,ScheduleSchemaSha256).Validate(doc);new ScheduleSchema(permutationSchema,PermutationSchemaSha256).Validate(permutation);
            string hash=PcmWave.Hash(bytes);var manifest=SessionJson.Parse(packageBytes);
            SessionJson.Require(PackageLoader.CanonicalPackageHash(new UTF8Encoding(false,true).GetString(packageBytes))==package.PackageSha256,"SESSION_PACKAGE_HASH");
            string person=(string)doc["person_id"],visit=(string)doc["visit"],study=(string)doc["study"];
            SessionJson.Require((bool)doc["demo"]==package.Demo&&(!(bool)doc["demo"]||allowDemo),"SESSION_DEMO_REFUSED");
            foreach(string key in new[]{"study","set","unit_id","unit_kind","demo","seed_label","family_first"})SessionJson.Require(JToken.DeepEquals(doc[key],permutation[key]));
            SessionJson.Require((string)doc["permutation_json_sha256"]==PcmWave.Hash(permutationBytes));
            string schedulePath="schedules/"+person+"/"+visit+".json";
            var files=(JObject)manifest["files"];
            SessionJson.Require((string)files[schedulePath]?["sha256"]==hash &&
                (string)files["permutation.json"]?["sha256"]==PcmWave.Hash(permutationBytes),"SESSION_PACKAGE_SCHEDULE_BINDING");
            int wave=study=="A"||visit=="V3"||visit=="W1"||visit=="W4"?3:visit=="V2"?2:1;
            SessionJson.Require((int)doc["wave"]==wave&&((study=="A"&&new[]{"D0","D7"}.Contains(visit))||(study=="B"&&new[]{"V1","V2","V3","W1","W4"}.Contains(visit))));
            var atomMap=((JArray)permutation["atoms"]).ToDictionary(x=>(string)x["atom_id"],x=>x);
            var messageMap=((JArray)permutation["messages"]).ToDictionary(x=>(string)x["message_id"],x=>x);
            SessionJson.Require(messageMap.Count==32&&atomMap.Count==16);
            foreach(string id in Messages(_=>true))SessionJson.Require(messageMap.ContainsKey(id)&&(string)messageMap[id]["status"]==(Trained(id)?"trained":"heldout"));
            SessionJson.Require(((JArray)doc["dictionary_messages"]).Select(x=>(string)x).OrderBy(x=>x).SequenceEqual(TrainedUpTo(wave).OrderBy(x=>x)),"SESSION_HELDOUT_DICTIONARY");
            bool teaching=study=="A"?visit=="D0":visit.StartsWith("V",StringComparison.Ordinal);
            var expected=new List<(string name,int count,int passes)>();
            if(study=="B"&&teaching&&wave>1)expected.Add(("pre_old",TrainedUpTo(wave-1).Length,1));
            if(study=="B"&&visit=="V1")expected.Add(("profile_menu",1,1));
            int newAtoms=study=="A"?16:Atoms(wave).Length-Atoms(wave-1).Length;
            int newMessages=study=="A"?18:TrainedUpTo(wave).Length-TrainedUpTo(wave-1).Length;
            if(teaching){if(study=="B")expected.Add(("atom_menus",newAtoms,1));expected.Add(("atomic_lessons",newAtoms,1));expected.Add(("message_lessons",newMessages*2,2));}
            expected.Add(("trained",TrainedUpTo(wave).Length*(teaching&&study=="B"?1:2),teaching&&study=="B"?1:2));
            expected.Add(("novel",study=="B"&&teaching?2:4,1));expected.Add(("atomic",Atoms(wave).Length,1));
            if(visit=="D7"||visit=="W4")expected.Add(("validity",16,1));
            var blocks=(JArray)doc["blocks"];SessionJson.Require(blocks.Count==expected.Count,"SESSION_BLOCK_ORDER");
            var output=new List<ScheduleBlock>();var ids=new HashSet<string>();
            for(int n=0;n<blocks.Count;n++)
            {
                var block=blocks[n];var plan=expected[n];var kind=kinds[plan.name];
                SessionJson.Require((string)block["block"]==plan.name&&(int)block["position"]==n+1&&(string)block["phase"]==kind.phase&&
                    (int)block["expected_count"]==plan.count&&(int)block["passes"]==plan.passes&&(int)block["slot_s"]==kind.slot&&(int)block["seconds"]==plan.count*kind.slot,"SESSION_BLOCK_PLAN");
                var items=(JArray)block["items"];SessionJson.Require(items.Count==plan.count);var records=new List<SlotItem>();
                for(int i=0;i<items.Count;i++)
                {
                    var item=items[i];string type=(string)item["trial_type"],id=(string)item["trial_id"],message=(string)item["message_id"],atom=(string)item["atom_id"],speech=(string)item["speech_id"];
                    int pass=i/(plan.count/plan.passes)+1;
                    SessionJson.Require(ids.Add(id)&&id==person+"-"+visit+"-"+kind.code+"-"+(i+1).ToString("D2")&&(int)item["position"]==i+1&&(int)item["pass"]==pass&&
                        (int)item["slot_s"]==kind.slot&&(int)item["plays"]==(type=="no_cue"?0:kind.plays)&&
                        (plan.name=="validity"?new[]{"no_cue","speech"}.Contains(type):type==kind.type),"SESSION_ITEM_PLAN");
                    string status=type=="profile_menu"?"nonsemantic":type=="novel"?"heldout":type=="no_cue"||type=="speech"?"validity":atom!=null?"atom":"trained";
                    SessionJson.Require((string)item["trained_status"]==status,"SESSION_HELDOUT_LESSON");
                    string role=null;var intended=item["intended"];
                    if(type=="profile_menu")SessionJson.Require(message==null&&atom==null&&speech==null&&intended.Type==JTokenType.Null);
                    else if(atom!=null)
                    {
                        SessionJson.Require(message==null&&speech==null&&atomMap.ContainsKey(atom)&&Atoms(wave).Contains(atom));var a=atomMap[atom];
                        SessionJson.Require((string)intended["kind"]=="atom"&&(string)intended["atom_id"]==atom&&
                            (string)intended["semantic_label"]==(string)a["semantic_label"]&&(string)intended["role"]==(string)a["role"]&&
                            (int)intended["index"]==(int)a["index"]&&(string)intended["family"]==(string)a["family"]);role=(string)a["role"];
                    }
                    else
                    {
                        string meaning=(string)intended["message_id"];SessionJson.Require(messageMap.ContainsKey(meaning));var m=messageMap[meaning];
                        SessionJson.Require((string)intended["kind"]=="message"&&(string)intended["semantic_action"]==(string)m["semantic_action"]&&
                            (string)intended["semantic_referent"]==(string)m["semantic_referent"]&&(string)intended["family"]==meaning.Substring(0,1)&&
                            (int)intended["action_index"]==meaning[3]-'0'&&(int)intended["referent_index"]==meaning[6]-'0');
                        if(type=="no_cue"||type=="speech")SessionJson.Require(message==null&&atom==null&&(type=="no_cue"?speech==null:speech==meaning.Substring(0,1)+"-"+(string)m["semantic_action"]+"-"+(string)m["semantic_referent"]));
                        else SessionJson.Require(message==meaning&&speech==null&&(Trained(message)==(status=="trained")),"SESSION_HELDOUT_LESSON");
                    }
                    string presentation=(string)item["presentation"];SessionJson.Require(type=="message_lesson"?presentation=="structured"||study=="B"&&presentation=="dictionary":presentation==null);
                    records.Add(new SlotItem(id,type,message??atom??speech,role,presentation,kind.phase,status=="heldout",kind.slot,(int)item["plays"],pass));
                }
                CheckPool(plan.name,records,study,visit,wave,(bool)doc["swap_w1_w4"],permutation);
                output.Add(new ScheduleBlock(plan.name,records.ToArray()));
            }
            var result=output.ToArray();evidence.Verify(doc,hash,package.PackageSha256,result);
            return new VisitSchedule(hash,package.PackageSha256,person,visit,(bool)doc["demo"],result);
        }
        static void CheckPool(string block,List<SlotItem> rows,string study,string visit,int wave,bool swap,JObject permutation)
        {
            string[] pool=null;
            if(block=="trained")pool=TrainedUpTo(wave);
            if(block=="pre_old")pool=TrainedUpTo(wave-1);
            if(block=="message_lessons")pool=study=="A"?TrainedUpTo(3):TrainedUpTo(wave).Except(TrainedUpTo(wave-1)).ToArray();
            if(block=="atomic")pool=Atoms(wave);
            if(block=="atomic_lessons"||block=="atom_menus")
            {
                pool=study=="A"?Atoms(3):Atoms(wave).Except(Atoms(wave-1)).ToArray();
                var order=(JArray)(study=="A"?permutation["atom_order"]:permutation["wave_atom_order"][wave.ToString()]);
                SessionJson.Require(rows.Select(x=>x.ContentId).SequenceEqual(order.Select(x=>(string)x)),"SESSION_STORED_ORDER");
            }
            if(block=="novel")
            {
                string selected=study=="A"?(visit=="D0"?"W1":"W4"):visit;if(swap&&selected=="W1")selected="W4";else if(swap&&selected=="W4")selected="W1";
                pool=Messages(id=>Cell(id)=="H-"+selected);
            }
            if(pool!=null)foreach(var pass in rows.GroupBy(x=>x.Pass))SessionJson.Require(pass.Select(x=>x.ContentId).OrderBy(x=>x).SequenceEqual(pool.OrderBy(x=>x)),"SESSION_ONCE_PER_PASS");
            if(block=="validity")SessionJson.Require(rows.Count(x=>x.TrialType=="no_cue")==8&&rows.Count(x=>x.TrialType=="speech")==8&&rows.Where(x=>x.TrialType=="speech").Select(x=>x.ContentId).Distinct().Count()==8);
        }
    }
}
