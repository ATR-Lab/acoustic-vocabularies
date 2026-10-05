using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Assessment;
using AcousticVocab.Foundation;
using AcousticVocab.SelectionMenus;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using AcousticVocab.Teaching;
using Newtonsoft.Json.Linq;
namespace AcousticVocab.SessionIntegration
{
    public enum JoinedModuleKind { Menus,Teaching,Assessment }
    public static class JoinedBlockMap
    {
        public static IReadOnlyDictionary<string,JoinedModuleKind> Build(VisitSchedule schedule)
        {
            if(schedule==null)throw new SessionFault("JOIN_SCHEDULE_MISSING");var result=new Dictionary<string,JoinedModuleKind>(StringComparer.Ordinal);
            foreach(var block in schedule.Blocks)
            {
                var kinds=block.Items.Select(i=>i.TrialType is "profile_menu" or "atom_menu"?JoinedModuleKind.Menus:
                    i.TrialType is "atomic_lesson" or "message_lesson"?JoinedModuleKind.Teaching:
                    i.TrialType is "atomic" or "trained" or "pre_old" or "novel" or "speech" or "no_cue"?JoinedModuleKind.Assessment:throw new SessionFault("JOIN_SLOT_UNSUPPORTED")).Distinct().ToArray();
                if(kinds.Length!=1||!result.TryAdd(block.Name,kinds[0]))throw new SessionFault("JOIN_BLOCK_MIXED");
            }
            return new ReadOnlyDictionary<string,JoinedModuleKind>(result);
        }
    }
    public sealed class JoinedVisitArtifacts
    {
        public JoinedEngineeringConfig Config{get;}public LoadedAudioPackage Package{get;}public VisitSchedule Schedule{get;}
        public IReadOnlyDictionary<string,JoinedModuleKind> Blocks{get;}public TeachingCatalog Teaching{get;private set;}public MenuCatalog Menus{get;private set;}
        public AssessmentScripts Scripts{get;private set;}public SpeechBank Speech{get;private set;}public AudioRouteCalibration Route{get;private set;}public float Gain{get;private set;}
        public bool RatingsReviewed{get;private set;}public SimulationTestAuthority Simulation{get;}
        public GrammarAssets Grammar{get;private set;}public JoinedGrammarReview GrammarReview{get;private set;}
        public VisitSchedule YokedActiveSchedule{get;private set;}
        public MenuLedgerBinding YokedActiveBinding{get;private set;}
        public string MissingAuthority{get;private set;}readonly byte[] permutation;public byte[] Permutation=>(byte[])permutation.Clone();
        public static JObject Json(byte[] bytes)=>StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(bytes));
        byte[] Read(string name)=>Config.RequireFile(name).ReadVerified();
        bool Have(params string[] names){foreach(string n in names)if(!Config.TryFile(n,out _)){MissingAuthority="JOIN_"+n.ToUpperInvariant()+"_MISSING";return false;}return true;}
        static bool ValidateRatingReview(byte[] bytes,VisitSchedule schedule,string methodology,SimulationTestAuthority simulation=null)
        {
            var doc=Json(bytes);string[] keys={"version","approved","methodology_sha256","items"};
            if(simulation==null&&(!doc.Properties().Select(p=>p.Name).OrderBy(x=>x).SequenceEqual(keys.OrderBy(x=>x))||doc["version"].Type!=JTokenType.Integer||(int)doc["version"]!=1||doc["approved"].Type!=JTokenType.Boolean||!(bool)doc["approved"]||(string)doc["methodology_sha256"]!=methodology))throw new SessionFault("JOIN_RATING_REVIEW");
            var expected=new JArray(RatingPlan.For(schedule.Study,schedule.Visit).Select(x=>new JObject{["id"]=x.Id,["question"]=x.Question,["low_label"]=x.LowLabel,["high_label"]=x.HighLabel,["minimum"]=x.Minimum,["maximum"]=x.Maximum}));
            if(simulation!=null){simulation.Attest(doc,"rating",new JObject{["items"]=expected});return true;}
            if(!JToken.DeepEquals(doc["items"],expected))throw new SessionFault("JOIN_RATING_REVIEW");return true;
        }
        public JoinedVisitArtifacts(JoinedEngineeringConfig config,SimulationTestAuthority simulation=null)
        {
            Simulation=simulation;Config=config??throw new ArgumentNullException(nameof(config));Package=PackageLoader.Load(config.Directory("package"),config.Pin("package_sha256"),true);
            byte[] manifest=Read("package_manifest");permutation=Read("permutation");
            if(PcmWave.Hash(File.ReadAllBytes(Path.Combine(config.Directory("package"),"manifest.json")))!=config.RequireFile("package_manifest").Sha256)throw new SessionFault("JOIN_PACKAGE_MANIFEST_PIN");
            var run=new RunSheetEvidence(Read("run_sheet_manifest"),Read("schedule_manifest"),Read("run_sheet_csv"),config.RequireFile("run_sheet_manifest").Sha256,Read("run_sheet_schema"));
            Schedule=ScheduleLoader.Load(Read("schedule"),Permutation,manifest,Package,run,Read("schedule_schema"),Read("permutation_schema"),true);
            if(!Schedule.Demo||!Package.Demo||Schedule.Visit!=config.VisitId||Schedule.PersonSlot!=config.CodedId||(string)Json(Permutation)["unit_id"]!=config.UnitId||(string)Json(Read("station"))["station_id"]!=config.StationId||(string)Json(Read("station"))["protocol_version"]!=config.ProtocolVersion)throw new SessionFault("JOIN_ENGINEERING_SCOPE");Blocks=JoinedBlockMap.Build(Schedule);
            // Missing provisioned authority is an explicit blocked state. No
            // placeholder route offset/review/gain is promoted by DEMO scope.
            if(simulation!=null){simulation.Bind(Package.PackageSha256,Schedule.Sha256,Package.Demo,Schedule.Demo,config.Directory("evidence"));Route=AudioRouteCalibration.ForSimulation(simulation);Gain=simulation.AudioGain;}
            else{if(!Have("audio_calibration","comfort_gain"))return;
            Route=AudioRouteCalibration.FromStationConfig(Json(Read("station")),Read("audio_calibration"));if(Route.UncertaintyMs>20)throw new SessionFault("JOIN_ROUTE_UNCERTAINTY");
            var gain=config.RequireFile("comfort_gain");gain.ReadVerified();string expected=PcmWave.Hash(Encoding.ASCII.GetBytes(config.CodedId))+".local.jsonl";
            if(Path.GetFileName(gain.Path)!=expected)throw new SessionFault("JOIN_GAIN_BINDING");Gain=ComfortableGainStore.RestoreVerified(gain.ReadVerified(),gain.Sha256,config.CodedId);}
            if(Blocks.Values.Any(k=>k is JoinedModuleKind.Teaching or JoinedModuleKind.Menus))
            {
                if(!Have("teaching_manifest","teaching_review"))return;
                Teaching=TeachingCatalog.Load(config.Directory("teaching"),config.RequireFile("teaching_manifest").Sha256,config.RequireFile("teaching_review").Sha256,Package,Schedule,manifest,Permutation,
                    config.TryFile("teaching_allocation",out var allocation)?allocation.ReadVerified():null,allocation?.Sha256,simulation);
            }
            if(Blocks.Values.Contains(JoinedModuleKind.Teaching))
            {
                if(!Have("grammar_review","reserved_registry"))return;
                if(config.Directory("grammar")==null){MissingAuthority="JOIN_GRAMMAR_DIRECTORY_MISSING";return;}
                Grammar=GrammarAssets.Load(config.Directory("grammar"),Read("reserved_registry"),config.RequireFile("reserved_registry").Sha256);
                GrammarReview=JoinedGrammarReview.Load(Read("grammar_review"),config.RequireFile("grammar_review").Sha256,Grammar.RegistrySha256,Teaching.MethodologySha256,simulation);
            }
            if(Blocks.Values.Contains(JoinedModuleKind.Menus))
            {
                if(!Have("menu_script","menu_review","menu_allocation","reserved_registry","menu_snapshot","menu_bridge_config"))return;
                Menus=MenuCatalog.Load(config.Directory("menu_scripts"),config.RequireFile("menu_script").Sha256,config.RequireFile("menu_review").Sha256,Package,Schedule,Teaching,manifest,Permutation,
                    Read("menu_allocation"),config.RequireFile("menu_allocation").Sha256,config.Pin("bank_sha256"),config.Directory("menu_examples"),Read("reserved_registry"),config.RequireFile("reserved_registry").Sha256,true,simulation);
                if(Menus.Role=="yoked")
                {
                    if(!Have("menu_replay_ledger","yoked_active_schedule","yoked_active_run_sheet_manifest","yoked_active_schedule_manifest","yoked_active_run_sheet_csv"))return;
                    if(!config.YokedAnchorLeadMs.HasValue){MissingAuthority="JOIN_YOKED_ANCHOR_POLICY_MISSING";return;}
                    var activeRun=new RunSheetEvidence(Read("yoked_active_run_sheet_manifest"),Read("yoked_active_schedule_manifest"),Read("yoked_active_run_sheet_csv"),config.RequireFile("yoked_active_run_sheet_manifest").Sha256,Read("run_sheet_schema"));
                    YokedActiveSchedule=ScheduleLoader.Load(Read("yoked_active_schedule"),Permutation,manifest,Package,activeRun,Read("schedule_schema"),Read("permutation_schema"),true);
                    ValidateYokedPair(Schedule,YokedActiveSchedule,Read("menu_allocation"),Menus.MenuKeys);
                    string unit=JoinedSelections.SharedUnitBindingSha256(Package.PackageSha256,config.RequireFile("permutation").Sha256,config.Pin("bank_sha256"),config.UnitId);
                    YokedActiveBinding=new MenuLedgerBinding(Menus.PackageSha256,Menus.BankSha256,Menus.AllocationSha256,YokedActiveSchedule.Sha256,unit,Menus.ReviewSha256,Menus.Visit,"active",Menus.MenuKeys);
                }
            }
            if(Package.Study=="B"&&!Have("menu_snapshot","menu_bridge_config"))return;
            if(Blocks.Values.Contains(JoinedModuleKind.Assessment))
            {
                if(!Have("assessment_script","assessment_review","rating_review"))return;
                Scripts=AssessmentScripts.Load(Read("assessment_script"),config.RequireFile("assessment_script").Sha256,Read("assessment_review"),config.RequireFile("assessment_review").Sha256,simulation);
                if(Teaching!=null&&Teaching.MethodologySha256!=Scripts.MethodologySha256)throw new SessionFault("JOIN_METHODOLOGY_MISMATCH");
                RatingsReviewed=ValidateRatingReview(Read("rating_review"),Schedule,Scripts.MethodologySha256,simulation);
                if((!Schedule.Demo||simulation!=null)&&Schedule.Blocks.SelectMany(b=>b.Items).Any(i=>i.TrialType=="speech"))
                {if(!Have("speech_manifest","speech_review"))return;Speech=simulation!=null?SpeechBank.LoadSimulation(config.Directory("speech"),config.RequireFile("speech_manifest").Sha256,Read("speech_review"),config.RequireFile("speech_review").Sha256,Schedule.SpeechListSha256,simulation):SpeechBank.LoadReviewed(config.Directory("speech"),config.RequireFile("speech_manifest").Sha256,config.RequireFile("speech_review").Sha256,Schedule.SpeechListSha256);}
            }
        }
        public static void ValidateYokedPair(VisitSchedule yoked,VisitSchedule active,byte[] pinnedAllocation,IReadOnlyList<string> expectedMenuKeys)
        {
            if(yoked==null||active==null||!active.Demo||!yoked.Demo||active.Study!="B"||yoked.Study!="B"||active.PackageSha256!=yoked.PackageSha256||active.Visit!=yoked.Visit||active.PersonSlot==yoked.PersonSlot)throw new SessionFault("JOIN_YOKED_PAIR");
            var allocation=Json(pinnedAllocation);if(allocation["demo"]?.Type!=JTokenType.Boolean||!(bool)allocation["demo"]||allocation["dyads"] is not JArray)throw new SessionFault("JOIN_YOKED_PAIR");
            var dyads=allocation["dyads"].Where(d=>d["members"] is JArray&&d["members"].Any(m=>(string)m["slot_id"]==yoked.PersonSlot)).ToArray();
            if(dyads==null||dyads.Length!=1||dyads[0]["members"].Count(m=>(string)m["slot_id"]==yoked.PersonSlot&&(string)m["role"]=="yoked")!=1||dyads[0]["members"].Count(m=>(string)m["slot_id"]==active.PersonSlot&&(string)m["role"]=="active")!=1)throw new SessionFault("JOIN_YOKED_PAIR");
            var keys=active.Blocks.SelectMany(b=>b.Items).Where(i=>i.Phase=="selection").Select(i=>i.TrialType=="profile_menu"?"profile":i.ContentId);
            if(expectedMenuKeys==null||!keys.SequenceEqual(expectedMenuKeys))throw new SessionFault("JOIN_YOKED_PAIR");
        }
    }
}
