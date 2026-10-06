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

Validation: schema validation passed (45 schemas, 14 synthetic examples), the
diff passed whitespace checks, and the reachable-history public guard passed.
Unity compiled the exact contrast source `087f697e462dc53eb89b2ed7e08f34846fe82c37`
in integration checkpoint `2248a79`. All four new contrast cases and both existing
placement cases passed. The combined run passed 155 of 156 cases and exited 2:
an unrelated diagnostic null-token assertion failed. The retained raw test XML
has SHA-256 `58c13f94e24ed1e9f5bf7b02309f781d6776a1d425a0516308400d615585f882`.
No native build or corrected-view capture is claimed by this test run.

Issue #155 remains open until an affected native teaching/menu view is checked
at the configured observer. Physical headset readability and study
qualification remain separate requirements.
