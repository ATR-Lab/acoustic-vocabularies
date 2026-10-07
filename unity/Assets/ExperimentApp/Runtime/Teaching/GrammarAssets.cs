using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using AcousticVocab.Foundation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Teaching
{
    // A separate familiarization source: no book atoms, messages or candidate
    // callbacks can enter this loader. Registry and WAVs are the #14 producer.
    public sealed class GrammarAssets
    {
        public PcmWave Ready {get;}
        public PcmWave Clicks {get;}
        public string RegistrySha256 {get;}
        GrammarAssets(PcmWave ready,PcmWave clicks,string hash){Ready=ready;Clicks=clicks;RegistrySha256=hash;}
        public static GrammarAssets Load(string directory,byte[] registry,string expectedRegistrySha256)
        {
            try
            {
                LessonTimeline.Require(registry!=null&&PcmWave.Hash(registry)==expectedRegistrySha256,"GRAMMAR_HASH");
                var document=StationConfig.ParseStrict(new UTF8Encoding(false,true).GetString(registry));
                LessonTimeline.Require(document["registry_version"]?.Type==JTokenType.Integer&&(int)document["registry_version"]==1&&document["entries"] is JArray,"GRAMMAR_REGISTRY");
                PcmWave Read(string id,string kind,int samples)
                {
                    var entries=document["entries"].Where(x=>(string)x["id"]==id).ToArray();LessonTimeline.Require(entries.Length==1,"GRAMMAR_REGISTRY");var entry=entries[0];
                    LessonTimeline.Require((string)entry["kind"]==kind&&entry["profile"].Type==JTokenType.Null&&entry["recipe"].Type==JTokenType.Null&&entry["n_samples"].Type==JTokenType.Integer&&(int)entry["n_samples"]==samples,"GRAMMAR_REGISTRY");
                    var file=new FileInfo(Path.Combine(directory,id+".wav"));LessonTimeline.Require(file.Exists&&file.Length==44+samples*2,"GRAMMAR_WAVE");
                    for(FileSystemInfo item=file;item!=null;item=item is FileInfo f?f.Directory:((DirectoryInfo)item).Parent)LessonTimeline.Require((item.Attributes&FileAttributes.ReparsePoint)==0,"GRAMMAR_LINK");
                    var wave=PcmWave.ParseCanonical(File.ReadAllBytes(file.FullName));LessonTimeline.Require(wave.FileSha256==(string)entry["file_sha256"]&&wave.PcmSha256==(string)entry["pcm_sha256"],"GRAMMAR_HASH");return wave;
                }
                return new GrammarAssets(Read("ready-cue","ready_cue",15360),Read("click-grammar-demo","click",13824),expectedRegistrySha256);
            }
            catch(SessionFault){throw;}catch{throw new SessionFault("GRAMMAR_ASSETS_INVALID");}
        }
        public IReadOnlyDictionary<string,PcmWave> Preload(string readyRequestId,string clicksRequestId)
        {LessonTimeline.Require(!string.IsNullOrEmpty(readyRequestId)&&!string.IsNullOrEmpty(clicksRequestId)&&readyRequestId!=clicksRequestId,"GRAMMAR_REQUEST_IDS");return new System.Collections.ObjectModel.ReadOnlyDictionary<string,PcmWave>(new Dictionary<string,PcmWave>{{readyRequestId,Ready},{clicksRequestId,Clicks}});}
    }
}
