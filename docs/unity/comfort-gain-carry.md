# Carry the comfortable level from screening into the allocated slot

The participant sets a comfortable level in the calibration scene **before
allocation**, under the screening code. Visits after allocation run under the slot
code and restore the gain history pinned for that slot
(`SHA256(slot ASCII).local.jsonl`). Without a link between the two codes, the first
visit would restart at the initial relative gain. That would break the rule that
the level chosen at screening is kept for every visit.

## Operator step after allocation

Run the calibration scene once more under the slot code. Add the optional
`gain_source` object to the private calibration file:

```json
"gain_source": {"coded_id": "<screening code>", "allocation_receipt_sha256": "<reveal receipt sha256>"}
```

- **First launch.** `ComfortableGainStore.CarryForward` creates the slot history. Its
  first row is `comfort_gain_carried`, which records:
  - the slot code;
  - the screening code;
  - the SHA-256 of the screening history;
  - the allocation receipt;
  - the exact gain;
  - the monotonic time.
- **Relaunch.** The same file restores normally. A different screening code or
  receipt is refused with `GAIN_CARRY_MISMATCH`.
- **Comfort changes.** Any comfort change made afterwards chains from the carried
  value as an ordinary `comfort_gain_changed` row.
- **Pinning.** Pin the resulting slot history as the visit's `comfort_gain` file,
  as before.

## Refusals

| Code | Cause |
| --- | --- |
| `GAIN_CARRY_SOURCE_MISSING` | There is no recorded screening history, so the code never invents a gain. |
| `GAIN_CARRY_TARGET_EXISTS` | The slot already has a history; it is never overwritten. |
| `GAIN_CARRY_INVALID` | An invalid code, receipt or time, or the slot code equals the screening code. |
| `GAIN_HISTORY_INVALID` | A carried row is not the first row or not canonical, or a later row does not chain from it. |

Gain is never tuned to accuracy. A comfort change is the only way it moves after
screening.
