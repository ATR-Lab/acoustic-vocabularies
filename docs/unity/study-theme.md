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
