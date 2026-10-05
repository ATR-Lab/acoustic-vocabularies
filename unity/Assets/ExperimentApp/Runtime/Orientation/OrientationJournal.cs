using System;
using System.IO;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Orientation
{
    public sealed class OrientationJournal : IDisposable
    {
        readonly FileStream stream;readonly StreamWriter writer;
        public OrientationJournal(string directory,JObject build,string station,string planHash,string demoIndexHash)
        {
            Directory.CreateDirectory(directory);
            stream=new FileStream(Path.Combine(directory,"orientation-"+Guid.NewGuid().ToString("N")+".jsonl"),FileMode.CreateNew,FileAccess.Write,FileShare.Read);
            writer=new StreamWriter(stream,new UTF8Encoding(false)) { AutoFlush=true };
            Record(new JObject { ["event"]="orientation_header",["schema"]="silent-orientation-v1",["build_identity"]=build.DeepClone(),["station_id"]=station,["plan_sha256"]=planHash,["demo_index_sha256"]=demoIndexHash,["preallocation"]=true,["study_audio_loaded"]=false });
        }
        public void Record(JObject value) { writer.WriteLine(value.ToString(Formatting.None));stream.Flush(true); }
        public void Dispose() => writer.Dispose();
    }
}
