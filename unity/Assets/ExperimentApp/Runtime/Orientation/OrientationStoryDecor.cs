using System;
using System.Collections.Generic;
using AcousticVocab.Theme;
using UnityEngine;

namespace AcousticVocab.Orientation
{
    // What the storyboard layer shows on the silent orientation screen. It is
    // derived from the flow's existing state only: no clock, no new step.
    public readonly struct OrientationStoryState
    {
        public StoryIcon Icon { get; }
        // Cards or practice requests completed in the current part, 0..1; null hides the bar.
        public float? Progress { get; }
        // Practice feedback only (orientation is the one place feedback is allowed).
        public bool ShowCorrect { get; }
        public OrientationStoryState(StoryIcon icon,float? progress,bool showCorrect) { Icon=icon;Progress=progress;ShowCorrect=showCorrect; }
        public static readonly OrientationStoryState Hidden=new OrientationStoryState(StoryIcon.None,null,false);

        public static OrientationStoryState From(OrientationFlow flow,OrientationPlan plan)
        {
            if(flow==null||plan==null)return Hidden;
            switch(flow.Stage)
            {
                case OrientationStage.Action:
                {
                    // The bar fills to this card once the robot's demonstration has completed.
                    int i=IndexOf(plan.Actions,flow.CurrentCard);
                    return new OrientationStoryState(StoryIcons.ForAction(flow.CurrentCard?.Id),i<0?(float?)null:(i+(flow.DemoComplete?1:0))/(float)plan.Actions.Count,false);
                }
                case OrientationStage.Target:
                {
                    int i=IndexOf(plan.Targets,flow.CurrentCard);
                    return new OrientationStoryState(StoryIcons.ForTarget(flow.CurrentCard?.Id),i<0?(float?)null:(i+1)/(float)plan.Targets.Count,false);
                }
                case OrientationStage.Practice:return new OrientationStoryState(StoryIcon.None,(flow.ItemNumber-1)/8f,false);
                case OrientationStage.Feedback:return new OrientationStoryState(StoryIcon.None,flow.ItemNumber/8f,flow.LastCorrect==true);
                default:return Hidden;
            }
        }
        static int IndexOf(IReadOnlyList<MeaningCard> cards,MeaningCard card)
        {for(int i=0;i<cards.Count;i++)if(ReferenceEquals(cards[i],card))return i;return -1;}
    }

    // Storyboard decoration for the silent orientation screen: a glow edge
    // behind the existing backing, the action or target icon, a progress bar
    // and a "Correct" chip after a correct practice answer. It is a world-space
    // canvas on the display, placed outside every existing label and the button,
    // and never moves, resizes or recolours them. Presentation only.
    public sealed class OrientationStoryDecor : IDisposable
    {
        // Display-local layout in canvas units (1 unit = 1 mm). The backing is
        // 720 x 280; title at y 95, body around y 15, button 320 x 45 at y -102.
        public const float IconX=-322,IconY=95,IconSize=40,BarY=-133,BarWidth=600,BarHeight=5,ChipX=268,ChipY=95;
        public static readonly Vector2 ChipSize=new Vector2(160,40);
        public readonly Canvas Canvas;
        public readonly StoryPanel Edge;
        public readonly UnityEngine.UI.RawImage Icon;
        public readonly StoryProgressBar Progress;
        public readonly StoryStatusChip Correct;

        public OrientationStoryDecor(Transform display,Font font)
        {
            // The edge panel sits just behind the backing cube (front face at
            // z -0.006, back at +0.006) so only its 8 mm rim and glow show around it.
            var back=new GameObject("Orientation story edge",typeof(RectTransform),typeof(Canvas));
            back.transform.SetParent(display,false);back.transform.localPosition=new Vector3(0,0,.0075f);back.transform.localScale=Vector3.one*.001f;
            var backCanvas=back.GetComponent<Canvas>();backCanvas.renderMode=RenderMode.WorldSpace;((RectTransform)back.transform).sizeDelta=new Vector2(760,320);
            Edge=StoryPanel.Create(back.transform,"Story card",Vector2.zero,new Vector2(736,296));
            // Front decor sits between the backing face and the labels (z -0.012).
            var front=new GameObject("Orientation story decor",typeof(RectTransform),typeof(Canvas));
            front.transform.SetParent(display,false);front.transform.localPosition=new Vector3(0,0,-.008f);front.transform.localScale=Vector3.one*.001f;
            Canvas=front.GetComponent<Canvas>();Canvas.renderMode=RenderMode.WorldSpace;((RectTransform)front.transform).sizeDelta=new Vector2(720,280);
            Icon=StoryUi.Icon(front.transform,"Card icon",StoryIcon.None,new Vector2(IconX,IconY),IconSize,StudyTheme.EdgeLit);
            Progress=StoryProgressBar.Create(front.transform,"Orientation progress",new Vector2(0,BarY),new Vector2(BarWidth,BarHeight));
            Correct=StoryStatusChip.Create(front.transform,"Practice result",new Vector2(ChipX,ChipY),ChipSize,font,24,"Correct",StoryIcon.Check);
            Present(OrientationStoryState.Hidden);
        }

        public void Present(OrientationStoryState state)
        {
            StoryUi.SetIcon(Icon,state.Icon);
            Progress.gameObject.SetActive(state.Progress.HasValue);
            if(state.Progress.HasValue)Progress.Set(state.Progress.Value);
            Correct.Show(state.ShowCorrect);
            Edge.SetLit(state.ShowCorrect);
        }

        public void Dispose()
        {
            if(Canvas!=null)Destroy(Canvas.gameObject);
            if(Edge!=null)Destroy(Edge.transform.parent.gameObject);
        }
        static void Destroy(UnityEngine.Object value){if(Application.isPlaying)UnityEngine.Object.Destroy(value);else UnityEngine.Object.DestroyImmediate(value);}
    }
}
