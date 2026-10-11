using System;
using System.Collections.Generic;
using System.IO;
using AcousticVocab.Foundation;
using AcousticVocab.Assessment;
using AcousticVocab.ResponsePanel;
using AcousticVocab.SelectionMenus;
using AcousticVocab.SessionEngine;
using Newtonsoft.Json.Linq;
using UnityEngine;
namespace AcousticVocab.SessionIntegration
{
    // Deterministic dummy answers, never correct-answer lookup. The actual
    // panel/menu/rating action and all readiness/deadline checks remain in the
    // same native view callbacks. No clock is advanced or compressed.
    internal sealed class SimulationInputDriver
    {
        readonly SimulationTestAuthority authority;readonly ResponsePanelController panel;readonly Action<JObject> persist;
        readonly HashSet<string> processed=new HashSet<string>(StringComparer.Ordinal);
        readonly string nonce;bool armed;double next;int responseIndex;
        internal SimulationInputDriver(SimulationTestAuthority authority,ResponsePanelController panel,string nonce,Action<JObject> persist,bool arm)
        {this.authority=authority;this.panel=panel;this.nonce=nonce;this.persist=persist;if(arm)Arm();}
        void Arm(){persist(new JObject{["origin"]="simulation_test",["kind"]="driver_armed",["session_nonce"]=nonce,["capability_sha256"]=authority.RawSha256,["policy"]="cycle_commit_dontknow_timeout_v1"});armed=true;}
        // The simulated action runs the real view callbacks. A rejection keeps its
        // own fault code; an exception from the view is journaled (type plus a
        // code-shaped message only) and reported as SIMULATION_INPUT_FAILED
        // instead of the joined owner's generic JOIN_RUNTIME_FAILED.
        bool Act(string kind,string id,Func<bool> action)
        {
            try{return action();}
            catch(SessionFault){throw;}
            catch(Exception error)
            {
                string message=error.Message;
                try{persist(new JObject{["origin"]="simulation_test",["kind"]="input_failure",["input_kind"]=kind,["attempt_id"]=id,["exception"]=error.GetType().Name+(message!=null&&System.Text.RegularExpressions.Regex.IsMatch(message,@"\A[A-Z][A-Z0-9_]{0,63}\z")?":"+message:"")});}catch{}
                throw new SessionFault("SIMULATION_INPUT_FAILED");
            }
        }
        internal void Tick(double now)
        {
            if(!armed)
            {
                string path=Path.Combine(authority.OutputDirectory,"arm-dummy-inputs.local.json");if(!File.Exists(path))return;
                var info=new FileInfo(path);if(info.Length>1024||(info.Attributes&FileAttributes.ReparsePoint)!=0)throw new SessionFault("SIMULATION_ARM_INVALID");
                var p=StationConfig.ParseStrict(File.ReadAllText(path));var expected=new JObject{["version"]=1,["scope"]="SIMULATION_TEST",["session_nonce"]=nonce,["capability_sha256"]=authority.RawSha256};
                if(!JToken.DeepEquals(p,expected))throw new SessionFault("SIMULATION_ARM_INVALID");Arm();
            }
            if(now<next||!panel.ReadyForTrial)return;
            var request=panel.State?.Request;
            if(request!=null&&!panel.State.Locked&&now>=request.OpensMonoMs+1000&&now<request.DeadlineMonoMs&&processed.Add("panel:"+request.TrialId))
            {
                int index=responseIndex++;string response=index%5==4?"timeout":index%5==3?"dont_know":"commit";
                persist(new JObject{["origin"]="simulation_test",["kind"]="panel",["attempt_id"]=request.TrialId,["response"]=response,["target"]="A",["action"]="ADD_ONE",["index"]=index});
                if(response!="timeout"&&!Act("panel",request.TrialId,()=>panel.SimulationResponse(authority,response,"A","ADD_ONE")))throw new SessionFault("SIMULATION_INPUT_REJECTED");next=now+250;return;
            }
            foreach(var menu in UnityEngine.Object.FindObjectsByType<MenuSessionHost>(FindObjectsSortMode.None))
                if(menu.SimulationChoiceOwner is string id&&processed.Add("menu:"+id))
                {persist(new JObject{["origin"]="simulation_test",["kind"]="menu",["attempt_id"]=id,["index"]=1});if(!Act("menu",id,()=>menu.SimulationChoose(authority,1)))throw new SessionFault("SIMULATION_MENU_REJECTED");next=now+1000;return;}
            foreach(var screen in UnityEngine.Object.FindObjectsByType<AssessmentScreen>(FindObjectsSortMode.None))
                if(screen.SimulationRatingId is string id&&processed.Add("rating:"+id))
                {persist(new JObject{["origin"]="simulation_test",["kind"]="rating",["item_id"]=id,["value_policy"]="scale_midpoint"});if(!Act("rating",id,()=>screen.SimulationRate(authority)))throw new SessionFault("SIMULATION_RATING_REJECTED");next=now+1500;return;}
        }
    }
}
