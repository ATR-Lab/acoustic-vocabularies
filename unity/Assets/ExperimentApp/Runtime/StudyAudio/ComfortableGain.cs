using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StudyAudio
{
    public sealed class GainChange
    {
        public float Previous { get; }
        public float Current { get; }
        public double MonoSeconds { get; }
        internal GainChange(float previous,float current,double mono) { Previous=previous;Current=current;MonoSeconds=mono; }
    }

    // Private append-only profile history is also the durable source of truth.
    // A torn or malformed history blocks restoration instead of choosing a gain.
    public sealed class ComfortableGainStore
    {
        public const float InitialGain=.1f; // Relative starting gain, not an SPL claim.
        readonly string directory;
        public ComfortableGainStore(string privateDirectory)
        { directory=Path.GetFullPath(privateDirectory);Directory.CreateDirectory(directory); }
        string FileFor(string codedId)
        {
            if(codedId==null || !Regex.IsMatch(codedId,"\\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\\z")) throw new AudioFault("GAIN_CODE_INVALID");
            using var sha=SHA256.Create();string hash=BitConverter.ToString(sha.ComputeHash(Encoding.ASCII.GetBytes(codedId))).Replace("-","").ToLowerInvariant();
            return Path.Combine(directory,hash+".local.jsonl");
        }
        static float Read(string text,string codedId)
        {
            float gain=InitialGain;
            if(text.Length==0) return gain;
            if(text.Length>1048576 || !text.EndsWith("\n",StringComparison.Ordinal)) throw new AudioFault("GAIN_HISTORY_INVALID");
            bool first=true;
            foreach(string line in text.Split('\n'))
            {
                if(line.Length==0) continue;
                JObject row;
                try { row=JObject.Parse(line,new JsonLoadSettings { DuplicatePropertyNameHandling=DuplicatePropertyNameHandling.Error }); }
                catch { throw new AudioFault("GAIN_HISTORY_INVALID"); }
                bool carried=(string)row["event"]=="comfort_gain_carried";
                if(carried && !first) throw new AudioFault("GAIN_HISTORY_INVALID");
                first=false;
                if(carried) { gain=CarriedGain(row,line,codedId);continue; }
                if(row.Count!=6 || (string)row["event"]!="comfort_gain_changed" || (string)row["coded_id"]!=codedId ||
                    row["old_gain"]?.Type is not (JTokenType.Float or JTokenType.Integer) ||
                    row["new_gain"]?.Type is not (JTokenType.Float or JTokenType.Integer) ||
                    row["mono_s"]?.Type is not (JTokenType.Float or JTokenType.Integer) ||
                    row["visit_id"]?.Type!=JTokenType.String || !Regex.IsMatch((string)row["visit_id"],"\\A[0-9a-f]{32}\\z") ||
                    row.ToString(Formatting.None)!=line) throw new AudioFault("GAIN_HISTORY_INVALID");
                double next=(double)row["new_gain"],prior=(double)row["old_gain"],mono=(double)row["mono_s"];
                if(!AudioRouteCalibration.Finite(next) || next<=0 || next>1 || (float)prior!=gain ||
                    !AudioRouteCalibration.Finite(mono) || mono<0) throw new AudioFault("GAIN_HISTORY_INVALID");
                gain=(float)next;
            }
            return gain;
        }
        // A carried row opens the history of an allocated slot. It copies the exact
        // gain recorded under the screening code, bound to the allocation receipt,
        // so the level chosen before allocation is the level used at every visit.
        static float CarriedGain(JObject row,string line,string codedId)
        {
            if(row.Count!=7 || (string)row["coded_id"]!=codedId ||
                row["source_coded_id"]?.Type!=JTokenType.String || !Regex.IsMatch((string)row["source_coded_id"],CodePattern) || (string)row["source_coded_id"]==codedId ||
                row["source_history_sha256"]?.Type!=JTokenType.String || !Regex.IsMatch((string)row["source_history_sha256"],"\\A[0-9a-f]{64}\\z") ||
                row["allocation_receipt_sha256"]?.Type!=JTokenType.String || !Regex.IsMatch((string)row["allocation_receipt_sha256"],"\\A[0-9a-f]{64}\\z") ||
                row["gain"]?.Type is not (JTokenType.Float or JTokenType.Integer) ||
                row["mono_s"]?.Type is not (JTokenType.Float or JTokenType.Integer) ||
                row.ToString(Formatting.None)!=line) throw new AudioFault("GAIN_HISTORY_INVALID");
            double value=(double)row["gain"],mono=(double)row["mono_s"];
            if(!AudioRouteCalibration.Finite(value) || value<=0 || value>1 || !AudioRouteCalibration.Finite(mono) || mono<0) throw new AudioFault("GAIN_HISTORY_INVALID");
            return (float)value;
        }
        const string CodePattern="\\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\\z";
        public static float RestoreVerified(byte[] bytes,string expectedRawSha256,string codedId)
        {
            if(bytes==null||bytes.Length==0||bytes.Length>1048576||PcmWave.Hash(bytes)!=expectedRawSha256||codedId==null||!Regex.IsMatch(codedId,@"\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z"))throw new AudioFault("GAIN_HISTORY_INVALID");
            string text=new UTF8Encoding(false,true).GetString(bytes);if(string.IsNullOrWhiteSpace(text))throw new AudioFault("GAIN_HISTORY_INVALID");return Read(text,codedId);
        }
        public float Restore(string codedId)
        {
            string path=FileFor(codedId);
            if(!File.Exists(path)) return InitialGain;
            if(new FileInfo(path).Length>1048576) throw new AudioFault("GAIN_HISTORY_INVALID");
            return Read(File.ReadAllText(path,Encoding.UTF8),codedId);
        }
        // Starts an allocated slot's history from the gain recorded under its
        // screening code. Never invents a gain and never overwrites a history:
        // the source must hold at least one recorded row and the slot must be new.
        public float CarryForward(string sourceCodedId,string codedId,string allocationReceiptSha256,double monoSeconds)
        {
            if(sourceCodedId==codedId || allocationReceiptSha256==null || !Regex.IsMatch(allocationReceiptSha256,"\\A[0-9a-f]{64}\\z") ||
                !AudioRouteCalibration.Finite(monoSeconds) || monoSeconds<0) throw new AudioFault("GAIN_CARRY_INVALID");
            string source=FileFor(sourceCodedId),target=FileFor(codedId);
            if(!File.Exists(source) || new FileInfo(source).Length==0) throw new AudioFault("GAIN_CARRY_SOURCE_MISSING");
            if(new FileInfo(source).Length>1048576) throw new AudioFault("GAIN_HISTORY_INVALID");
            byte[] bytes=File.ReadAllBytes(source);
            float gain=Read(new UTF8Encoding(false,true).GetString(bytes),sourceCodedId);
            var row=new JObject { ["event"]="comfort_gain_carried",["coded_id"]=codedId,["source_coded_id"]=sourceCodedId,
                ["source_history_sha256"]=PcmWave.Hash(bytes),["allocation_receipt_sha256"]=allocationReceiptSha256,["gain"]=gain,["mono_s"]=monoSeconds };
            byte[] line=new UTF8Encoding(false).GetBytes(row.ToString(Formatting.None)+"\n");
            try
            {
                using var stream=new FileStream(target,FileMode.CreateNew,FileAccess.Write,FileShare.Read,4096,FileOptions.WriteThrough);
                stream.Write(line,0,line.Length);stream.Flush(true);
            }
            catch(IOException) when(File.Exists(target)) { throw new AudioFault("GAIN_CARRY_TARGET_EXISTS"); }
            return gain;
        }
        // Idempotent relaunch: an existing slot history must have been carried
        // from the same screening code and allocation receipt.
        public float RestoreOrCarry(string sourceCodedId,string codedId,string allocationReceiptSha256,double monoSeconds)
        {
            string target=FileFor(codedId);
            if(!File.Exists(target)) return CarryForward(sourceCodedId,codedId,allocationReceiptSha256,monoSeconds);
            string text=File.ReadAllText(target,Encoding.UTF8);
            JObject head;
            try { head=JObject.Parse(text.Substring(0,Math.Max(0,text.IndexOf('\n')))); } catch { throw new AudioFault("GAIN_HISTORY_INVALID"); }
            if((string)head["event"]!="comfort_gain_carried" || (string)head["source_coded_id"]!=sourceCodedId ||
                (string)head["allocation_receipt_sha256"]!=allocationReceiptSha256) throw new AudioFault("GAIN_CARRY_MISMATCH");
            return Restore(codedId);
        }
        public GainChange ChangeForComfort(string codedId,string visitId,float value,double monoSeconds,bool audioIdle)
        {
            if(!audioIdle || float.IsNaN(value) || float.IsInfinity(value) || value<=0 || value>1 ||
                !AudioRouteCalibration.Finite(monoSeconds) || monoSeconds<0 || visitId==null ||
                !Regex.IsMatch(visitId,"\\A[0-9a-f]{32}\\z")) throw new AudioFault("GAIN_CHANGE_INVALID");
            string path=FileFor(codedId);
            using var stream=new FileStream(path,FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.Read,4096,FileOptions.WriteThrough);
            if(stream.Length>1044480) throw new AudioFault("GAIN_HISTORY_FULL");
            string text;using(var reader=new StreamReader(stream,Encoding.UTF8,true,4096,true)) text=reader.ReadToEnd();
            float old=Read(text,codedId);
            var row=new JObject { ["event"]="comfort_gain_changed",["coded_id"]=codedId,["visit_id"]=visitId,
                ["old_gain"]=old,["new_gain"]=value,["mono_s"]=monoSeconds };
            byte[] bytes=new UTF8Encoding(false).GetBytes(row.ToString(Formatting.None)+"\n");
            stream.Position=stream.Length;stream.Write(bytes,0,bytes.Length);stream.Flush(true);
            return new GainChange(old,value,monoSeconds);
        }
    }

    public sealed class ProfileComfortSequence
    {
        readonly string[] order;
        int profileIndex;
        bool started,firstComplete,secondComplete;
        double lastCompletion;
        public string Profile => profileIndex<3?order[profileIndex]:null;
        public bool Finished => profileIndex==3;
        public double FirstOnsetMonoSeconds { get; private set; }
        public double SecondOnsetMonoSeconds => FirstOnsetMonoSeconds+4;
        public bool CanAnswer => started && firstComplete && secondComplete;
        public event Action<string,bool,double> Answer;
        public ProfileComfortSequence(string[] storedOrder)
        {
            if(storedOrder==null || storedOrder.Length!=3 || new System.Collections.Generic.HashSet<string>(storedOrder).Count!=3 ||
                Array.Exists(storedOrder,x=>x!="P1" && x!="P2" && x!="P3")) throw new AudioFault("COMFORT_ORDER_INVALID");
            order=(string[])storedOrder.Clone();
        }
        public void StartProfile(double firstOnsetMonoSeconds,PcmWave example)
        {
            if(Finished || started || example==null || example.SampleCount!=96000 ||
                !AudioRouteCalibration.Finite(firstOnsetMonoSeconds) || firstOnsetMonoSeconds<0) throw new AudioFault("COMFORT_SEQUENCE_INVALID");
            FirstOnsetMonoSeconds=firstOnsetMonoSeconds;lastCompletion=firstOnsetMonoSeconds;started=true;
        }
        public void ConfirmPlayback(int playIndex,double completionMonoSeconds)
        {
            if(!started || !AudioRouteCalibration.Finite(completionMonoSeconds) ||
                playIndex is not (0 or 1) || completionMonoSeconds<lastCompletion || completionMonoSeconds<FirstOnsetMonoSeconds+(playIndex==0?2:6) ||
                playIndex==0 && firstComplete || playIndex==1 && (!firstComplete || secondComplete))
                throw new AudioFault("COMFORT_DELIVERY_INVALID");
            if(playIndex==0) firstComplete=true;else secondComplete=true;lastCompletion=completionMonoSeconds;
        }
        public void Respond(bool comfortable,double monoSeconds)
        {
            if(!CanAnswer || Answer==null || !AudioRouteCalibration.Finite(monoSeconds) || monoSeconds<lastCompletion)
                throw new AudioFault("COMFORT_RESPONSE_INVALID");
            Answer(Profile,comfortable,monoSeconds);profileIndex++;started=firstComplete=secondComplete=false;
        }
    }
}
