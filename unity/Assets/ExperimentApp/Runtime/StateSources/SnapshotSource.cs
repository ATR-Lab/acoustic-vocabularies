using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json.Linq;

namespace AcousticVocab.StateSources
{
    public sealed class SnapshotSource : IRobotStateSource
    {
        readonly SceneRegistry registry;
        readonly SceneFrame neutral;
        SceneFrame held;
        List<SceneFrame> frames;
        double playStart, playDuration, lastValid, gapAt=double.PositiveInfinity, gapDuration;
        bool stale, failed;
        public string Kind => "snapshot";
        public bool Stale => stale;
        public double SampleAgeSeconds { get; private set; }
        public double LastSimTime => held.SimTime;
        public bool ResetConfirmed { get; private set; }
        public bool PlaybackFinished { get; private set; } = true;
        public event Action<SourceEvent> Event;
        public SceneFrame Neutral => neutral;
        public SnapshotSource(byte[] snapshotBytes, SceneRegistry expected, double now)
        {
            registry=expected;
            if (snapshotBytes==null || SceneRegistry.Hash(snapshotBytes)!=registry.SnapshotHash) throw new StateFault("HASH_MISMATCH");
            var snapshot=StateParser.Json(new UTF8Encoding(false,true).GetString(snapshotBytes));
            StateParser.Keys(snapshot,"schema_version","scene_sha256","fixed_steps","coordinate_frame","state");
            StateParser.Require(StateParser.Text(snapshot["schema_version"])=="1.0.0" &&
                StateParser.Text(snapshot["coordinate_frame"])=="usd_world_rh_z_up_xyzw" &&
                StateParser.Text(snapshot["scene_sha256"])==registry.SceneHash,"SNAPSHOT_IDENTITY");
            StateParser.Require(snapshot["fixed_steps"].Type==JTokenType.Integer &&
                (int)snapshot["fixed_steps"]>=1 && (int)snapshot["fixed_steps"]<=10,"SNAPSHOT_FIXED_STEPS");
            CheckTime(now, double.NegativeInfinity);
            var state=snapshot["state"] as JObject;
            StateParser.Keys(state,"robot","objects","environment","frames");
            var robot=state["robot"] as JObject;
            StateParser.Keys(robot,"joint_names","joint_positions_rad","joint_velocities_rad_s","root_position_m",
                "root_rotation_xyzw","root_linear_velocity_m_s","root_angular_velocity_rad_s");
            StateParser.Require(robot["joint_names"] is JArray names && names.Select(StateParser.Text).SequenceEqual(registry.JointNames),"STATE_JOINT_ORDER");
            Zero(robot["joint_velocities_rad_s"],43);
            Zero(robot["root_linear_velocity_m_s"],3); Zero(robot["root_angular_velocity_rad_s"],3);
            StateParser.Vector(robot["root_position_m"],3); Quaternion(robot["root_rotation_xyzw"]);
            var environment=state["environment"] as JObject;
            StateParser.Keys(environment,"materials","lights");
            foreach(var category in environment.Properties())
            { StateParser.Require(category.Value is JObject,"SNAPSHOT_ENVIRONMENT"); PrimitiveTree(category.Value); }
            var links=state["frames"] as JObject;
            StateParser.Require(links!=null && links.Count>=3,"SNAPSHOT_FRAMES");
            foreach(var link in links.Properties())
            {
                StateParser.Require(link.Name.Length>0,"SNAPSHOT_FRAMES");
                var pose=link.Value as JObject; StateParser.Keys(pose,"position_m","rotation_xyzw");
                StateParser.Vector(pose["position_m"],3); Quaternion(pose["rotation_xyzw"]);
            }
            var objects=state["objects"] as JObject;
            StateParser.Keys(objects,registry.ObjectKeys.Keys.ToArray());
            var parsed=new List<SceneObject>();
            foreach(var pair in registry.ObjectKeys)
            {
                var source=objects[pair.Key] as JObject;
                StateParser.Keys(source,"position_m","rotation_xyzw","visible","enabled","collision_enabled",
                    "linear_velocity_m_s","angular_velocity_rad_s","state");
                StateParser.Require(source["collision_enabled"].Type==JTokenType.Boolean,"SNAPSHOT_COLLISION");
                Zero(source["linear_velocity_m_s"],3); Zero(source["angular_velocity_rad_s"],3);
                var visual=new JObject { ["id"]=pair.Key };
                foreach(string key in new[]{"position_m","rotation_xyzw","visible","enabled","state"})
                    visual[key]=source[key].DeepClone();
                parsed.Add(StateParser.ParseObject(visual,pair.Key,pair.Value,registry));
            }
            neutral=new SceneFrame(new string('0',32),0,0,0,0,"snapshot",
                StateParser.Vector(robot["joint_positions_rad"],43),parsed);
            held=neutral; lastValid=now; ResetConfirmed=true;
        }
        public void PlayTrajectory(byte[] bytes, string expectedHash, double now, double? nominalDurationSeconds=null)
        {
            try
            {
                CheckTime(now,lastValid);
                StateParser.Require(bytes!=null && bytes.Length<=64*1024*1024 &&
                    StateParser.IsHash(expectedHash) && SceneRegistry.Hash(bytes)==expectedHash,"HASH_MISMATCH");
                string text=new UTF8Encoding(false,true).GetString(bytes);
                var loaded=new List<SceneFrame>();
                foreach(string line in text.Split('\n'))
                {
                    if(string.IsNullOrWhiteSpace(line)) continue;
                    if(loaded.Count>=12000) throw new StateFault("TRAJECTORY_TOO_LARGE");
                    var frame=StateParser.Parse(line,registry);
                    if(loaded.Count>0)
                    {
                        var previous=loaded[loaded.Count-1];
                        StateParser.Require(frame.SessionId==previous.SessionId && frame.Sequence==previous.Sequence+1 &&
                            frame.PublishedNs>previous.PublishedNs && frame.SimStep>previous.SimStep &&
                            frame.SimTime>previous.SimTime,"TRAJECTORY_NONPROGRESSING");
                        StateParser.Require(frame.PublishedNs-previous.PublishedNs<=250000000,"TRAJECTORY_STALE_GAP");
                    }
                    loaded.Add(frame);
                }
                StateParser.Require(loaded.Count>=2 &&
                    (loaded[loaded.Count-1].PublishedNs-loaded[0].PublishedNs)/1e9<=600,"TRAJECTORY_DURATION");
                StateParser.Require(NeutralComparison.Matches(loaded[0],neutral),"TRAJECTORY_START_NOT_NEUTRAL");
                double measuredSpan=(loaded[loaded.Count-1].PublishedNs-loaded[0].PublishedNs)/1e9;
                double duration=nominalDurationSeconds??measuredSpan;
                StateParser.Require(!double.IsNaN(duration) && !double.IsInfinity(duration) &&
                    duration>=measuredSpan && duration<=600,"TRAJECTORY_CAPTURE_OVERRUN");
                frames=loaded; playStart=lastValid=now; playDuration=duration;
                ResetConfirmed=false; PlaybackFinished=false;
                Event?.Invoke(new SourceEvent("TRAJECTORY_STARTED",now,now));
            }
            catch(StateFault ex) { failed=true; ResetConfirmed=false; Event?.Invoke(new SourceEvent(ex.Message,now,now)); throw; }
            catch(Exception) { failed=true; ResetConfirmed=false; Event?.Invoke(new SourceEvent("TRAJECTORY_MALFORMED",now,now)); throw new StateFault("TRAJECTORY_MALFORMED"); }
        }
        public void RestoreNeutral(double now)
        {
            CheckTime(now,lastValid);
            frames=null; held=neutral; lastValid=now; ResetConfirmed=true; PlaybackFinished=true; stale=failed=false; gapAt=double.PositiveInfinity;
            Event?.Invoke(new SourceEvent("SNAPSHOT_NEUTRAL_RESTORED",now,now));
        }
        // Deterministic engineering fault injection, not enabled by a study request.
        public void InjectGap(double startMonoSeconds,double durationSeconds)
        {
            if(double.IsNaN(startMonoSeconds) || double.IsNaN(durationSeconds) ||
                double.IsInfinity(startMonoSeconds) || double.IsInfinity(durationSeconds) || durationSeconds<0)
                throw new ArgumentException("Invalid injection");
            gapAt=startMonoSeconds; gapDuration=durationSeconds;
        }
        public SceneFrame Render(double now)
        {
            if(double.IsNaN(now) || double.IsInfinity(now) || now<lastValid) throw new StateFault("HOST_CLOCK_REGRESSED");
            if(failed) return held;
            SampleAgeSeconds=now-lastValid;
            bool dropping=now>=gapAt && now<gapAt+gapDuration;
            if(SampleAgeSeconds>.25 && !stale)
            {
                stale=true; ResetConfirmed=false;
                Event?.Invoke(new SourceEvent("STATE_STALE",lastValid,now));
            }
            if(dropping) return held;
            if(stale)
            { Event?.Invoke(new SourceEvent("STATE_RECOVERED",lastValid,now,now-lastValid)); stale=false; }
            lastValid=now; SampleAgeSeconds=0;
            if(frames==null) { held=neutral; return held; }
            double offset=Math.Max(0,now-playStart);
            if(offset>=playDuration && !PlaybackFinished)
            {
                PlaybackFinished=true;
                Event?.Invoke(new SourceEvent("TRAJECTORY_COMPLETED",playStart,now,now-playStart));
            }
            ulong first=frames[0].PublishedNs;
            int index=0;
            while(index+1<frames.Count && (frames[index+1].PublishedNs-first)/1e9<=offset) index++;
            if(index+1==frames.Count) held=frames[index];
            else
            {
                double a=(frames[index].PublishedNs-first)/1e9, b=(frames[index+1].PublishedNs-first)/1e9;
                held=SceneFrame.Interpolate(frames[index],frames[index+1],(offset-a)/(b-a));
            }
            return held;
        }
        public bool ConfirmReset(SceneFrame expectedNeutral,double now)
        {
            Render(now);
            ResetConfirmed=!failed && !stale && frames==null && NeutralComparison.Matches(held,expectedNeutral);
            return ResetConfirmed;
        }
        static void CheckTime(double now,double previous)
        { StateParser.Require(!double.IsNaN(now) && !double.IsInfinity(now) && now>=previous,"HOST_CLOCK_REGRESSED"); }
        static void Zero(JToken value,int length)
        { StateParser.Require(StateParser.Vector(value,length).All(x=>x==0),"SNAPSHOT_NONZERO_VELOCITY"); }
        static void Quaternion(JToken value)
        { StateParser.Require(Math.Abs(StateParser.Vector(value,4).Sum(x=>x*x)-1)<=1e-5,"STATE_QUATERNION"); }
        static void PrimitiveTree(JToken value)
        {
            if(value is JObject obj)
                foreach(var property in obj.Properties())
                { StateParser.Require(property.Name.Length>0,"SNAPSHOT_ENVIRONMENT"); PrimitiveTree(property.Value); }
            else if(value is JArray array)
            { StateParser.Require(array.Count>=1 && array.Count<=4,"SNAPSHOT_ENVIRONMENT"); foreach(var item in array) StateParser.Number(item); }
            else if(value.Type!=JTokenType.Boolean) StateParser.Number(value);
        }
    }
}
