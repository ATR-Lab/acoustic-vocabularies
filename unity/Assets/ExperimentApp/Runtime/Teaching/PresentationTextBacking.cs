using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Teaching
{
    // Keep legibility independent of the workcell behind the world-space view.
    // The existing canvas owns visibility; this adds no input or timing owner.
    public static class PresentationTextBacking
    {
        public static void Add(Transform parent,string textName,Vector2 position,Vector2 size)
        {
            var node=new GameObject(textName+" backing",typeof(RectTransform),typeof(CanvasRenderer),typeof(Image));
            node.transform.SetParent(parent,false);
            var rect=(RectTransform)node.transform;rect.anchoredPosition=position;rect.sizeDelta=size;
            var image=node.GetComponent<Image>();image.color=new Color(.055f,.07f,.095f,1f);image.raycastTarget=false;
            // All backings precede every text/image sibling, including later additions.
            node.transform.SetAsFirstSibling();
        }
    }
}
