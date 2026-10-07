using System;
using System.Collections.Generic;
using System.Linq;
using AcousticVocab.StateSources;
using AcousticVocab.Workcell;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace AcousticVocab.StateIntegration
{
    // Complete validation precedes any transform mutation on Unity's thread.
    public sealed class WorkcellStateRenderer
    {
        readonly WorkcellRegistry workcell;
        public WorkcellStateRenderer(WorkcellRegistry registry) { workcell=registry??throw new ArgumentNullException(nameof(registry)); }
        static PublicVisualState Visual(JObject state) => new PublicVisualState(
            (int?)state["card_face"],(float?)state["arrow_angle_rad"],(float?)state["lid_open_fraction"],
            (bool?)state["tag_attached"],(string)state["location"]);
        public void VerifyImportedNeutral(SceneFrame neutral)
        {
            if(workcell.ImportedLayout==null) throw new StateFault("SOURCE_IMPORTED_LAYOUT_MISSING");
            var layout=StateParser.Json(workcell.ImportedLayout.text);
            var states=((JArray)layout["objects"]).ToDictionary(x=>(string)x["id"],x=>(JObject)x["state"],StringComparer.Ordinal);
            var objects=workcell.objects.OrderBy(x=>x.id,StringComparer.Ordinal).Select(item=>
            {
                // Preserve exact semantic values from the hash-bound layout.
                // Unity's serialized float angles are a rendering conversion,
                // not a new neutral-state definition (pi/2 is not float-exact).
                var state=states[item.id];
                var q=item.neutralRotation;
                return new SceneObject(item.id,SceneCoordinates.InversePosition(item.neutralPosition),
                    new Quaternion(-q.z,q.x,-q.y,q.w),item.neutralVisible,item.neutralEnabled,state);
            });
            var imported=new SceneFrame(new string('0',32),0,0,0,0,"snapshot",workcell.joints.Select(x=>(double)x.neutralRad),objects);
            if(!NeutralComparison.Matches(neutral,imported)) throw new StateFault("SOURCE_IMPORTED_NEUTRAL_MISMATCH");
        }
        public void Apply(SceneFrame frame)
        {
            if(frame==null || frame.Joints.Count!=workcell.joints.Length || frame.Objects.Count!=workcell.objects.Length)
                throw new StateFault("RENDER_INVENTORY");
            for(int i=0;i<frame.Joints.Count;i++)
                if(!workcell.CanApplyJoint(workcell.CanonicalJointNames[i],(float)frame.Joints[i])) throw new StateFault("RENDER_JOINT");
            var seen=new HashSet<string>(StringComparer.Ordinal);
            var visuals=new PublicVisualState[frame.Objects.Count];
            for(int i=0;i<frame.Objects.Count;i++)
            {
                var item=frame.Objects[i]; visuals[i]=Visual(item.VisualState);
                if(!seen.Add(item.Id) || !workcell.CanApplyObject(item.Id,SceneCoordinates.Position(item.Position),
                    SceneCoordinates.Rotation(item.Rotation),visuals[i])) throw new StateFault("RENDER_OBJECT");
            }
            for(int i=0;i<frame.Joints.Count;i++) workcell.ApplyJoint(workcell.CanonicalJointNames[i],(float)frame.Joints[i]);
            for(int i=0;i<frame.Objects.Count;i++)
            {
                var item=frame.Objects[i]; workcell.ApplyObject(item.Id,SceneCoordinates.Position(item.Position),
                    SceneCoordinates.Rotation(item.Rotation),item.Visible,item.Enabled,visuals[i]);
            }
        }
    }
}
