using System;
using System.Collections.Generic;
using AcousticVocab.Foundation;
using AcousticVocab.ResponsePanel;
using AcousticVocab.Teaching;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Hands;

namespace AcousticVocab.Assessment
{
    // World-space administrative-free presentation. No content/schedule/answer
    // identifier is accepted by the acknowledgment surface.
    public sealed class AssessmentScreen : MonoBehaviour,IAssessmentView,IPostStudyDictionaryView
    {
        public FoundationBootstrap foundation;
        public Transform trackingSpace;
        public Font font;
        public Shader unlitShader;
        public Shader dictionaryShader;
        public ResponsePanelController inputSource;
        public event Action<string> Faulted;
        public string VisibleText => root!=null&&root.gameObject.activeInHierarchy?text.text:"";
        public bool FormsVisible=>formsVisible;
        AssessmentStages stages;
        Transform root;
        TextMesh text;
        AssessmentAcknowledgmentView acknowledgment;
        GameObject dictionaryImage;Texture2D dictionaryTexture;Material dictionaryMaterial;
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
            acknowledgment=new AssessmentAcknowledgmentView(root,font);text=acknowledgment.Text;
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
            =>AssessmentAcknowledgmentView.Fit(label,width,height);
        void ClearButtons()
        {
            foreach(var item in buttons)if(item.collider!=null){item.collider.gameObject.SetActive(false);Destroy(item.collider.gameObject);}
            foreach(var material in materials)if(material!=null)Destroy(material);materials.Clear();
            buttons.Clear();armed=false;down=false;haveTip=false;
        }
        void Show(string value,float height=.05f)
        {
            if(root==null||failed)throw new AssessmentFault("ASSESSMENT_VIEW_UNAVAILABLE");
            ClearDictionary();text.transform.localPosition=Vector3.zero;text.text=value;Fit(text,.75f,height);root.gameObject.SetActive(value.Length>0||formsVisible);
        }
        public void Neutral(){formsVisible=false;ClearButtons();ClearDictionary();if(root!=null&&!failed){text.transform.localPosition=Vector3.zero;acknowledgment.Neutral();}}
        public void Acknowledgment(){formsVisible=false;ClearButtons();ClearDictionary();if(root==null||failed)throw new AssessmentFault("ASSESSMENT_VIEW_UNAVAILABLE");text.transform.localPosition=Vector3.zero;acknowledgment.Acknowledgment();}
        public void BeginForms()
        {
            if(!foundation.Ready||failed)throw new AssessmentFault("ASSESSMENT_VIEW_UNAVAILABLE");
            if(!stages.FormsStarted)stages.BeginForms();
            ShowRating();
        }
        public void ShowInstruction(AssessmentScripts scripts,string block)
        {
            if(scripts==null||!foundation.Ready||failed)throw new AssessmentFault("ASSESSMENT_SCRIPT_UNAVAILABLE");
            stages.RequireSafeBoundary();formsVisible=false;ClearButtons();Show(scripts.For(block),.40f);
        }
        void IPostStudyDictionaryView.Show(TeachingDisplay display)
        {
            if(display==null||!stages.OptionalStarted||dictionaryShader==null||!foundation.Ready)throw new AssessmentFault("ASSESSMENT_OPTIONAL_VIEW_BLOCKED");
            stages.RequireSafeBoundary();formsVisible=false;ClearButtons();Show(display.Definition+"\n"+display.ActionWords+display.TargetWords,.13f);text.transform.localPosition=new Vector3(0,.2f,0);
            dictionaryTexture=new Texture2D(2,2,TextureFormat.RGBA32,false);
            if(!dictionaryTexture.LoadImage(display.CopyImage(),true)||dictionaryTexture.width>2048||dictionaryTexture.height>2048){ClearDictionary();throw new AssessmentFault("ASSESSMENT_OPTIONAL_IMAGE_INVALID");}
            dictionaryImage=GameObject.CreatePrimitive(PrimitiveType.Quad);dictionaryImage.name="Optional dictionary image";dictionaryImage.transform.SetParent(root,false);
            dictionaryImage.transform.localPosition=new Vector3(0,-.05f,0);dictionaryImage.transform.localScale=new Vector3(.55f,.30f,1);
            Destroy(dictionaryImage.GetComponent<Collider>());dictionaryMaterial=new Material(dictionaryShader){mainTexture=dictionaryTexture};dictionaryImage.GetComponent<MeshRenderer>().sharedMaterial=dictionaryMaterial;
        }
        void IPostStudyDictionaryView.Hide()=>Neutral();
        void ClearDictionary()
        {
            if(dictionaryImage!=null){dictionaryImage.SetActive(false);Destroy(dictionaryImage);dictionaryImage=null;}
            if(dictionaryMaterial!=null){Destroy(dictionaryMaterial);dictionaryMaterial=null;}
            if(dictionaryTexture!=null){Destroy(dictionaryTexture);dictionaryTexture=null;}
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
            Show(item.Question+"\n"+item.LowLabel+" — "+item.HighLabel,.10f);
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
        { failed=true;formsVisible=false;if(root!=null)root.gameObject.SetActive(false);Faulted?.Invoke(reason); }
        void OnApplicationFocus(bool value){focused=value;if(!value&&formsVisible)Fail("ASSESSMENT_FOCUS_LOST");}
        void OnDisable(){if(root!=null)Fail("ASSESSMENT_VIEW_DISABLED");}
        void OnDestroy(){ClearDictionary();foreach(var material in materials)if(material!=null)Destroy(material);}
    }
}
