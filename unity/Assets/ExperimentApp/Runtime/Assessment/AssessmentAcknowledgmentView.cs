using System;
using UnityEngine;

namespace AcousticVocab.Assessment
{
    // This renderer cannot receive an answer or response outcome. The factory
    // owns its fixed display interval; every result uses these same pixels.
    public sealed class AssessmentAcknowledgmentView : IAssessmentView
    {
        readonly Transform root;
        public TextMesh Text { get; }
        public AssessmentAcknowledgmentView(Transform root,Font font)
        {
            if(root==null||font==null)throw new AssessmentFault("ASSESSMENT_VIEW_CONFIG");
            this.root=root;Text=new GameObject("Assessment text").AddComponent<TextMesh>();Text.transform.SetParent(root,false);
            Text.font=font;Text.GetComponent<MeshRenderer>().sharedMaterial=font.material;Text.fontSize=100;Text.characterSize=.01f;
            Text.anchor=TextAnchor.MiddleCenter;Text.alignment=TextAlignment.Center;Text.color=Color.white;Neutral();
        }
        public static void Fit(TextMesh label,float width,float height)
        {
            label.transform.localScale=Vector3.one;if(label.text.Length==0)return;
            var bounds=label.GetComponent<MeshRenderer>().localBounds.size;
            if(bounds.x<=0||bounds.y<=0)throw new AssessmentFault("ASSESSMENT_GLYPH_BOUNDS");
            label.transform.localScale=Vector3.one*Math.Min(width/bounds.x,height/bounds.y);
        }
        public void Neutral(){Text.text="";root.gameObject.SetActive(false);}
        public void Acknowledgment(){Text.text="Response recorded";Fit(Text,.75f,.05f);root.gameObject.SetActive(true);}
    }
}
