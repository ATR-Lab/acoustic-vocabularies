using AcousticVocab.Theme;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.Teaching
{
    // Keep legibility independent of the workcell behind the world-space view.
    // The existing canvas owns visibility; this adds no input or timing owner.
    public static class PresentationTextBacking
    {
        public static Image Add(Transform parent,string textName,Vector2 position,Vector2 size)
        {
            var node=new GameObject(textName+" backing",typeof(RectTransform),typeof(CanvasRenderer),typeof(Image));
            node.transform.SetParent(parent,false);
            var rect=(RectTransform)node.transform;rect.anchoredPosition=position;rect.sizeDelta=size;
            var image=node.GetComponent<Image>();image.raycastTarget=false;
            Style(image,StudyTheme.PanelFill,false);
            // All backings precede every text/image sibling, including later additions.
            node.transform.SetAsFirstSibling();
            return image;
        }

        // Colour-only theme styling for a UI surface: an opaque fill and a teal
        // edge drawn just outside its rect. The RectTransform is never changed.
        public static void Style(Image image,Color fill,bool lit)
        {
            fill.a=1f;image.color=fill;
            var edge=image.GetComponent<Outline>();
            if(edge==null)edge=image.gameObject.AddComponent<Outline>();
            edge.effectColor=StudyTheme.EdgeFor(lit);
            edge.effectDistance=new Vector2(StudyTheme.EdgeWidthCanvasUnits,-StudyTheme.EdgeWidthCanvasUnits);
            edge.useGraphicAlpha=false;
        }
    }
}
