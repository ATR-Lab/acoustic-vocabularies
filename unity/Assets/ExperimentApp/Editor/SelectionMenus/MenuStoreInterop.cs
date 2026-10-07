using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;
using UnityEditor;
namespace AcousticVocab.SelectionMenus.Editor
{
    // Explicit operator-side DEMO diagnostic only. No participant scene opens,
    // no audio schedules, and no recipes are imported into the Unity project.
    public static class MenuStoreInterop
    {
        public static void Run()
        {
            string root=Environment.GetEnvironmentVariable("AV_MENU_SERVICE_FIXTURE")??throw new InvalidOperationException("Fixture required");
            var summary=JObject.Parse(File.ReadAllText(Path.Combine(root,"summary.json")));var config=JObject.Parse(File.ReadAllText(Path.Combine(root,"config.json")));byte[] initial=File.ReadAllBytes(Path.Combine(root,"initial-snapshot.json"));var snapshot=JObject.Parse(Encoding.UTF8.GetString(initial));
            var package=PackageLoader.Load((string)summary["package_path"],(string)summary["package_sha256"],true);var binding=new MenuStoreBinding((string)config["unit_id"],(string)config["book_id"],(string)config["bank_sha256"],package.PackageSha256,(string)summary["config_sha256"]);
            var watch=Stopwatch.StartNew();string output=Path.Combine(root,"unity-checkpoint.jsonl");using var log=new FileStream(output,FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);int checkpoints=0;
            void Persist(JObject value){byte[] bytes=new UTF8Encoding(false).GetBytes(value.ToString(Newtonsoft.Json.Formatting.None)+"\n");log.Write(bytes,0,bytes.Length);log.Flush(true);checkpoints++;}
            using var port=new FileMenuStore(Path.Combine(root,"mailbox"),binding,package,initial,(string)summary["initial_manifest_sha256"],null,(string)snapshot["snapshot_sha256"],package.AtomIds.Where(x=>x.EndsWith("1",StringComparison.Ordinal)||x.EndsWith("2",StringComparison.Ordinal)),Persist,()=>watch.Elapsed.TotalMilliseconds);
            void Ready(){var until=watch.Elapsed.TotalSeconds+5;while(!port.Ready&&watch.Elapsed.TotalSeconds<until){port.Pump();Thread.Sleep(10);}if(!port.Ready)throw new InvalidOperationException("Store did not become ready");}
            Ready();double start=watch.Elapsed.TotalMilliseconds;string profile=port.RequestSelection("profile","P1",null);Ready();double profileMs=watch.Elapsed.TotalMilliseconds-start;
            if(!port.TryGetReceipt(profile,out var profileReceipt))throw new InvalidOperationException("Profile receipt absent");start=watch.Elapsed.TotalMilliseconds;string atom=port.RequestSelection("K-a1","P1",1);Ready();double atomMs=watch.Elapsed.TotalMilliseconds-start;
            if(!port.TryGetReceipt(atom,out var atomReceipt)||port.Get("K-a1").Rank!=1||port.Get("K-a1").Profile!="P1")throw new InvalidOperationException("Actual selection handoff failed");
            start=watch.Elapsed.TotalMilliseconds;port.RequestVerification();Ready();double verifyMs=watch.Elapsed.TotalMilliseconds-start;
            var report=new JObject{["scope"]="actual_python_store_unity_file_mailbox_DEMO",["package_sha256"]=package.PackageSha256,["config_sha256"]=binding.ConfigSha256,["profile_receipt_sha256"]=profileReceipt,["atom_receipt_sha256"]=atomReceipt,["profile_ms"]=profileMs,["atom_ms"]=atomMs,["verify_ms"]=verifyMs,["durable_checkpoints"]=checkpoints,["participant_ready"]=false,["audio_played"]=false,["timing_qualified"]=false};
            byte[] reportBytes=Encoding.UTF8.GetBytes(report.ToString()+"\n");using var reportFile=new FileStream(Path.Combine(root,"unity-report.json"),FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);reportFile.Write(reportBytes,0,reportBytes.Length);reportFile.Flush(true);UnityEngine.Debug.Log("MENU_STORE_INTEROP_PASS actual_profile_atom_verify=true participant_ready=false audio_played=false");
        }
    }
}
