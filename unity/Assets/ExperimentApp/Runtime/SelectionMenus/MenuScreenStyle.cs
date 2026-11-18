using AcousticVocab.Theme;
using UnityEngine;
using UnityEngine.UI;

namespace AcousticVocab.SelectionMenus
{
    // Storyboard presentation of the Study B menu: the shared glass card behind
    // the menu, a static "plays twice" mark under each option card and a lock
    // badge beside the instructions for the assigned (read-only) role. The
    // assigned wording itself stays the reviewed content text in the
    // instructions; the badge adds no words. Nothing here changes a card, label,
    // collider, phase, timing or audio. The marks are identical for every option
    // and both roles, and never move. There is no countdown.
    public sealed class MenuScreenStyle
    {
        public const int PlaysPerOption = 2;
        // One fixed seed for every option: the strip is a glyph, not the option's sound.
        const int WaveformSeed = 7;
        public StoryPanel Glass { get; }
        public GameObject LockBadge { get; }
        public GameObject[] PlayMarks { get; } = new GameObject[3];

        public MenuScreenStyle(Transform root, RectTransform[] cards, RectTransform instructionsBacking)
        {
            Glass = StoryPanel.Create(root, "Menu glass", Vector2.zero, ((RectTransform)root).sizeDelta);
            for (int i = 0; i < cards.Length; i++)
            {
                var card = cards[i]; float w = card.sizeDelta.x;
                // Below the card and inside the canvas; never over the card's label.
                var mark = StoryUi.Rect(root, "Plays twice " + (i + 1), card.anchoredPosition + new Vector2(0, -card.sizeDelta.y / 2 - 17), new Vector2(w, 20));
                StoryWaveform.Create(mark, "Waveform", new Vector2(-22, 0), new Vector2(w - 84, 18), WaveformSeed, 24).SetLit(false);
                for (int play = 0; play < PlaysPerOption; play++)
                    StoryUi.Block(mark, "Play " + (play + 1), new Vector2(w / 2 - 40 + 16 * play, 0), new Vector2(8, 8), StudyTheme.TextPrimary);
                PlayMarks[i] = mark.gameObject;
            }
            // Lock badge just left of the instructions backing.
            var at = instructionsBacking.anchoredPosition + new Vector2(-instructionsBacking.sizeDelta.x / 2 - 30, 0);
            var badge = StoryUi.Rect(root, "Assigned lock badge", at, new Vector2(44, 44));
            StoryUi.Surface(badge, "Badge surface", Vector2.zero, new Vector2(44, 44), StudyTheme.PanelFill, true);
            StoryUi.Icon(badge, "Lock", StoryIcon.Lock, Vector2.zero, 30, StudyTheme.TextPrimary);
            LockBadge = badge.gameObject; LockBadge.SetActive(false);
        }

        // The only role-dependent element of the menu screen.
        public void Show(bool readOnly) => LockBadge.SetActive(readOnly);
    }
}
