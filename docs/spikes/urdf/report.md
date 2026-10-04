# G1 / Dex3 Unity import — O5.1.4

Status: **incomplete**. Fetch, conversion, hierarchy import, Android robot build
and the full measured Isaac-versus-Unity pose comparison are verified. Headset
frame timing remains pending. Supplemental close hand views have been inspected.
Binary vendoring remains
blocked while Git LFS uploads are rejected.
No fabricated performance values appear here.

## Reproducible source and import

Pinned Unitree revision `5994d4faef0a9cadd3287f8de0199a67eeb2a259`; text URDF,
unchanged BSD-3 license and checksums are vendored under
`unity/Assets/ThirdParty/Unitree/G1/`. Source URDF SHA-256:
`97da67732d067c3147fc5fb7b7bafc8982718f4e7f8c92ff82266a4d9c07200d`.
All 49 referenced STL meshes fetched and hashed, totaling 31,471,016 source bytes.
Two independent output directories produced the same `g1_description.json` SHA-256:
`6ff5a6f555cb650e628fff674d28827df6f8fb4618afcb61506c0f868d1cbb41`.

The source Transform hierarchy imported successfully in Unity 6000.6.0f1 with
53 links, 52 joints (43 revolute, 9 fixed), and 629,338 rendered triangles. The
aligned hierarchy has 55 link frames after adding the two USD-only sensor frames.
Both Dex3 hands expose seven driven joints. Generated geometry is unmodified in
density. The robot preview scene built successfully as an Android ARM64 IL2CPP
development APK (60,353,958 bytes). Building does not establish headset performance.
The aligned 55-frame rebuild also passed, with APK SHA-256
`64323a16dd8aa6c302fd4b2288dd248cff2e978aa9b0770ef67bc45a876f01ca`.
Raw build logs and generated assets remain in ignored storage.

The converter maps positions by `C(x,y,z)=(-y,z,x)` and reverses triangle winding.
Joint axes use the axial-vector transform `-C(axis)`; rotations use `C R C^-1`.
This is separate from any candidate Isaac/URDF sign or zero offset. No Unity
URDF-Importer or ArticulationBody is involved. [Unity mesh index and winding docs](https://docs.unity3d.com/Manual/mesh-index-data.html)
and [AngleAxis API](https://docs.unity3d.com/ScriptReference/Quaternion.AngleAxis.html)
describe the target mesh/rotation APIs.

## Mapping and pose evidence

The actual loaded Isaac articulation inventory from #44 has 43 joints. All 43
names occur exactly once in the imported driven-joint set; there are zero unmatched
names on either side, including seven per hand. `joint_map.csv` preserves observed
Isaac index order and both sources' limits. The sign +1 / offset 0 entries are now
verified on the complete recorded 92-pose suite, with `pose_verified=true`.
The canonical CSV uses UTF-8 without BOM, LF newlines and observed Isaac index
order; its SHA-256 is
`1d6a3129b2dc3747284043d43b1ecd33bbaae6003874a5a01f63711b3a7b686e`.

The initial comparison found a real source discrepancy: mid360's fixed frame had
12.254243 mm position error and a 180-degree orientation difference, and two USD
hand-camera frames were absent from the URDF. The other 52 source links already
matched. Authored USD fixed-joint metadata confirmed the mid360 origin and rotation
and both hand-camera parent transforms. `usd-fixed-frames.json` records the exact
corrections; the vendored URDF is unchanged. `baseline-pose-summary.json` preserves
the failed result so the correction is reviewable.

After correction, all **5,060 link-pose pairs (92 poses x 55 links)** pass the
proposed <=5 mm / <=1 degree tolerance. Maximum observed position error is
**0.000649038 mm**, and maximum orientation error is **0.000105371 degrees**.
There are no missing or unobserved link frames. These are measured FK comparison
results from the pinned sources, not headset tracking or motion accuracy claims.
The pose-summary records hashes of both full exports; #44 owns the reference pose
fixture. The per-link values are in `pose_errors.csv`.

| Measurement | Result |
| --- | --- |
| Actual Isaac names matched to Unity | 43 / 43 |
| Left / right hand name matches | 7 / 7 |
| Measured reference poses | 92: default + 86 single-joint + 5 engineering spread |
| Maximum link position / orientation error | 0.000649038 mm / 0.000105371 degrees |
| Link coverage | 55 / 55 in every pose; zero missing/unobserved frames |
| Quest 10-minute frame-budget run | pending wearer/device |
| Unity engineering captures | 3 rendered and locally inspected; hashes in screenshot-manifest.json |
| Side-by-side Isaac/Unity hand review | 3 supplemental measured poses inspected with matching cameras |

Three synthetic Python tests verify binary/ASCII STL conversion, coordinate and
winding convention, deterministic description hashes, invalid hierarchy rejection
and quaternion/position error calculations. They are not measured apparatus data.
All 12 repository Python tests pass. Three 1024-square Unity editor images show
the complete robot at `default`, `joint_20_75pct` and `spread_4`, without obvious
missing mesh geometry. They remain local while LFS is unavailable. They are not
headset captures. Those full-body images were insufficient to inspect hand closure.

A separate measured fixture now covers `hands_open`, `hands_half_flexed` and
`hands_flexed` with the body at its loaded default. All **165 link-pose pairs**
pass, with maximum position error **0.000299510 mm** and orientation error
**0.000092271 degrees**. The canonical 92-pose fixture and map hash are unchanged.
Results and paired screenshot hashes are in `hand-comparison/`. Three matching
1280x720 camera views visibly agree in body and finger flexion. Six additional
Unity views fit the left and right palms separately; both fully flexed close views
were inspected. Lighting and materials differ, and visible USD wrist brackets are
absent from the source URDF geometry; the added camera frames are empty transforms.
This establishes pose/display evidence, not identical mesh content or Quest timing.

## Remaining acceptance

Run the full G1 scene on Quest for at least 600 seconds at the chosen #45 rate with app timing and
presentation evidence. Decide on decimation only from that result. Resolve binary
vendoring after LFS is enabled. External protocol review is still required.
Follow [the reproducible runbook](../O5.1.4-runbook.md).
