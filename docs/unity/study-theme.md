# Participant panel theme

`AcousticVocab.Theme.StudyTheme` (Foundation assembly) holds the visual tokens for
participant-facing panels. The look is deep navy glass with a soft teal edge, white
sans-serif text and teal selection. The theme only covers colour and text style.

## Rules

- **Geometry is out of scope.** The theme never sets position, size, scale, viewing
  angle, font or glyph height. Label size stays with the reviewed panel and view
  geometry (#49, #74). A test fails if a geometry or size member is added to the theme.
- **Surfaces behind text are opaque.** Text-bearing surfaces have alpha 1, so the
  workcell behind a panel never changes text contrast. The "glass" effect comes from
  the colour and the edge only; there is no translucency or blur.
- **Text contrast is AAA.** Text stays `Color.white`. Every surface that carries text
  keeps at least 7:1 contrast with it (WCAG 2.x relative luminance), including selected
  cards and lit lesson labels.
- **The edge is a hairline.** It is a UI `Outline` drawn 1.5 canvas units (1.5 mm on
  the 0.001-scaled world canvases) outside the backing. It does not change any
  `RectTransform` and does not intercept input.

## Tokens

| Token | sRGB | Use |
| --- | --- | --- |
| `PanelFill` | `#0D2233` | Text backings, orientation panel |
| `EdgeIdle` | `#2A8C86` | Edge of an idle backing or card |
| `EdgeLit` | `#3DD9C5` | Edge of a selected card or lit label; pointer |
| `SurfaceEnabled` | `#133247` | Enabled button or card |
| `SurfaceDisabled` | `#0A1824` | Disabled button |
| `SurfaceSelected` | `#0B6158` | Selected card |
| `HighlightFill` | `#08473F` | Lesson label while its motif plays (aligned lessons only) |
| `TextPrimary` | `#FFFFFF` | All participant text, `FontStyle.Normal` |

## Where it applies

| View | What changes |
| --- | --- |
| Teaching (lessons and grammar) | The backings use `PanelFill` and the idle edge. In aligned lessons, the label whose motif is playing gets `HighlightFill` and the lit edge; its text stays white, where the earlier version turned the text yellow. Static lessons never light. |
| Study B menus | Cards use `SurfaceEnabled`. The selected card uses `SurfaceSelected` with the lit edge. Both roles see the same styling, and the read-only role never shows a selection during the choice window (existing rule). |
| Silent orientation | The panel and its buttons use the theme colours. |
| Assessment screens and the neutral "Response recorded" acknowledgment | Button and text colours come from the theme. The acknowledgment stays identical for every outcome. |

The response panel (`ResponsePanelController`) still uses its earlier colours. Three
open pull requests (#212, #230 and #234) change that file, so it will adopt these
tokens in a follow-up once they merge. That follow-up changes colours only, never
geometry.

There is no countdown and no playing indicator in this theme. A visible Study B choice
countdown is an open decision. If it is ever added, it will sit behind a station flag,
be off by default, and look identical for both roles.

## Storyboard panel layer

The components below (also `AcousticVocab.Theme`, Foundation assembly, which now
references uGUI) build the storyboard look from uGUI primitives on the existing
world-space canvases: a navy glass card with a teal edge and soft glow, step and
status chips, banners, a progress bar, a decorative waveform with a playhead, and
simple line icons. They are presentation only.

- **No timing, order, scoring or logging.** No component owns a clock, schedules
  anything or reads study content. Callers pass text, state and fractions in.
- **Existing labels keep their geometry.** Components are added next to existing
  labels and never change their `RectTransform`, font, font size or scale. A
  `StoryPanel` is inserted as the first sibling so it renders behind everything.
- **Text sits on opaque surfaces with at least 7:1 contrast.** Chip, banner and
  label text is white on `PanelFill`, `SurfaceEnabled` or `SurfaceSelected`.
  Glow, waveform bars, tracks and playheads are decoration and never sit behind text.
- **No image assets.** Icons and rounded/glow sprites are generated at run time
  into small textures. There is no icon font and no file from the storyboard.
- **No input.** Every graphic has `raycastTarget = false`.

| Type | Use |
| --- | --- |
| `StoryPanel.Create(parent, name, position, size)` | Glass card (`PanelGlass` body, `EdgeIdle` edge, three `GlowSoft` layers outside). `SetLit(bool)` brightens edge and glow. |
| `StoryStepChips.Create(parent, name, position, font, fontSize, chipWidth, steps...)` | Step chips joined by connectors. `Set(i)`: earlier steps show a check, step `i` is lit, later steps idle; `-1` lights none. |
| `StoryStatusChip.Create(parent, name, position, size, font, fontSize, text, icon, lit)` | Pill with a leading icon, e.g. check and "Correct". `Show(bool)`, `Set(text, icon)`. |
| `StoryBanner.Create(parent, name, position, size, font, fontSize, text, locked)` | One-line banner with an optional lock badge. `Set(text, locked)`. |
| `StoryProgressBar.Create(parent, name, position, size)` | Track and fill. `Set(fraction)` (clamped; NaN is 0). |
| `StoryWaveform.Create(parent, name, position, size, seed, count)` | Bars from a fixed seed (never from audio). `SetPlayhead(float?)`: `null` hides the playhead; bars left of it are lit. `SetLit(bool)`: all bars lit or idle, no playhead. |
| `StoryIcons.Texture(icon)`, `ForAction(id)`, `ForTarget(id)` | Cached line icons: speaker, tag, box, tray, lock, check, ring with plus or minus, card, arrow, scan. |
| `StoryUi.Surface/Label/PlainText/Icon/Block/Round/Style` | Shared helpers (opaque rounded surface with edge, label on its own surface named `<name> surface`, icon, plain block). The ` surface` suffix keeps them apart from the ` backing` objects that `PresentationTextBacking` adds to existing labels. |

New tokens: `PanelGlass` `#081825` (card body, opaque), `GlowSoft` (`EdgeLit` at
alpha 0x55, glow only), `WaveIdle` `#2F6B73`, `WaveLit` (= `EdgeLit`), `Track` `#163A4A`.

### Where the storyboard layer applies (frames 2-7)

| Screen | What is added | What stays as before |
| --- | --- | --- |
| Silent orientation (action and target cards) | Glow edge behind the existing backing; the card's action or target icon left of the title; a thin progress bar under the button (cards seen in this part; an action card counts once its demonstration has completed). | Title, body, button, their sizes and the flow. |
| Orientation practice feedback | A "Correct" chip with a check, shown only after a correct practice answer (orientation is the only place with feedback). | The title, the expected-answer text and the response panel. |
| Grammar explanation ("roles" step) | A decorative row in the image area (hidden in this step): speaker, waveform glyph, "pause", waveform glyph. The glyphs come from a fixed seed, never from the explanation audio. | The "Action"/"Target" labels and the audio sequence. |
| Lessons | Glass card behind the existing labels; motif strips with a speaker below the feedback line (only under a label that has text); step chips "Listen", "Your turn", "Check" above the definition on a separate canvas 2 mm in front. During the try the meaning canvas stays hidden as before and only the chips and a fixed prompt ("Which action was that?", "Which target was that?", "Which target and action was that?") show. | Label text, positions, sizes, 27-unit type, picture, feedback text and every timeline event. |
| Aligned lessons only | While a label is lit, a playhead moves over that label's strip. Its position comes from `LessonTimeline.PlayheadAt(now)`, a read-only function of the same onsets and sample counts that drive the highlight; it is null whenever no label is lit. | The highlight itself and every record. Static and atomic lessons never show a playhead. |

The step chips and playhead reach the view through the optional `ILessonStoryView`
interface, called by the lesson content right after the existing Show/Hide/Highlight
calls and on the existing tick. A view that does not implement it is unaffected.

### Review captures

`AcousticVocab.Theme.Editor.StoryboardCapture.CaptureAll` (editor only) builds each
screen above with the real view code in a scratch scene, renders it with one camera
and writes `frame-02-*.png` ... `frame-07-*.png` plus `capture.json` (which backdrop
was used) to the directory in `AV_STORYBOARD_CAPTURE_DIR`. It refuses to run without
that variable or with a directory under `Assets/`, and never saves a scene. It needs
a GPU display (no `-nographics`). When the workcell and robot description can be
built (`G1_DESCRIPTION_JSON` set) the panels are drawn in front of the workcell;
otherwise on a neutral floor. Lesson text is example text and the lesson picture is
a procedural placeholder. Captures are for review only and are not committed.
