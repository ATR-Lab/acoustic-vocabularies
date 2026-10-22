# ADR-004 — Seated input and legibility

Status: Proposed — method and text angle pending internal testers

## Context

WBS O5.1.8 / #50 and #49. The panel has eight targets, four family-filtered
actions and Commit, with no preselection. Issue constants give 12 s full-message
and 7 s atomic windows; these need protocol reconciliation.

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

Do not select an input method yet. Adopt proposed engineering screens: p95
prompt-to-Commit <=3.5 s, and candidate text angle 1.5 times the smallest measured
angle that every tester reads without error. Confirm the selected angle on all
panel positions and longest actual protocol labels; placeholder labels cannot
qualify final legibility. Same-family target changes preserve legal actions;
cross-family changes clear them. Commit requires a legal tuple.

## Consequences

Input loss clears selection and pauses the next exposure; recovery requires
explicit operator/engineering retry. Polling delay and runtime loss events must
be measured. Internal testers cannot later serve as study participants. #65/#74
inherit a choice only after G1; the spike is not the production response panel.

## Manifest fields

`input_method`, `controller_profile`, `xr_hands_version`, `text_angle_deg`,
`panel_geometry`, `observer_reference`, `loss_events`, input evidence hash.

## Revisit trigger

Tracking/runtime/layout/font/geometry change, recurrent accidental activation,
loss detection failure or any tester unable to read the frozen labels.
