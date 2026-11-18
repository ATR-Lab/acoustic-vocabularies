# Held comfortable level and the discomfort stop

The comfortable level is set at screening and in the first calibration of the
allocated slot (visit 1). It is then held constant for every later visit. No level
change is made inside a visit. A participant who finds the sounds uncomfortable may
stop; that stop is logged as its own visit exit.

## Holding the level

- The calibration scene runs under the slot code with `gain_source` (see
  [comfort-gain-carry.md](comfort-gain-carry.md)). The participant may adjust the
  level there. When all three profiles are answered as comfortable,
  `ComfortableGainStore.Hold` appends one `comfort_gain_held` row
  (`coded_id`, `visit_id`, `gain`, `mono_s`) and the scene logs `calibration_gain_held`.
- Screening histories are never held. A run with any uncomfortable answer does not
  hold; the operator decides what happens next.
- After the hold, the level buttons are disabled and `ChangeForComfort` refuses with
  `GAIN_HELD_CONSTANT` without writing. A history with a change row after a hold, a
  hold that differs from the level, or a hold as the first row is
  `GAIN_HISTORY_INVALID`.
- A visit loads the pinned history through `JoinedVisitArtifacts.HeldGain` for every
  role (Study A learners and both Study B partners). An unheld history is refused
  with `JOIN_GAIN_NOT_HELD`; another person's history with `JOIN_GAIN_BINDING`.
  Pin the history after the hold.

| Code | Cause |
| --- | --- |
| `GAIN_HELD_CONSTANT` | A level change was requested after the level was held. |
| `GAIN_HOLD_SOURCE_MISSING` | No recorded history to hold; a level is never invented. |
| `GAIN_HOLD_INVALID` | Invalid visit ID or time. |
| `GAIN_NOT_HELD` / `JOIN_GAIN_NOT_HELD` | A visit was loaded with a level that was not held. |

## Discomfort stop

The operator request `stop` may carry `"stop_reason":"participant_discomfort"`.

- The engine writes the session event `participant_discomfort_stop` before anything
  else. Its `technical_fault_code` is null. An item stopped after its cue request is
  marked `SESSION_PARTICIPANT_DISCOMFORT_STOP`, not `SESSION_OPERATOR_STOP`.
- The visit stops at once and its remaining trials are not run. Start and resume are
  refused, and a restarted engine recovers the visit as stopped.
- The operator journal adds a `visit_exit` row with `exit_reason`
  `participant_discomfort` after the accepted result. Use it for the run sheet and
  the payment paperwork; it is neither a technical fault nor a withdrawal.
- A plain stop can be marked as a discomfort stop afterwards by sending the reasoned
  stop; the session event is written once.
