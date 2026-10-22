using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using AcousticVocab.Foundation;
using AcousticVocab.SelectionMenus;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace AcousticVocab.SessionIntegration.Tests
{
    // #149 input-coordinate consistency for the real menu host at the configured yawed observer reference.
    // XR controller and hand-joint reads are not reachable in EditMode, so Controller/Poke below repeat
    // MenuSessionHost.Controller/Hand's tracking-space conversion and poke thresholds verbatim; every
    // selection still goes through the host's own CreateView colliders, private Hit guards and Chosen event.
    public sealed class JoinedMenuInputTests
    {
        const BindingFlags Private=BindingFlags.Instance|BindingFlags.NonPublic;
        static readonly Vector3 ReferencePosition=new Vector3(0,1.5f,1.45f);
        static readonly Quaternion ReferenceRotation=Quaternion.Euler(0,180,0);
        // Tracked head in runtime tracking space: translated, yawed and pitched, so the resolved origin is far from identity.
        static readonly Pose Head=new Pose(new Vector3(.13f,1.21f,-.09f),Quaternion.Euler(12,-40,0));
        static readonly Vector3 RightController=new Vector3(.21f,-.32f,.28f);

        // A point given in the observer reference frame (metres), expressed in tracking space without
        // reading any world transform: the restored origin maps head heading onto the reference heading.
        static Vector3 InTracking(Vector3 observerOffset)
        {
            var forward=Head.rotation*Vector3.forward;forward.y=0;
            return Head.position+Quaternion.LookRotation(forward,Vector3.up)*observerOffset;
        }
        // MenuSessionHost.CreateView: canvas .02 m up and 1.05 m ahead, cards at ((i-1)*270,-165) canvas units of 1 mm.
        static Vector3 CardCentre(int index)=>new Vector3((index-1)*.27f,.02f-.165f,1.05f);

        [Test]
        public void SyntheticRigRestoresTheConfiguredYawedReferenceThroughANonidentityTrackingSpace()
        {
            using(var rig=new Rig())
            {
                Assert.That(Vector3.Distance(rig.Camera.transform.position,ReferencePosition),Is.LessThan(.00001f));
                var heading=rig.Camera.transform.forward;heading.y=0;
                Assert.That(Vector3.Angle(heading,ReferenceRotation*Vector3.forward),Is.LessThan(.01f));
                Assert.That(Quaternion.Angle(rig.TrackingSpace.rotation,Quaternion.identity),Is.GreaterThan(90),"Tracking space must not hide local/world mixing");
                Assert.That(Vector3.Distance(rig.TrackingSpace.localPosition,rig.TrackingSpace.position),Is.GreaterThan(1),"Posed parent must not hide local/world mixing");
                Assert.That(rig.Host.foundation.observerCamera.transform.parent,Is.SameAs(rig.TrackingSpace));
                Assert.That(Quaternion.Angle(rig.Canvas.transform.rotation,ReferenceRotation),Is.LessThan(.001f));
                for(int i=0;i<3;i++)
                    Assert.That(Vector3.Distance(rig.TrackingSpace.TransformPoint(InTracking(CardCentre(i))),rig.Cards[i].transform.TransformPoint(rig.Cards[i].center)),Is.LessThan(.00002f),"Card "+(i+1)+" is not where the observer reference places it");
            }
        }

        [TestCase(0)][TestCase(1)][TestCase(2)]
        public void ControllerRayFromTrackingSpaceSelectsTheCardAtWhichItAims(int index)
        {
            using(var rig=new Rig())
            {
                var origin=InTracking(RightController);
                // Centre plus four points inside the 245 x 130 mm card face.
                foreach(var offset in new[]{Vector3.zero,new Vector3(.11f,.055f,0),new Vector3(-.11f,.055f,0),new Vector3(.11f,-.055f,0),new Vector3(-.11f,-.055f,0)})
                {
                    rig.Chosen.Clear();
                    rig.Controller(origin,Quaternion.LookRotation(InTracking(CardCentre(index)+offset)-origin,Vector3.up));
                    Assert.That(rig.Chosen,Is.EqualTo(new[]{index+1}),"aim offset "+offset);
                }
            }
        }

        [Test]
        public void ControllerRaysOffTheCardsOrWithoutTrackingSpaceSelectNothing()
        {
            using(var rig=new Rig())
            {
                var origin=InTracking(RightController);
                var misses=new[]
                {
                    new Vector3(-.135f,-.145f,1.05f),new Vector3(.135f,-.145f,1.05f), // gaps between cards
                    new Vector3(0,.04f,1.05f),new Vector3(0,-.145f-.08f,1.05f),       // meaning image above, below the row
                    new Vector3(-.43f,-.145f,1.05f),new Vector3(.43f,-.145f,1.05f)    // beyond the outer cards
                };
                foreach(var point in misses)rig.Controller(origin,Quaternion.LookRotation(InTracking(point)-origin,Vector3.up));
                rig.Controller(origin,Quaternion.LookRotation(origin-InTracking(CardCentre(1)),Vector3.up)); // pointing away
                Assert.That(rig.Chosen,Is.Empty);
                for(int i=0;i<3;i++)
                {
                    // Treating the tracking-space pose as a world pose must not happen to select a card.
                    var rotation=Quaternion.LookRotation(InTracking(CardCentre(i))-origin,Vector3.up);
                    rig.Hit(new Ray(origin,rotation*Vector3.forward),2);
                }
                Assert.That(rig.Chosen,Is.Empty,"The tracking-space conversion must be load-bearing for this rig");
                rig.Controller(origin,Quaternion.LookRotation(InTracking(CardCentre(1))-origin,Vector3.up));
                Assert.That(rig.Chosen,Is.EqualTo(new[]{2}));
            }
        }

        [Test]
        public void ConcealedOrNonChoiceMenuIgnoresAValidRay()
        {
            using(var rig=new Rig())
            {
                var origin=InTracking(RightController);var aim=Quaternion.LookRotation(InTracking(CardCentre(0))-origin,Vector3.up);
                rig.Presentation.SetActive(false);rig.Controller(origin,aim);
                rig.Presentation.SetActive(true);rig.Set("choosing",false);rig.Controller(origin,aim);
                rig.Set("choosing",true);rig.Set("readOnly",true);rig.Controller(origin,aim);
                Assert.That(rig.Chosen,Is.Empty);
                rig.Set("readOnly",false);rig.Controller(origin,aim);
                Assert.That(rig.Chosen,Is.EqualTo(new[]{1}));
            }
        }

        [TestCase(0)][TestCase(1)][TestCase(2)]
        public void PokeAlongTheYawedViewArmsAndSelectsAtCanvasLocalThresholds(int index)
        {
            using(var rig=new Rig())
            {
                var previousWorld=Vector3.zero;
                // Millimetres in front of the card face along the observer's view, approaching then passing through.
                var steps=new[]{80f,50f,30f,15f,5f,-5f,-20f};
                for(int s=0;s<steps.Length;s++)
                {
                    var tip=InTracking(CardCentre(index)-new Vector3(0,0,steps[s]*.001f));
                    float localZ=rig.Poke(tip);
                    Assert.That(localZ,Is.EqualTo(-steps[s]).Within(.05f),"Canvas-local z must be millimetres along the reference view");
                    var world=rig.TrackingSpace.TransformPoint(tip);
                    if(s>0)Assert.That(world.z,Is.LessThan(previousWorld.z),"At yaw 180 approaching the card decreases world z while local z increases");
                    previousWorld=world;
                    // Armed at -80 (< -35); -15 is short of -7.5; -5 crosses the collider front face and selects once.
                    Assert.That(rig.Chosen,Is.EqualTo(steps[s]>7.5f?Array.Empty<int>():new[]{index+1}),"after step "+steps[s]+" mm");
                }
            }
        }

        [Test]
        public void PokesThatMissDoNotArmOrSelect()
        {
            using(var rig=new Rig())
            {
                // Gap between cards 1 and 2: the press threshold fires but no collider is crossed.
                foreach(float mm in new[]{80f,15f,5f})rig.Poke(InTracking(new Vector3(-.135f,-.145f,1.05f-mm*.001f)));
                Assert.That(rig.Chosen,Is.Empty);
                // Hovering between the arm and press thresholds never presses.
                rig.Reset();foreach(float mm in new[]{80f,20f,10f,8f,20f})rig.Poke(InTracking(CardCentre(0)-new Vector3(0,0,mm*.001f)));
                Assert.That(rig.Chosen,Is.Empty);
                // From behind the canvas local z is positive, so the poke never arms.
                rig.Reset();
                foreach(float mm in new[]{80f,40f,5f,-5f})Assert.That(rig.Poke(InTracking(CardCentre(2)+new Vector3(0,0,mm*.001f))),Is.GreaterThan(-7.5f));
                Assert.That(rig.Chosen,Is.Empty);
                // An unarmed first sample inside the threshold cannot press either.
                rig.Reset();rig.Poke(InTracking(CardCentre(1)-new Vector3(0,0,.005f)));rig.Poke(InTracking(CardCentre(1)+new Vector3(0,0,.005f)));
                Assert.That(rig.Chosen,Is.Empty);
            }
        }

        sealed class Rig:IDisposable
        {
            readonly GameObject holder,lease,rigParent;
            public readonly GameObject Presentation;
            public readonly MenuSessionHost Host;
            public readonly Transform TrackingSpace;
            public readonly Camera Camera;
            public readonly Canvas Canvas;
            public readonly BoxCollider[] Cards=new BoxCollider[3];
            public readonly List<int> Chosen=new List<int>();
            bool armed,haveTip;Vector3 previousTip;

            public Rig()
            {
                holder=new GameObject("Inactive synthetic foundation");holder.SetActive(false);
                Presentation=new GameObject("Synthetic gated presentation");lease=new GameObject("Synthetic module lease");
                rigParent=new GameObject("Posed rig parent");rigParent.transform.SetPositionAndRotation(new Vector3(-1.7f,.4f,2.6f),Quaternion.Euler(0,-63,0));
                TrackingSpace=new GameObject("Synthetic tracking space").transform;TrackingSpace.SetParent(rigParent.transform,false);
                Camera=new GameObject("Observer camera").AddComponent<Camera>();Camera.transform.SetParent(TrackingSpace,false);
                Camera.transform.SetLocalPositionAndRotation(Head.position,Head.rotation);
                var foundation=holder.AddComponent<FoundationBootstrap>();foundation.observerCamera=Camera;foundation.seatedOrigin=TrackingSpace;
                var config=new JObject{["observer_reference"]=new JObject{["position_m"]=new JArray(ReferencePosition.x,ReferencePosition.y,ReferencePosition.z),["rotation_xyzw"]=new JArray(ReferenceRotation.x,ReferenceRotation.y,ReferenceRotation.z,ReferenceRotation.w)}};
                typeof(FoundationBootstrap).GetField("configuration",Private).SetValue(foundation,config);
                // Same restoration as FoundationBootstrap.RestoreAtSafeBoundary.
                var headInOrigin=new Pose(TrackingSpace.InverseTransformPoint(Camera.transform.position),Quaternion.Inverse(TrackingSpace.rotation)*Camera.transform.rotation);
                var origin=ObserverReference.ResolveOrigin(StationConfig.ReferencePose(config),headInOrigin);
                TrackingSpace.SetPositionAndRotation(origin.position,origin.rotation);
                Presentation.transform.SetPositionAndRotation(new Vector3(2,0,-3),Quaternion.Euler(0,37,0));foundation.presentationRoot=Presentation;
                Host=lease.AddComponent<MenuSessionHost>();Host.foundation=foundation;Host.presentationParent=Presentation.transform;Host.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                typeof(MenuSessionHost).GetMethod("CreateView",Private).Invoke(Host,null);
                Canvas=Presentation.GetComponentInChildren<Canvas>(true);
                for(int i=0;i<3;i++)Cards[i]=Canvas.transform.Find("Candidate "+(i+1)).GetComponent<BoxCollider>();
                // Enter the choice phase without a catalog or audio. Hit's installed/focus/choice/read-only/visibility guards stay live.
                Set("factory",FormatterServices.GetUninitializedObject(typeof(MenuContentFactory)));Set("choosing",true);Set("visibleOwner","synthetic-owner");
                Canvas.gameObject.SetActive(true);
                Assert.That(Host.Installed,Is.True,"Synthetic choice phase must pass the real installed guard");
                Host.Chosen+=Chosen.Add;
            }
            public void Set(string field,object value)=>typeof(MenuSessionHost).GetField(field,Private).SetValue(Host,value);
            public void Hit(Ray ray,float distance)=>typeof(MenuSessionHost).GetMethod("Hit",Private).Invoke(Host,new object[]{ray,distance});
            // MenuSessionHost.Controller after a tracked trigger edge.
            public void Controller(Vector3 position,Quaternion rotation)
            {
                var trackingSpace=Host.foundation.observerCamera.transform.parent;
                Hit(new Ray(trackingSpace.TransformPoint(position),trackingSpace.TransformDirection(rotation*Vector3.forward)),2);
            }
            // MenuSessionHost.Hand for one tracked index-tip sample; returns canvas-local z.
            public float Poke(Vector3 tip)
            {
                Vector3 point=Host.foundation.observerCamera.transform.parent.TransformPoint(tip);Vector3 local=Canvas.transform.InverseTransformPoint(point);
                if(local.z< -35)armed=true;
                if(haveTip&&armed&&local.z>= -7.5f){var delta=point-previousTip;if(delta.sqrMagnitude>0)Hit(new Ray(previousTip,delta.normalized),delta.magnitude);armed=false;}
                previousTip=point;haveTip=true;return local.z;
            }
            public void Reset(){armed=haveTip=false;Chosen.Clear();}
            public void Dispose()
            {
                Set("factory",null);
                UnityEngine.Object.DestroyImmediate(Presentation);UnityEngine.Object.DestroyImmediate(lease);UnityEngine.Object.DestroyImmediate(holder);UnityEngine.Object.DestroyImmediate(rigParent);
            }
        }
    }
}
