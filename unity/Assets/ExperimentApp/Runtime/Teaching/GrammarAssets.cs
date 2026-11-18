using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using AcousticVocab.Foundation;
using AcousticVocab.SessionEngine;
using AcousticVocab.StudyAudio;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.Teaching
{
    // A separate familiarization source: no book atoms, messages or candidate
    // callbacks can enter this loader. Registry and WAVs are the #14 producer.
    // The spoken explanation is one private recording with a fixed ID, pinned
    // by its own SHA-256; no study, role or lesson format selects it.
    public sealed class GrammarAssets
    {
        public const string ExplanationId="grammar-explanation";
        // A complete study message is at most two 900 ms motifs and the 200 ms
        // gap (96,000 samples), as is the 2 s calibration example. A longer
        // recording can never be a motif, a message or a reserved signal.
        public const int ExplanationMinimumSamples=96001,ExplanationMaximumSamples=60*PcmWave.SampleRate;
        readonly string directory;
        public PcmWave Explanation {get;}
        public PcmWave Ready {get;}
        public PcmWave Clicks {get;}
        public string RegistrySha256 {get;}
        GrammarAssets(string directory,PcmWave explanation,PcmWave ready,PcmWave clicks,string hash){this.directory=directory;Explanation=explanation;Ready=ready;Clicks=clicks;RegistrySha256=hash;}
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
                    var file=new FileInfo(Path.Combine(directory,id+".wav"));LessonTimeline.Require(file.Exists&&file.Length==44+samples*2,"GRAMMAR_WAVE");NoLinks(file);
                    var wave=PcmWave.ParseCanonical(File.ReadAllBytes(file.FullName));LessonTimeline.Require(wave.FileSha256==(string)entry["file_sha256"]&&wave.PcmSha256==(string)entry["pcm_sha256"],"GRAMMAR_HASH");return wave;
                }
                return new GrammarAssets(directory,null,Read("ready-cue","ready_cue",15360),Read("click-grammar-demo","click",13824),expectedRegistrySha256);
            }
            catch(SessionFault){throw;}catch{throw new SessionFault("GRAMMAR_ASSETS_INVALID");}
        }
        // Adds the one spoken explanation from the same verified directory. The
        // pin is the recording's file SHA-256 from a separately pinned record.
        public GrammarAssets WithExplanation(string expectedFileSha256)
        {
            try
            {
                LessonTimeline.Require(Explanation==null&&directory!=null,"GRAMMAR_EXPLANATION_STATE");
                LessonTimeline.Require(expectedFileSha256!=null&&Regex.IsMatch(expectedFileSha256,@"\A[0-9a-f]{64}\z"),"GRAMMAR_EXPLANATION_HASH");
                var file=new FileInfo(Path.Combine(directory,ExplanationId+".wav"));LessonTimeline.Require(file.Exists,"GRAMMAR_EXPLANATION_MISSING");
                NoLinks(file);LessonTimeline.Require(file.Length<=44+ExplanationMaximumSamples*2L,"GRAMMAR_EXPLANATION_LENGTH");
                byte[] bytes=File.ReadAllBytes(file.FullName);LessonTimeline.Require(PcmWave.Hash(bytes)==expectedFileSha256,"GRAMMAR_EXPLANATION_HASH");
                PcmWave wave;try{wave=PcmWave.ParseCanonical(bytes);}catch(AudioFault){throw new SessionFault("GRAMMAR_EXPLANATION_FORMAT");}
                LessonTimeline.Require(wave.SampleCount>=ExplanationMinimumSamples&&wave.SampleCount<=ExplanationMaximumSamples,"GRAMMAR_EXPLANATION_LENGTH");
                return new GrammarAssets(directory,wave,Ready,Clicks,RegistrySha256);
            }
            catch(SessionFault){throw;}catch{throw new SessionFault("GRAMMAR_ASSETS_INVALID");}
        }
        static void NoLinks(FileInfo file)
        {for(FileSystemInfo item=file;item!=null;item=item is FileInfo f?f.Directory:((DirectoryInfo)item).Parent)LessonTimeline.Require((item.Attributes&FileAttributes.ReparsePoint)==0,"GRAMMAR_LINK");}
        public IReadOnlyDictionary<string,PcmWave> Preload(string explanationRequestId,string readyRequestId,string clicksRequestId)
        {
            LessonTimeline.Require(Explanation!=null,"GRAMMAR_EXPLANATION_MISSING");
            var ids=new[]{explanationRequestId,readyRequestId,clicksRequestId};LessonTimeline.Require(ids.All(id=>!string.IsNullOrEmpty(id))&&ids.Distinct().Count()==3,"GRAMMAR_REQUEST_IDS");
            return new System.Collections.ObjectModel.ReadOnlyDictionary<string,PcmWave>(new Dictionary<string,PcmWave>{{explanationRequestId,Explanation},{readyRequestId,Ready},{clicksRequestId,Clicks}});
        }
    }
}
