using System;
using System.Collections.Generic;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Hands;

namespace AcousticVocab.Assessment
{
    // World-space administrative-free presentation. No content/schedule/answer
    // identifier is accepted by the acknowledgment surface.
    public sealed class AssessmentScreen : MonoBehaviour,IAssessmentView
    {
        public FoundationBootstrap foundation;
        public Transform trackingSpace;
        public Font font;
        public Shader unlitShader;
        public ResponsePanelController inputSource;
        public event Action<string> Faulted;
        public string VisibleText { get; private set; }="";
        public bool FormsVisible=>formsVisible;
        AssessmentStages stages;
        Transform root;
        TextMesh text;
        readonly List<(BoxCollider collider,int value)> buttons=new List<(BoxCollider,int)>();
        readonly List<Material> materials=new List<Material>();
        readonly List<XRHandSubsystem> hands=new List<XRHandSubsystem>();
        bool formsVisible,armed,down,focused=true,failed,haveTip;
        Vector3 previousTip;

        public void Configure(AssessmentStages value)
        {
            if(root!=null||value==null||foundation==null||trackingSpace==null||font==null||unlitShader==null||foundation.Configuration==null||inputSource==null||!inputSource.InputConfigured)
                throw new AssessmentFault("ASSESSMENT_VIEW_CONFIG");
            stages=value;var pose=StationConfig.ReferencePose(foundation.Configuration);
            root=new GameObject("Assessment surface").transform;root.SetParent(foundation.presentationRoot.transform,false);
            root.SetPositionAndRotation(pose.position+pose.rotation*new Vector3(0,.05f,1.05f),pose.rotation);
            text=Label(root,"",Vector3.zero,.75f,.04f);
            Neutral();
        }
        TextMesh Label(Transform parent,string value,Vector3 position,float width,float height)
        {
            var label=new GameObject("Assessment text").AddComponent<TextMesh>();label.transform.SetParent(parent,false);label.transform.localPosition=position;
            label.font=font;label.GetComponent<MeshRenderer>().sharedMaterial=font.material;label.fontSize=100;label.characterSize=.01f;
            label.anchor=TextAnchor.MiddleCenter;label.alignment=TextAlignment.Center;label.color=Color.white;label.text=value;
            Fit(label,width,height);return label;
        }
        static void Fit(TextMesh label,float width,float height)
        {
            label.transform.localScale=Vector3.one;if(label.text.Length==0)return;
            var bounds=label.GetComponent<MeshRenderer>().localBounds.size;
            if(bounds.x<=0||bounds.y<=0)throw new AssessmentFault("ASSESSMENT_GLYPH_BOUNDS");
            label.transform.localScale=Vector3.one*Math.Min(width/bounds.x,height/bounds.y);
        }
        void ClearButtons()
        {
            foreach(var item in buttons)if(item.collider!=null){item.collider.gameObject.SetActive(false);Destroy(item.collider.gameObject);}
            foreach(var material in materials)if(material!=null)Destroy(material);materials.Clear();
            buttons.Clear();armed=false;down=false;haveTip=false;
        }
        void Show(string value)
        {
            if(root==null||failed)throw new AssessmentFault("ASSESSMENT_VIEW_UNAVAILABLE");
            VisibleText=value;text.text=value;Fit(text,.75f,.05f);root.gameObject.SetActive(value.Length>0||formsVisible);
        }
        public void Neutral(){formsVisible=false;ClearButtons();if(root!=null&&!failed)Show("");}
        public void Acknowledgment(){formsVisible=false;ClearButtons();Show("Response recorded");}
        public void BeginForms()
        {
            if(!foundation.Ready||failed)throw new AssessmentFault("ASSESSMENT_VIEW_UNAVAILABLE");
            if(!stages.FormsStarted)stages.BeginForms();
            ShowRating();
        }
        void ShowRating()
        {
            ClearButtons();formsVisible=true;
            var item=stages.CurrentRating;
            if(item==null)
            {
                if(!stages.FormsComplete)stages.CompleteForms();
                formsVisible=false;Show("Responses recorded");return;
            }
            Show(item.Question+"\n"+item.LowLabel+" — "+item.HighLabel);
            for(int value=item.Minimum;value<=item.Maximum;value++)
            {
                int i=value-item.Minimum;var button=GameObject.CreatePrimitive(PrimitiveType.Cube);button.name="Rating choice";
                button.transform.SetParent(root,false);button.transform.localPosition=new Vector3((i%6-2.5f)*.12f,-.13f-(i/6)*.1f,0);
                button.transform.localScale=new Vector3(.095f,.075f,.012f);
                var material=new Material(unlitShader){color=new Color(.075f,.085f,.095f)};materials.Add(material);button.GetComponent<MeshRenderer>().sharedMaterial=material;
                var label=Label(button.transform,value.ToString(),new Vector3(0,0,-.7f),.55f,.45f);
                buttons.Add((button.GetComponent<BoxCollider>(),value));
            }
        }
        public void SelectRating(int value)
        {
            if(!formsVisible||failed||!focused||!foundation.Ready)throw new AssessmentFault("ASSESSMENT_RATING_INPUT_BLOCKED");
            try{stages.Rate(stages.CurrentRating.Id,value);ShowRating();}
            catch{Fail("ASSESSMENT_RATING_LOG_FAILED");throw;}
        }
        void Hit(Ray ray,float distance)
        {
            int? chosen=null;float nearest=distance;
            foreach(var entry in buttons)if(entry.collider!=null&&entry.collider.Raycast(ray,out var hit,nearest)){chosen=entry.value;nearest=hit.distance;}
            if(chosen.HasValue)SelectRating(chosen.Value);
        }
        void Update()
        {
            if(!formsVisible||failed||!focused)return;
            try
            {
                if(!foundation.Ready)throw new AssessmentFault("ASSESSMENT_INTERFACE_UNAVAILABLE");
                string input=(string)foundation.Configuration["input_method"];
                if(input=="controller_ray")
                {
                    var device=InputDevices.GetDeviceAtXRNode(inputSource.UsesLeftHand?XRNode.LeftHand:XRNode.RightHand);
                    if(!device.isValid||!device.TryGetFeatureValue(CommonUsages.isTracked,out bool tracked)||!tracked||
                        !device.TryGetFeatureValue(CommonUsages.trackingState,out InputTrackingState tracking)||
                        (tracking&(InputTrackingState.Position|InputTrackingState.Rotation))!=(InputTrackingState.Position|InputTrackingState.Rotation)||
                        !device.TryGetFeatureValue(CommonUsages.devicePosition,out var position)||!Finite(position)||!device.TryGetFeatureValue(CommonUsages.deviceRotation,out var rotation)||
                        !device.TryGetFeatureValue(CommonUsages.triggerButton,out bool press))throw new AssessmentFault("ASSESSMENT_INTERFACE_UNAVAILABLE");
                    float norm=rotation.x*rotation.x+rotation.y*rotation.y+rotation.z*rotation.z+rotation.w*rotation.w;
                    Vector3 origin=trackingSpace.TransformPoint(position),direction=trackingSpace.TransformDirection(rotation*Vector3.forward);
                    if(!float.IsFinite(norm)||Mathf.Abs(norm-1)>.001f||!Finite(origin)||!Finite(direction)||direction.sqrMagnitude<.9f||direction.sqrMagnitude>1.1f)throw new AssessmentFault("ASSESSMENT_INTERFACE_UNAVAILABLE");
                    if(!press)armed=true;
                    if(press&&!down&&armed){Hit(new Ray(origin,direction),2);armed=false;}
                    down=press;
                }
                else if(input=="hand_poke")
                {
                    hands.Clear();SubsystemManager.GetSubsystems(hands);var subsystem=hands.Find(x=>x.running);
                    var hand=subsystem==null?default:inputSource.UsesLeftHand?subsystem.leftHand:subsystem.rightHand;
                    if(subsystem==null||!hand.isTracked||!hand.GetJoint(XRHandJointID.IndexTip).TryGetPose(out var pose)||!Finite(pose.position))throw new AssessmentFault("ASSESSMENT_INTERFACE_UNAVAILABLE");
                    Vector3 point=trackingSpace.TransformPoint(pose.position);if(!Finite(point))throw new AssessmentFault("ASSESSMENT_INTERFACE_UNAVAILABLE");
                    var local=root.InverseTransformPoint(point);if(local.z<-.04f)armed=true;
                    if(haveTip&&armed&&local.z>=-.006f){var delta=point-previousTip;if(delta.sqrMagnitude>0){Hit(new Ray(previousTip,delta.normalized),delta.magnitude);armed=false;}}
                    previousTip=point;haveTip=true;
                }
                else throw new AssessmentFault("ASSESSMENT_INTERFACE_UNAVAILABLE");
            }
            catch(AssessmentFault error){Fail(error.Code);}catch{Fail("ASSESSMENT_INTERFACE_UNAVAILABLE");}
        }
        static bool Finite(Vector3 value)=>float.IsFinite(value.x)&&float.IsFinite(value.y)&&float.IsFinite(value.z);
        void Fail(string reason)
        { failed=true;formsVisible=false;VisibleText="";if(root!=null)root.gameObject.SetActive(false);Faulted?.Invoke(reason); }
        void OnApplicationFocus(bool value){focused=value;if(!value&&formsVisible)Fail("ASSESSMENT_FOCUS_LOST");}
        void OnDisable(){if(root!=null)Fail("ASSESSMENT_VIEW_DISABLED");}
        void OnDestroy(){foreach(var material in materials)if(material!=null)Destroy(material);}
    }
}
