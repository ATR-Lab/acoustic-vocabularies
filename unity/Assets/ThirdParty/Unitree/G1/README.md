# Unitree G1 display source

Source repository: [unitreerobotics/unitree_ros](https://github.com/unitreerobotics/unitree_ros),
pinned revision `5994d4faef0a9cadd3287f8de0199a67eeb2a259`.
Source file: `robots/g1_description/g1_29dof_with_hand_rev_1_0.urdf`.
URDF SHA-256: `97da67732d067c3147fc5fb7b7bafc8982718f4e7f8c92ff82266a4d9c07200d`.
The repository's [BSD-3-Clause license at the pinned revision](https://github.com/unitreerobotics/unitree_ros/blob/5994d4faef0a9cadd3287f8de0199a67eeb2a259/LICENSE)
is reproduced unchanged in `LICENSE` and applies to these repository assets.

Only the text URDF, license and mesh checksums are vendored. The 49 STL meshes are
fetched by `spikes/O5.1.4/fetch_meshes.py` into ignored `external-assets/` and
verified against `mesh-sources.json`. Binary vendoring remains blocked by the
repository's disabled Git LFS service; do not commit dangling pointers or put the
meshes directly into ordinary Git. This is an explicit incomplete #46 checklist item.

The converter writes deterministic display geometry and joint metadata. The Unity
editor importer uses plain Transforms, never ArticulationBody or local physics.
No unmaintained URDF importer package is required. Generated Unity assets, prefab
and preview scene remain under ignored `Assets/Generated.local.data/` until the
binary distribution path is approved. The importer copies this license into the
generated Resources so local robot builds include the attribution.
