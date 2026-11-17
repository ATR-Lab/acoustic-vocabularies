using System;
using System.Collections.Generic;
using AcousticVocab.ResponsePanel;
using AcousticVocab.Workcell;
using UnityEngine;

namespace AcousticVocab.Orientation
{
    // Marks the object a target card names with a pointer above it.
    // Orientation only: the pointer is a child of the orientation screen, so it is
    // hidden whenever that screen is, including after the handoff to protected blocks.
    // It reads the workcell and never writes it, so the neutral scene does not move.
    // Internal to this assembly: assessment and session code cannot reach it.
    internal sealed class OrientationTargetMarker : IDisposable
    {
        internal static readonly Color Colour=new Color(1f,.72f,.1f);
        const float Gap=.02f,Height=.04f,Width=.03f;
        readonly WorkcellRegistry workcell;
        readonly Dictionary<string,ObjectBinding> objects=new Dictionary<string,ObjectBinding>(StringComparer.Ordinal);
        readonly Mesh mesh;readonly Material material;
        internal Transform Pointer { get; }
        internal string MarkedTarget { get; private set; }
        internal OrientationTargetMarker(WorkcellRegistry workcell,Transform screen,Shader shader)
        {
            if(workcell==null||screen==null||shader==null) throw new OrientationFault("ORIENTATION_TARGET_MARKER");
            this.workcell=workcell;
            foreach(string target in PublicCommands.Targets)
            {
                if(!workcell.TryGetObject(ObjectId(target),out var binding)||binding.root==null||binding.kind!=(PublicCommands.Family(target)==0?"tray":"container"))
                    throw new OrientationFault("ORIENTATION_TARGET_MARKER");
                objects.Add(target,binding);
            }
            mesh=Pyramid();material=new Material(shader){color=Colour};
            var pointer=new GameObject("Orientation target marker");pointer.SetActive(false);pointer.transform.SetParent(screen,false);
            pointer.AddComponent<MeshFilter>().sharedMesh=mesh;pointer.AddComponent<MeshRenderer>().sharedMaterial=material;Pointer=pointer.transform;
        }
        // Targets A-D are trays and E-H are containers, as in the command protocol.
        internal static string ObjectId(string target)=>PublicCommands.Family(target) switch
        { 0=>"tray_"+target, 1=>"container_"+target, _=>throw new OrientationFault("ORIENTATION_TARGET_MARKER") };
        // Shown on target cards only; hidden on every other stage, on a fault and from the first practice item on.
        internal void Present(OrientationFlow flow)=>Show(flow!=null&&flow.Stage==OrientationStage.Target?flow.CurrentCard.Id:null);
        internal void Show(string target)
        {
            if(target==MarkedTarget)return;
            Pointer.gameObject.SetActive(false);MarkedTarget=null;
            if(target==null)return;
            if(!objects.TryGetValue(target,out var binding)||!Extent(binding,out var bounds)) throw new OrientationFault("ORIENTATION_TARGET_MARKER");
            var frame=workcell.transform;
            Pointer.SetPositionAndRotation(frame.TransformPoint(new Vector3(bounds.center.x,bounds.max.y+Gap,bounds.center.z)),frame.rotation);
            Pointer.gameObject.SetActive(true);MarkedTarget=target;
        }
        // The object and its parts (for example container_E/lid) in the workcell frame.
        // Mesh bounds, so the result does not depend on whether the scene is rendering.
        bool Extent(ObjectBinding target,out Bounds bounds)
        {
            bounds=default;bool any=false;
            if(!target.root.gameObject.activeSelf)return false;
            var toFrame=workcell.transform.worldToLocalMatrix;
            foreach(var item in workcell.objects)
            {
                if(item.root==null||!item.root.gameObject.activeSelf||(item.id!=target.id&&!item.id.StartsWith(target.id+"/",StringComparison.Ordinal)))continue;
                foreach(var filter in item.root.GetComponentsInChildren<MeshFilter>(true))
                {
                    if(filter.sharedMesh==null)continue;
                    var local=filter.sharedMesh.bounds;var matrix=toFrame*filter.transform.localToWorldMatrix;
                    for(int corner=0;corner<8;corner++)
                    {
                        var point=matrix.MultiplyPoint3x4(local.center+Vector3.Scale(local.extents,new Vector3((corner&1)==0?-1:1,(corner&2)==0?-1:1,(corner&4)==0?-1:1)));
                        if(any)bounds.Encapsulate(point);else { bounds=new Bounds(point,Vector3.zero);any=true; }
                    }
                }
            }
            return any;
        }
        // A downward-pointing square pyramid: apex at the origin, base Height above it.
        static Mesh Pyramid()
        {
            float h=Width/2;
            var vertices=new[]{Vector3.zero,new Vector3(-h,Height,-h),new Vector3(h,Height,-h),new Vector3(h,Height,h),new Vector3(-h,Height,h)};
            var triangles=new List<int>();
            void Face(int a,int b,int c)=>triangles.AddRange(new[]{a,b,c,a,c,b}); // both windings, so no face is culled
            for(int i=0;i<4;i++)Face(0,1+i,1+(i+1)%4);
            Face(1,2,3);Face(1,3,4);
            var result=new Mesh{name="Orientation target marker"};result.vertices=vertices;result.SetTriangles(triangles,0);result.RecalculateBounds();return result;
        }
        public void Dispose()
        {
            if(Pointer!=null)Destroy(Pointer.gameObject);
            Destroy(material);Destroy(mesh);
        }
        static void Destroy(UnityEngine.Object value)
        { if(value==null)return;if(Application.isPlaying)UnityEngine.Object.Destroy(value);else UnityEngine.Object.DestroyImmediate(value); }
    }
}
