# Teaching and menu text contrast correction

Refs #155, #82, #81.

The native simulator teaching capture from build `simulation-native-008`
showed white text against the light workcell with no text backing. The retained
private capture has SHA-256
`f4f5320fdbaea072ddd3f41176f64799395feb8888256f01e1d0408425a6ff88`;
the build source was `4ddd7f65d2f7648fe7a60ec5e3d853659996646f`.
Source inspection identified the same unbacked instruction and meaning text in
the menu host. The capture is not published because it contains private material.

The correction adds an opaque dark backing matching each existing teaching
definition, role and feedback rectangle and each menu instruction and meaning
rectangle. White text and yellow teaching highlights remain unchanged. The
backings render before text, have no raycast target or collider, and remain
children of the same canvas under the foundation presentation gate. The menu
candidate cards retain their existing backing and input geometry.

Typography, calibrated world pose, scale, content, images, timing and response
panel angular dimensions are unchanged. The correction does not add a full
canvas background or create a new owner of presentation visibility.

`JoinedTextContrastTests` constructs the actual teaching and menu hosts and
checks backing coverage and draw order, white/yellow palette contrast, original
font sizes and menu candidate geometry, initially hidden views, matching-owner
hide, and ancestor concealment. The numerical contrast threshold is a software
palette regression; it does not establish actual headset readability.
`JoinedViewPlacementTests` separately checks the existing calibrated poses.

Validation at this checkpoint: schema validation passed (45 schemas, 14
synthetic examples) and the diff passed whitespace checks. Unity execution of
the new tests and an affected native teaching/menu capture are pending. Issue
#155 remains open until the native view is checked at the configured observer;
physical headset readability and study qualification are separate requirements.
