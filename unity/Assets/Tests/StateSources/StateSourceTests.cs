using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using AcousticVocab.StateSources;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.Tests
{
    public sealed class StateSourceTests
    {
        static readonly string[] Names=Enumerable.Range(0,43).Select(i=>"joint_"+i.ToString("00")).ToArray();
        static SceneRegistry Registry(string snapshotHash=null) => new SceneRegistry("station-01",new string('a',64),
            snapshotHash??new string('b',64),Names,new Dictionary<string,string[]> { ["card"]=new[]{"card_face"} },new[]{"card"});
        static JObject Object(int face=0,double x=0) => JObject.FromObject(new {
            id="card",position_m=new[]{x,0d,0d},rotation_xyzw=new[]{0d,0d,0d,1d},
            visible=true,enabled=true,state=new { card_face=face }
        });
        static JObject Raw(long seq=0,double at=0,int face=0,double x=0,string snapshotHash=null)
        {
            return JObject.FromObject(new {
                version=2,kind="state",source_kind="live",station_id="station-01",
                scene_sha256=new string('a',64),reset_snapshot_sha256=snapshotHash??new string('b',64),
                session_id=new string('c',32),seq,host_monotonic_ns=((ulong)Math.Round(at*1e9)).ToString(),
                sim_time=at,sim_step=seq,joint_names=Names,joint_positions=new double[43],objects=new[]{Object(face,x)}
            });
        }
        static SceneFrame Frame(long seq=0,double at=0,int face=0,double x=0) => StateParser.Parse(Raw(seq,at,face,x).ToString(Formatting.None),Registry());
        static byte[] Snapshot()
        {
            var obj=Object(); obj.Remove("id");
            obj["collision_enabled"]=false; obj["linear_velocity_m_s"]=new JArray(0,0,0); obj["angular_velocity_rad_s"]=new JArray(0,0,0);
            var root=JObject.FromObject(new {
                schema_version="1.0.0",scene_sha256=new string('a',64),fixed_steps=1,coordinate_frame="usd_world_rh_z_up_xyzw",
                state=new {
                    robot=new { joint_names=Names,joint_positions_rad=new double[43],joint_velocities_rad_s=new double[43],
                        root_position_m=new[]{0,0,0},root_rotation_xyzw=new[]{0,0,0,1},
                        root_linear_velocity_m_s=new[]{0,0,0},root_angular_velocity_rad_s=new[]{0,0,0} },
                    objects=new Dictionary<string,JObject> { ["card"]=obj },environment=new { materials=new{},lights=new{} },
                    frames=Enumerable.Range(0,3).ToDictionary(i=>"link"+i,i=>new { position_m=new[]{0,0,0},rotation_xyzw=new[]{0,0,0,1} })
                }
            });
            return Encoding.UTF8.GetBytes(root.ToString(Formatting.None)+"\n");
        }
        [Test]
        public void ParserReadsAllJointsAndPublicObjects()
        {
            var frame=Frame();
            Assert.That(frame.Joints.Count,Is.EqualTo(43)); Assert.That(frame.Objects.Single().Id,Is.EqualTo("card"));
        }
        [TestCase("trial_id")][TestCase("target")][TestCase("condition")][TestCase("schedule")][TestCase("command")][TestCase("scan_result")]
        public void AnswerFieldsRejectedAtEveryLevel(string field)
        {
            foreach(int level in new[]{0,1,2})
            {
                var raw=Raw(); var dest=level==0?raw:level==1?(JObject)raw["objects"][0]:(JObject)raw["objects"][0]["state"];
                dest[field]="synthetic";
                Assert.Throws<StateFault>(()=>StateParser.Parse(raw.ToString(Formatting.None),Registry()));
            }
        }
        [Test]
        public void DuplicateKeysNonfiniteAndUnknownStationFail()
        {
            Assert.Throws<StateFault>(()=>StateParser.Parse("{\"version\":2,\"version\":2}",Registry()));
            var raw=Raw(); raw["station_id"]="station-02";
            Assert.Throws<StateFault>(()=>StateParser.Parse(raw.ToString(Formatting.None),Registry()));
            raw=Raw(); raw["joint_positions"][0]=double.NaN;
            Assert.Throws<StateFault>(()=>StateParser.Parse(raw.ToString(Formatting.None),Registry()));
            raw=Raw(); raw["joint_names"]=new JArray(Names.Reverse());
            Assert.Throws<StateFault>(()=>StateParser.Parse(raw.ToString(Formatting.None),Registry()));
        }
        [Test]
        public void InterpolationDoesNotRevealFutureDiscreteState()
        {
            var mid=SceneFrame.Interpolate(Frame(0,0,0,0),Frame(1,.1,1,1),.5);
            Assert.That(mid.Objects[0].Position.x,Is.EqualTo(.5f).Within(1e-6));
            Assert.That((int)mid.Objects[0].VisualState["card_face"],Is.Zero);
            var copy=mid.Objects[0].VisualState; copy["card_face"]=99;
            Assert.That((int)mid.Objects[0].VisualState["card_face"],Is.Zero);
        }
        [Test]
        public void CardReleaseRepresentationDoesNotAddAVisibleTurn()
        {
            SceneFrame Make(Quaternion q,int face) => new SceneFrame(new string('a',32),0,0,0,0,"synthetic",new double[43],
                new[]{new SceneObject("card",Vector3.zero,q,true,true,new JObject { ["card_face"]=face })});
            var turned=Quaternion.AngleAxis(180,Vector3.right);
            var middle=SceneFrame.Interpolate(Make(turned,0),Make(Quaternion.identity,1),.5);
            Assert.That((int)middle.Objects[0].VisualState["card_face"],Is.Zero);
            Assert.That(Quaternion.Angle(middle.Objects[0].Rotation,turned),Is.LessThan(.001f));
        }
        [TestCase(.2,0)][TestCase(.3,1)][TestCase(2,1)]
        public void InjectedGapProducesOneFaultAndFullGapDuration(double gap,int expected)
        {
            var source=new LiveIsaacSource(0,0); var events=new List<SourceEvent>(); source.Event+=events.Add;
            source.Receive(Frame(),0,0); source.Render(0);
            for(double now=1.0/90;now<gap;now+=1.0/90) source.Render(now);
            source.Receive(Frame(1,gap),gap,gap); source.Render(gap);
            Assert.That(events.Count(e=>e.Code=="STATE_STALE"),Is.EqualTo(expected));
            if(expected==1) Assert.That(events.Single(e=>e.Code=="STATE_RECOVERED").DurationSeconds,Is.EqualTo(gap).Within(1.0/90));
        }
        [Test]
        public void StaleSourceHoldsLastRenderedPoseAndRejectsReplay()
        {
            var source=new LiveIsaacSource(0,0);
            source.Receive(Frame(),0,0); var held=source.Render(0);
            Assert.That(source.Render(.3),Is.SameAs(held));
            Assert.That(source.Receive(Frame(),.31,.31),Is.False);
            Assert.That(source.Stale,Is.True);
        }
        [Test]
        public void AppliedObservationArrivalRemainsLatestAcceptedSampleNotInterpolationOrReplay()
        {
            var source=new LiveIsaacSource(0,.1);
            Assert.That(source.Latest,Is.Null);
            Assert.That(source.Receive(Frame(0,0),0,0),Is.True);
            Assert.That(source.Receive(Frame(1,.1),.1,.1),Is.True);
            Assert.That(source.Render(.15).Sequence,Is.EqualTo(0));
            Assert.That(source.LastReceivedMonoSeconds,Is.EqualTo(.1));
            Assert.That(source.Receive(Frame(1,.1),.2,.2),Is.False);
            Assert.That(source.LastReceivedMonoSeconds,Is.EqualTo(.1));
            source.Render(.36);
            Assert.That(source.Stale,Is.True);
            Assert.That(source.LastReceivedMonoSeconds,Is.EqualTo(.1));
        }
        [Test]
        public void ResetNeedsClockEvidenceAndBothLatestAndRenderedNeutral()
        {
            var source=new LiveIsaacSource(0,0);
            source.Receive(Frame(),0,0);
            Assert.That(source.ConfirmReset(Frame(),0),Is.False);
            var clock=new SourceClock(0,5,new string('d',64));
            Assert.That(clock.Echo(0,0,0,0),Is.True);
            source=new LiveIsaacSource(0,0,clock);
            source.Receive(Frame(),0,0);
            Assert.That(source.ConfirmReset(Frame(),0),Is.True);
            Assert.That(source.ConfirmReset(Frame(),.251),Is.False);
        }
        [Test]
        public void LocalProgressObservationNeverGrantsRemoteClockQualification()
        {
            var source=new LiveIsaacSource(0,0);
            Assert.That(source.LocalProgressFresh(0),Is.False);
            Assert.That(source.Receive(Frame(),0,0),Is.True);
            Assert.That(source.LocalProgressFresh(.2),Is.True);
            Assert.That(source.SourceFresh,Is.False);
            Assert.That(source.ConfirmReset(Frame(),.2),Is.False);
            Assert.That(source.Receive(Frame(),.21,.21),Is.False);
            Assert.That(source.LocalProgressFresh(.21),Is.False);
            Assert.That(source.Receive(Frame(1,.22),.22,.22),Is.True);
            Assert.That(source.LocalProgressFresh(.22),Is.True);
            Assert.That(source.LocalProgressFresh(.471),Is.False);
            Assert.That(source.SourceFresh,Is.False);
            source.Invalidate("STATE_TRANSPORT_DISCONNECTED",.48);
            Assert.That(source.LocalProgressFresh(.48),Is.False);
        }
        [Test]
        public void WrongHashRefusedAndSnapshotEqualsNeutral()
        {
            byte[] bytes=Snapshot(); string hash=SceneRegistry.Hash(bytes);
            Assert.Throws<StateFault>(()=>new SnapshotSource(bytes,Registry(),0));
            var source=new SnapshotSource(bytes,Registry(hash),0);
            Assert.That(NeutralComparison.Matches(source.Render(0),Frame()),Is.True);
            Assert.That(source.ConfirmReset(Frame(),0),Is.True);
            bytes[bytes.Length-2]^=1;
            Assert.Throws<StateFault>(()=>new SnapshotSource(bytes,Registry(hash),0));
        }
        [TestCase("STATE_TRANSPORT_DISCONNECTED")][TestCase("STATE_RECEIVE_QUEUE_OVERFLOW")][TestCase("STATE_MALFORMED")]
        public void FaultCannotRegrantFromRetainedNeutral(string fault)
        {
            var clock=new SourceClock(0,5,new string('d',64)); clock.Echo(0,0,0,0);
            var source=new LiveIsaacSource(0,0,clock); source.Receive(Frame(),0,0);
            Assert.That(source.ConfirmReset(Frame(),0),Is.True);
            source.Invalidate(fault,.01);
            Assert.That(source.Render(.01),Is.Not.Null); // Held image remains available.
            Assert.That(source.SourceFresh,Is.False);
            Assert.That(source.ConfirmReset(Frame(),.01),Is.False);
            Assert.That(source.Receive(Frame(1,.02),.02,.02),Is.True);
            Assert.That(source.ResetConfirmed,Is.False); // New data never grants implicitly.
            Assert.That(source.ConfirmReset(Frame(),.02),Is.True);
        }
        [Test]
        public void RejectedReplayRevokesGrantUntilNewProgress()
        {
            var clock=new SourceClock(0,5,new string('d',64)); clock.Echo(0,0,0,0);
            var source=new LiveIsaacSource(0,0,clock); source.Receive(Frame(),0,0);
            Assert.That(source.ConfirmReset(Frame(),0),Is.True);
            Assert.That(source.Receive(Frame(),.01,.01),Is.False);
            Assert.That(source.ConfirmReset(Frame(),.01),Is.False);
            Assert.That(source.Receive(Frame(1,.02),.02,.02),Is.True);
            Assert.That(source.ConfirmReset(Frame(),.02),Is.True);
        }
        [Test]
        public void CorruptTrajectoryLogsFaultAndRequiresExplicitRecovery()
        {
            byte[] bytes=Snapshot(); var source=new SnapshotSource(bytes,Registry(SceneRegistry.Hash(bytes)),0);
            var events=new List<SourceEvent>(); source.Event+=events.Add;
            Assert.Throws<StateFault>(()=>source.PlayTrajectory(new byte[]{1,2,3},new string('e',64),0));
            Assert.That(events.Single().Code,Is.EqualTo("HASH_MISMATCH"));
            Assert.That(source.ConfirmReset(source.Neutral,0),Is.False);
            source.RestoreNeutral(0);
            Assert.That(source.ConfirmReset(source.Neutral,0),Is.True);
        }
        [Test]
        public void OldPublisherStampDoesNotRefreshQualifiedSource()
        {
            var clock=new SourceClock(0,5,new string('d',64)); clock.Echo(0,0,0,0);
            var source=new LiveIsaacSource(0,0,clock);
            source.Receive(Frame(),0,0); source.Render(0);
            Assert.That(source.Receive(Frame(1,.01),1,1),Is.False);
            Assert.That(source.Stale,Is.True);
            Assert.That(source.ConfirmReset(Frame(),1),Is.False);
        }
        [Test]
        public void FreshMovementRevokesEarlierResetConfirmation()
        {
            var clock=new SourceClock(0,5,new string('d',64));clock.Echo(0,0,0,0);
            var source=new LiveIsaacSource(0,0,clock);source.Receive(Frame(),0,0);
            Assert.That(source.ConfirmReset(Frame(),0),Is.True);
            source.Receive(Frame(1,.1,1,1),.1,.1);
            Assert.That(source.ResetConfirmed,Is.False);
        }
        // Fixed-step trajectory pacing and refusals: FixedStepPlaybackTests.
        [TestCase("velocity")][TestCase("fixed_steps")][TestCase("root_rotation")][TestCase("environment")][TestCase("frames")]
        public void EvenCorrectlyHashedMalformedNeutralIsRefused(string field)
        {
            var raw=JObject.Parse(Encoding.UTF8.GetString(Snapshot()));
            if(field=="velocity") raw["state"]["robot"]["joint_velocities_rad_s"][0]=.1;
            if(field=="fixed_steps") raw["fixed_steps"]=0;
            if(field=="root_rotation") raw["state"]["robot"]["root_rotation_xyzw"][3]=0;
            if(field=="environment") raw["state"]["environment"]["lights"]["bad"]="text";
            if(field=="frames") raw["state"]["frames"]=new JObject();
            var bytes=Encoding.UTF8.GetBytes(raw.ToString(Formatting.None));
            Assert.Throws<StateFault>(()=>new SnapshotSource(bytes,Registry(SceneRegistry.Hash(bytes)),0));
        }
        [TestCase(double.NaN)][TestCase(9)][TestCase(double.PositiveInfinity)]
        public void InvalidTrajectoryStartClockIsRefused(double now)
        {
            var bytes=Snapshot();var hash=SceneRegistry.Hash(bytes);var source=new SnapshotSource(bytes,Registry(hash),10);
            var data=Encoding.UTF8.GetBytes(string.Join("\n",Enumerable.Range(0,300).Select(i=>{var f=Raw(i,100+i/30d,0,0,hash);f["sim_step"]=2*(i+1);f["sim_time"]=(i+1)/30d;return f.ToString(Formatting.None);})));
            Assert.That(Assert.Throws<StateFault>(()=>source.PlayTrajectory(data,SceneRegistry.Hash(data),now)).Message,Is.EqualTo("HOST_CLOCK_REGRESSED"));
            Assert.That(source.ResetConfirmed,Is.False);
            source.RestoreNeutral(10); source.PlayTrajectory(data,SceneRegistry.Hash(data),10); // The same fixed-step stream plays from a valid clock.
        }
        [TestCase(.2,0)][TestCase(.3,1)][TestCase(2,1)]
        public void SnapshotDropInjectionReportsRealHostGap(double gap,int expected)
        {
            byte[] bytes=Snapshot(); var source=new SnapshotSource(bytes,Registry(SceneRegistry.Hash(bytes)),0);
            var events=new List<SourceEvent>(); source.Event+=events.Add;
            source.Render(0); source.InjectGap(0,gap);
            for(double now=1.0/90;now<gap;now+=1.0/90) source.Render(now);
            source.Render(gap);
            Assert.That(events.Count(e=>e.Code=="STATE_STALE"),Is.EqualTo(expected));
            if(expected==1) Assert.That(events.Single(e=>e.Code=="STATE_RECOVERED").DurationSeconds,Is.EqualTo(gap).Within(1.0/90));
        }
    }
}
