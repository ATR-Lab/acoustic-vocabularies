using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using AcousticVocab.StateSources;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace AcousticVocab.Tests
{
    // Requirement trace: the state wire cannot move the robot base. Synthetic
    // registry and messages only, built like StateSourceTests.
    public sealed class RequirementTraceStateSourceTests
    {
        static readonly string[] Names=Enumerable.Range(0,43).Select(i=>"joint_"+i.ToString("00")).ToArray();
        static SceneRegistry Registry(string snapshotHash=null) => new SceneRegistry("station-01",new string('a',64),
            snapshotHash??new string('b',64),Names,new Dictionary<string,string[]> { ["card"]=new[]{"card_face"} },new[]{"card"});
        static JObject Card() => JObject.FromObject(new {
            id="card",position_m=new[]{0d,0d,0d},rotation_xyzw=new[]{0d,0d,0d,1d},visible=true,enabled=true,state=new { card_face=0 } });
        static JObject Live() => JObject.FromObject(new {
            version=2,kind="state",source_kind="live",station_id="station-01",scene_sha256=new string('a',64),reset_snapshot_sha256=new string('b',64),
            session_id=new string('c',32),seq=0,host_monotonic_ns="0",sim_time=0d,sim_step=0,joint_names=Names,joint_positions=new double[43],objects=new[]{Card()} });
        static JObject Snapshot()
        {
            var card=Card();card.Remove("id");
            card["collision_enabled"]=false;card["linear_velocity_m_s"]=new JArray(0,0,0);card["angular_velocity_rad_s"]=new JArray(0,0,0);
            return JObject.FromObject(new {
                schema_version="1.0.0",scene_sha256=new string('a',64),fixed_steps=1,coordinate_frame="usd_world_rh_z_up_xyzw",
                state=new {
                    robot=new { joint_names=Names,joint_positions_rad=new double[43],joint_velocities_rad_s=new double[43],
                        root_position_m=new[]{0,0,0},root_rotation_xyzw=new[]{0,0,0,1},
                        root_linear_velocity_m_s=new[]{0,0,0},root_angular_velocity_rad_s=new[]{0,0,0} },
                    objects=new Dictionary<string,JObject> { ["card"]=card },environment=new { materials=new{},lights=new{} },
                    frames=Enumerable.Range(0,3).ToDictionary(i=>"link"+i,i=>new { position_m=new[]{0,0,0},rotation_xyzw=new[]{0,0,0,1} })
                }
            });
        }
        static byte[] Bytes(JObject value) => Encoding.UTF8.GetBytes(value.ToString(Formatting.None)+"\n");

        [TestCase("root_position_m")][TestCase("root_rotation_xyzw")][TestCase("root_linear_velocity_m_s")][TestCase("base_pose")]
        [Category("REQ_APP_01"), NUnit.Framework.Property("Requirement","REQ-APP-01")]
        public void LiveStateWireCannotCarryARobotBasePose(string field)
        {
            Assert.That(StateParser.Parse(Live().ToString(Formatting.None),Registry()).Joints.Count,Is.EqualTo(43),"positive control");
            var raw=Live();raw[field]=new JArray(1,0,0);
            Assert.Throws<StateFault>(()=>StateParser.Parse(raw.ToString(Formatting.None),Registry()));
        }

        [TestCase("root_linear_velocity_m_s",0)][TestCase("root_linear_velocity_m_s",2)][TestCase("root_angular_velocity_rad_s",2)]
        [Category("REQ_APP_01"), NUnit.Framework.Property("Requirement","REQ-APP-01")]
        public void NeutralSnapshotWithAMovingRobotBaseIsRefused(string field,int axis)
        {
            byte[] good=Bytes(Snapshot());Assert.That(new SnapshotSource(good,Registry(SceneRegistry.Hash(good)),0),Is.Not.Null,"positive control");
            var raw=Snapshot();raw["state"]["robot"][field][axis]=.05;byte[] moving=Bytes(raw);
            Assert.Throws<StateFault>(()=>new SnapshotSource(moving,Registry(SceneRegistry.Hash(moving)),0));
        }
    }
}
