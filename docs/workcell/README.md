# Unity public workcell (#61)

The generated participant scene displays the exact 60-object layout exported by
#52 at commit `8802e2e`, SHA-256
`173048c7109741b3c8b3e34e9166a480e17b72fcc3b60f15a1edacba482879eb`.
This remains a provisional engineering layout pending the missing Common
procedures review. The 18 physical reach anchors and eight-action methodology
are separate qualification evidence; object presence is not a protocol sign-off.

## Generate and build

Use the pinned Unity 6000.6.0f1 editor. Fetch and convert the approved Unitree
assets with the commands in `docs/spikes/O5.1.4-runbook.md`. The deterministic
converted description must hash to
`6ff5a6f555cb650e628fff674d28827df6f8fb4618afcb61506c0f868d1cbb41`;
the importer verifies every AVM1 mesh hash from that pinned description. The
BSD-3 notice is included in player Resources. No URDF-Importer or ROS-TCP package
is required. LFS remains unavailable, so downloaded meshes, generated mesh assets,
scene, logs, captures and players stay ignored.

```powershell
./tools/build-unity.ps1 -Unity $env:UNITY_EDITOR_PATH -Scene Workcell -G1Description $env:G1_DESCRIPTION_JSON -Target Configure -ProtocolVersion engineering-pending-review -BuildId workcell-configure-001
./tools/build-unity.ps1 -Unity $env:UNITY_EDITOR_PATH -Scene Workcell -G1Description $env:G1_DESCRIPTION_JSON -Target Test -ProtocolVersion engineering-pending-review -BuildId workcell-test-001
./tools/build-unity.ps1 -Unity $env:UNITY_EDITOR_PATH -Scene Workcell -G1Description $env:G1_DESCRIPTION_JSON -Target Windows -ProtocolVersion engineering-pending-review -BuildId workcell-link-001
./tools/build-unity.ps1 -Unity $env:UNITY_EDITOR_PATH -Scene Workcell -G1Description $env:G1_DESCRIPTION_JSON -Target Android -ProtocolVersion engineering-pending-review -BuildId workcell-quest-001
```

Pass the same process-local `-TemporaryDirectory` and `-GradleCache` documented by
foundation when needed. Use a fresh build ID each time. Production source goes
under `Assets/ExperimentApp/Runtime`; tests and editor code have separate asmdefs.
Generated participant scene: `Assets/Generated.local.data/Workcell/Workcell.unity`.
The public Foundation scene remains unchanged. Missing private station config
still hides PresentationRoot. #62 supplies state-source orchestration.

## Contract for #62

`AcousticVocab.Workcell` references only UnityEngine/system assemblies. It cannot
reference Foundation, state transports, Session or Answer code. Its registry
contains the immutable imported TextAsset (`ImportedLayout`), `LayoutSha256`, and
`CanonicalJointNames` in the verified 43-row #46 order: 29 body + 7 + 7 hand joints.
The registry transform is identity under `PresentationRoot`.

`SceneCoordinates.Position` maps Isaac RH Z-up metres to Unity `(-y,z,x)`.
`SceneCoordinates.Rotation` maps Isaac quaternion XYZW to `(y,-z,-x,w)`.
The source host must convert once, then call:

- `bool ApplyJoint(string name, float radians)`; finite values inside the actual
  Isaac limits, with a 0.00001 rad float tolerance, are accepted without clamping.
- `bool ApplyObject(string id, Vector3 localPosition, Quaternion localRotation,
  bool visible, bool enabled, PublicVisualState state)`; poses are Unity workcell
  local coordinates. State must have exactly the authored keys. The nullable
  public fields are `cardFace`, `arrowAngleRad`, `lidOpenFraction`, `tagAttached`,
  and `location`. Null means absent. Quaternion squared norm tolerance is 1e-4.
- `ResetToImportedNeutral()` restores every object and all robot joints.

Each individual update validates before mutation. #62 must validate a complete
frame before applying its sequence of updates; this API does not claim frame-wide
transactionality. Enabled is a public semantic property and does not hide an
otherwise visible object. Location and attachment remain discrete metadata;
authored position/rotation drives their actual placement. Card, arrow and lid
articulation rotates only its Visual child. Arrow slots remain fixed. The frozen
USD arrow heads use T*S*R, reproduced with a scaled parent and rotated cube.

`WorkcellBuild.AddToOpenScene()` returns the registry for a host scene composer.
`WorkcellBuild.Configure()` composes the base foundation, generates the workcell,
and sets `FoundationBuild.ParticipantScenePath` before guarded builds. #62 must
add its reviewed host component to the explicit build allowlist.

## Rendering and qualification limits

The own 3x5 geometric glyphs reproduce the USD generator. Primary tray letters,
container codes and quarantine letters all use a 3.8 mm pixel pitch. Cards use
4 mm; cup text uses at most 3 mm with the exported 85% width cap. Materials use
exact exported diffuse RGB, roughness 0.7 represented as Standard glossiness 0.3.
A fixed hemispheric ambient gradient and fixed white directional source approximate
the Isaac dome in Unity's built-in renderer. Unity's lighting, shadows and tone
response are not a photometric match to Isaac RTX; no per-trial light/material
changes exist. The background is a fixed neutral 0.95 RGB. No ambient audio,
colliders or rigid bodies are present in the workcell.

The robot uses the reviewed URDF geometry/materials and authored USD sensor-frame
corrections. USD wrist-camera bracket meshes are absent from the URDF. USD and
URDF robot material assignments differ (notably dark hands/upper arms and the
chest lettering). These are visible differences, not pose errors. Quest frame
budget, wearer comfort/legibility, eight-action protocol reconciliation, formal
32-target leakage comparison (#79), and human side-by-side sign-off remain open.

## Captures

Run `AcousticVocab.Workcell.Editor.WorkcellBuild.Capture` in batch mode without
`-nographics`, setting `G1_DESCRIPTION_JSON` and a fresh ignored
`WORKCELL_CAPTURE_OUTPUT` directory. It renders the exported observer pose and
writes a PNG plus a public-field pose/hash record. The camera is temporary editor
code and is never saved in the participant scene. `capture-poses.json` records
that reference and proposed matched offsets for #79; these are engineering
capture viewpoints, not forced HMD poses or accepted calibration values.
