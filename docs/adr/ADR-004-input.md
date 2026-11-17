# ADR-004 — Seated input and legibility

Status: Accepted at G1 (#51) — controller ray (`controller_ray`); text angle is
chosen by the measured ladder below before the engineering pilot

## Context

WBS O5.1.8 / #50 and #49. The panel has eight targets, four family-filtered
actions and Commit, with no preselection. The study procedure confirms a 12 s
response window in a 14 s full-message slot and a 7 s window in a 9 s atomic slot.

## Options

Controller ray with trigger edge, or XR Hands index-tip poke with withdrawal
before another activation. Both share identical fixed seated panel geometry.

## Measurements

The [input analysis](../../spikes/O5.1.7/input_analysis.py) and
[runbook](../spikes/O5.1.7-runbook.md), merged in
[PR98](https://github.com/ATR-Lab/acoustic-vocabularies/pull/98), contain the
harness and analysis, not human results. Require three internal
testers, 32 legal commands each per method, alternating method order, captured
loss/recovery, wrong-selection/accidental-Commit counts and a legibility ladder.
Median/p95 prompt-to-Commit, minimum legible angle and chosen angle are Pending.

## Decision

Use the **controller ray with a trigger rising edge** (panel `input_method:
controller_ray`, station `input_method: controllers`) for every session with
people. The ray hand comes from the private panel file; a change for a
participant's accommodation is set at screening, logged and kept for every
visit. Hand poke stays in the engineering build for comparison only;
no fallback silently substitutes it.

No human input measurement exists yet. The choice rests on these reasons:

1. The panel sits at 0.7 m in the reviewed example geometry. A seated poke at
   that distance needs leaning or a closer panel, and a closer panel covers more
   of the robot and workcell. The ray works at the current geometry.
2. One trigger edge gives one activation, with no dwell and no withdrawal rule.
   Poke needs a 4 cm withdrawal and is more exposed to accidental Commit presses
   when the hand rests near the panel.
3. Controller disconnect and pose loss are already detected and pause before the
   next exposure (`docs/response-panel/README.md`). Hand-pose loss under occlusion
   or poor light is harder to bound.
4. The 12 s and 7 s windows need quick, low-error input; a ray keeps the arm at
   rest between selections.

Text size is accepted as a rule, not a number: the panel text angle is 1.5 times
the smallest angle every tester reads without error on the #49/#74 ladder,
checked on all panel positions with the longest real labels. Placeholder labels
cannot qualify it. Until then the reviewed example geometry stays an engineering
setting. Same-family target changes keep a legal action; cross-family changes
clear it. Commit requires a legal tuple.

## Acceptance conditions

Measured with the delivered controllers, before the engineering pilot:

- Three internal testers enter all 32 legal commands with the controller ray;
  p95 prompt-to-Commit is at most 3.5 s; wrong selections and accidental Commits
  are counted (`docs/spikes/O5.1.7-runbook.md`).
- The legibility ladder gives the text angle above, recorded in the private panel
  file and the apparatus manifest.
- Controller disconnect, pose loss and focus loss produce the logged pause and
  explicit recovery.

## Consequences

Input loss clears selection and pauses the next exposure; recovery requires
explicit operator/engineering retry. Polling delay and runtime loss events must
be measured. Internal testers cannot later serve as study participants. #65 and
#74 use the controller ray from this decision; the spike is not the production
response panel.

## Manifest fields

`input_method`, `controller_profile`, `xr_hands_version`, `text_angle_deg`,
`panel_geometry`, `observer_reference`, `loss_events`, input evidence hash.

## Revisit trigger

Tracking/runtime/layout/font/geometry change, recurrent accidental activation,
loss detection failure or any tester unable to read the frozen labels.
