using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StateSources
{
    public sealed class StateSourceConfiguration
    {
        public string SceneHash { get; private set; }
        public string LayoutHash { get; private set; }
        public string NeutralHash { get; private set; }
        public string NeutralFile { get; private set; }
        public double InterpolationDelay { get; private set; }
        public SourceClock Clock { get; private set; }
        public static StateSourceConfiguration Load(string json)
        {
            var value=StateParser.Json(json);
            StateParser.Keys(value,"version","scene_sha256","layout_sha256","neutral_sha256","neutral_file","interpolation_delay_s","clock");
            StateParser.Require(value["version"].Type==JTokenType.Integer && (int)value["version"]==1,"SOURCE_CONFIG_VERSION");
            string scene=StateParser.Text(value["scene_sha256"]), neutral=StateParser.Text(value["neutral_sha256"]);
            string layout=StateParser.Text(value["layout_sha256"]);
            StateParser.Require(StateParser.IsHash(scene) && StateParser.IsHash(neutral) && StateParser.IsHash(layout),"SOURCE_CONFIG_HASH");
            string file=StateParser.Text(value["neutral_file"]);
            StateParser.Require(Regex.IsMatch(file,"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\\.json$") && !file.Contains(".."),"SOURCE_ASSET_PATH");
            double delay=StateParser.Number(value["interpolation_delay_s"]);
            StateParser.Require(delay>=0 && delay<=.2,"SOURCE_INTERPOLATION_DELAY");
            var clock=value["clock"] as JObject;
            StateParser.Keys(clock,"drift_bound_ppm","max_echo_age_s","evidence_sha256");
            double age=StateParser.Number(clock["max_echo_age_s"]);
            StateParser.Require(age>0 && age<=5,"SOURCE_ECHO_AGE");
            bool noEvidence=clock["drift_bound_ppm"].Type==JTokenType.Null && clock["evidence_sha256"].Type==JTokenType.Null;
            SourceClock mapping=null;
            if(!noEvidence)
            {
                string evidence=StateParser.Text(clock["evidence_sha256"]);
                StateParser.Require(StateParser.IsHash(evidence),"SOURCE_CLOCK_EVIDENCE");
                mapping=new SourceClock(StateParser.Number(clock["drift_bound_ppm"]),age,evidence);
            }
            return new StateSourceConfiguration { SceneHash=scene,LayoutHash=layout,NeutralHash=neutral,NeutralFile=file,InterpolationDelay=delay,Clock=mapping };
        }
        public SceneRegistry Registry(string stationId,string layoutJson,IEnumerable<string> canonicalNames)
        {
            StateParser.Require(SceneRegistry.Hash(Encoding.UTF8.GetBytes(layoutJson))==LayoutHash,"LAYOUT_HASH_MISMATCH");
            var layout=StateParser.Json(layoutJson);
            var objects=layout["objects"] as JArray;
            StateParser.Require(objects!=null && objects.Count>0,"SOURCE_LAYOUT");
            var keys=new Dictionary<string,string[]>(StringComparer.Ordinal);
            foreach(var token in objects)
            {
                string id=StateParser.Text(token["id"]);
                StateParser.Require(token["state"] is JObject,"SOURCE_LAYOUT_STATE");
                keys.Add(id,((JObject)token["state"]).Properties().Select(x=>x.Name).ToArray());
            }
            StateParser.Require(layout["anchor_ids"] is JArray,"SOURCE_LAYOUT_ANCHORS");
            return new SceneRegistry(stationId,SceneHash,NeutralHash,canonicalNames,keys,
                ((JArray)layout["anchor_ids"]).Select(StateParser.Text));
        }
        public byte[] LoadNeutralBytes(string privateDirectory)
        {
            // Basename validation prevents traversal out of the provisioned directory.
            string path=Path.Combine(privateDirectory,NeutralFile);
            var file=new FileInfo(path);
            if(!file.Exists || file.Length>65536) throw new StateFault("NEUTRAL_ASSET_MISSING_OR_LARGE");
            byte[] bytes=File.ReadAllBytes(path);
            if(SceneRegistry.Hash(bytes)!=NeutralHash) throw new StateFault("HASH_MISMATCH");
            return bytes;
        }
    }
}
