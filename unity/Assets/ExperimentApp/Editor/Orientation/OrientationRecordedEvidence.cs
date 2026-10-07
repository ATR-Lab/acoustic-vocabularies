using System;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation.Editor;
using AcousticVocab.StateSources;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.Orientation.Editor
{
    public static class OrientationRecordedEvidence
    {
        // Deliberate rejection of the retained failed #56 library; never changes qualification flags.
        public static void VerifyRejected()
        {
            string root=Environment.GetEnvironmentVariable("ORIENTATION_REJECT_EVIDENCE")??throw new ArgumentException("Actual evidence directory required");
            string output=Environment.GetEnvironmentVariable("ORIENTATION_REJECT_OUTPUT")??throw new ArgumentException("Fresh private report required");
            string prefix=Path.GetFullPath(Path.Combine(FoundationBuild.RepositoryRoot,".local"))+Path.DirectorySeparatorChar;
            if(!Path.GetFullPath(output).StartsWith(prefix,StringComparison.OrdinalIgnoreCase)||File.Exists(output))throw new ArgumentException("Fresh private report required");
            byte[] neutral=File.ReadAllBytes(Path.Combine(root,"reset-check/neutral_v1.json"));
            var snapshot=JObject.Parse(Encoding.UTF8.GetString(neutral));string directory=Path.Combine(root,"demo-check");
            byte[] indexBytes=File.ReadAllBytes(Path.Combine(directory,"index.private.json"));var index=JObject.Parse(Encoding.UTF8.GetString(indexBytes));
            if((bool)index["recording_complete"])throw new InvalidOperationException("This check specifically requires the retained failed recording library");
            string layout=File.ReadAllText(Path.Combine(FoundationBuild.RepositoryRoot,"apparatus/workcell_layout.json"));
            var config=JObject.FromObject(new {version=1,scene_sha256=(string)snapshot["scene_sha256"],layout_sha256=SceneRegistry.Hash(Encoding.UTF8.GetBytes(layout)),neutral_sha256=SceneRegistry.Hash(neutral),neutral_file="neutral_v1.json",interpolation_delay_s=2d/30,clock=new{drift_bound_ppm=(double?)null,max_echo_age_s=2,evidence_sha256=(string)null}});
            var names=((JArray)snapshot["state"]["robot"]["joint_names"]).Select(x=>(string)x);
            var registry=StateSourceConfiguration.Load(config.ToString()).Registry((string)index["station_id"],layout,names);
            string code=null;try {OrientationDemos.Load(directory,SceneRegistry.Hash(indexBytes),registry,neutral,true);}catch(OrientationFault e){code=e.Message;}
            if(code!="ORIENTATION_DEMO_UNQUALIFIED")throw new InvalidOperationException("Actual failed library did not fail at its qualification guard: "+code);
            File.WriteAllText(output,new JObject{["qualification"]="actual_failed_library_rejection_only",["index_sha256"]=SceneRegistry.Hash(indexBytes),["neutral_sha256"]=SceneRegistry.Hash(neutral),["recording_complete"]=false,["rejection_code"]=code,["passed"]=true,["frames_applied"]=0,["human_eligibility_evidence"]=false}.ToString()+"\n");
            Debug.Log("ORIENTATION_ACTUAL_FAILED_LIBRARY_REJECTED "+code);
        }
    }
}
