using UnityEngine;

namespace AcousticVocab.StudyAudio
{
    // Display-only level bar on the comfort screen. It draws the relative gain
    // the level logic has already set, on the gain store's own domain (0, 1].
    // It takes no input and never sets, clamps or widens a gain; the range and
    // any cap stay with the level logic (ComfortableGainStore and the host).
    public static class CalibrationLevelView
    {
        public static float Fraction(float gain) => float.IsFinite(gain) ? Mathf.Clamp01(gain) : 0f;

        public static void Show(RectTransform fill,RectTransform knob,float gain)
        {
            float fraction=Fraction(gain);
            if(fill!=null) { fill.anchorMin=Vector2.zero; fill.anchorMax=new Vector2(fraction,1); fill.offsetMin=fill.offsetMax=Vector2.zero; }
            if(knob!=null) { knob.anchorMin=knob.anchorMax=new Vector2(fraction,.5f); knob.anchoredPosition=Vector2.zero; }
        }
    }
}
