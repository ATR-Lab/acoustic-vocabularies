using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.ViewCapture
{
    // Private run directory. Every file is created exclusively, flushed to
    // disk and re-read against its in-memory SHA-256. Nothing is replaced,
    // renamed over or deleted; a failed run keeps every byte already written.
    public sealed class ViewCaptureOutput:IDisposable
    {
        public const string RunFile="run.json",RowsFile="rows.jsonl",StatusFile="status.json",ControlFile="control.jsonl";
        public string Directory{get;}
        FileStream rows;bool statusWritten,disposed;int rowCount;
        public int RowCount=>rowCount;

        ViewCaptureOutput(string directory){Directory=directory;}

        // The run directory must be a fresh direct child of the capability's
        // private SIMULATION_TEST output root.
        public static ViewCaptureOutput Create(string authorizedRoot,string runName)
        {
            ViewCaptureFault.Require(authorizedRoot!=null&&Path.IsPathRooted(authorizedRoot)&&runName!=null&&
                System.Text.RegularExpressions.Regex.IsMatch(runName,@"\Aview-capture-[A-Za-z0-9][A-Za-z0-9._-]{0,63}\z")&&!runName.Contains(".."),"CAPTURE_RUN_NAME");
            string root=Path.GetFullPath(authorizedRoot);CaptureJson.NoLinks(root);
            ViewCaptureFault.Require(System.IO.Directory.Exists(root),"CAPTURE_OUTPUT_ROOT_MISSING");
            string path=Path.Combine(root,runName);
            ViewCaptureFault.Require(!System.IO.Directory.Exists(path)&&!File.Exists(path),"CAPTURE_OUTPUT_EXISTS");
            System.IO.Directory.CreateDirectory(path);CaptureJson.NoLinks(path);
            // Refuse a directory that another writer populated concurrently.
            ViewCaptureFault.Require(!System.IO.Directory.EnumerateFileSystemEntries(path).Any(),"CAPTURE_OUTPUT_EXISTS");
            return new ViewCaptureOutput(path);
        }

        string Resolve(string name)
        {
            ViewCaptureFault.Require(name!=null&&System.Text.RegularExpressions.Regex.IsMatch(name,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z")&&!name.Contains(".."),"CAPTURE_FILE_NAME");
            return Path.Combine(Directory,name);
        }

        // Returns the verifier reference {path,sha256} for the exact bytes.
        public JObject WriteExclusive(string name,byte[] bytes)
        {
            ViewCaptureFault.Require(!disposed&&!statusWritten,"CAPTURE_WRITE_STATE");
            return Write(name,bytes);
        }
        JObject Write(string name,byte[] bytes)
        {
            ViewCaptureFault.Require(bytes!=null&&bytes.Length>0,"CAPTURE_WRITE_STATE");
            string path=Resolve(name);string hash=CaptureJson.Hash(bytes);
            try
            {
                using(var stream=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.None))
                {stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
                ViewCaptureFault.Require(CaptureJson.Hash(File.ReadAllBytes(path))==hash,"CAPTURE_WRITE_VERIFY");
            }
            catch(ViewCaptureFault){throw;}
            catch(IOException){throw new ViewCaptureFault("CAPTURE_WRITE_FAILED");}
            catch(UnauthorizedAccessException){throw new ViewCaptureFault("CAPTURE_WRITE_FAILED");}
            return new JObject{["path"]=name,["sha256"]=hash};
        }

        public JObject WriteJson(string name,JToken value)=>WriteExclusive(name,CaptureJson.Canonical(value));

        public void OpenRows()
        {
            ViewCaptureFault.Require(rows==null&&!disposed,"CAPTURE_ROWS_STATE");
            try{rows=new FileStream(Resolve(RowsFile),FileMode.CreateNew,FileAccess.Write,FileShare.Read);}
            catch(IOException){throw new ViewCaptureFault("CAPTURE_WRITE_FAILED");}
        }

        // A row is appended only after all of its referenced files are durable.
        public void AppendRow(JObject row)
        {
            ViewCaptureFault.Require(rows!=null&&!disposed&&!statusWritten,"CAPTURE_ROWS_STATE");
            byte[] line=CaptureJson.Canonical(row);
            try{rows.Write(line,0,line.Length);rows.Flush(true);rowCount++;}
            catch(IOException){throw new ViewCaptureFault("CAPTURE_WRITE_FAILED");}
        }

        // Written exactly once, after the rows journal is closed. Faulted runs
        // record complete:false and their first fault; no file is removed.
        public JObject WriteStatus(bool complete,string fault,int required,JObject runReference)
        {
            ViewCaptureFault.Require(!statusWritten&&!disposed,"CAPTURE_STATUS_STATE");statusWritten=true;
            JToken rowsReference=JValue.CreateNull();
            try{rows?.Dispose();}catch(Exception){complete=false;fault??="CAPTURE_WRITE_FAILED";}
            string rowsPath=Path.Combine(Directory,RowsFile);
            if(File.Exists(rowsPath))rowsReference=new JObject{["path"]=RowsFile,["sha256"]=CaptureJson.Hash(File.ReadAllBytes(rowsPath))};
            var status=new JObject{["version"]=1,["kind"]="view_capture_status",["complete"]=complete&&fault==null,["fault"]=fault==null?JValue.CreateNull():new JValue(fault),
                ["captures_completed"]=rowCount,["captures_required"]=required,["run"]=runReference==null?JValue.CreateNull():runReference.DeepClone(),["rows"]=rowsReference,
                ["participant_admission"]=false,["g3_signed"]=false};
            return Write(StatusFile,CaptureJson.Canonical(status));
        }

        public void Dispose(){if(disposed)return;disposed=true;try{rows?.Dispose();}catch(Exception){}}
    }
}
