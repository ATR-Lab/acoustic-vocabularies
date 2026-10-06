"""Independent small synthetic fixtures, never robot performance evidence."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from convert_urdf import convert, stl_triangles, unity_position, write_mesh
from compare_poses import compare, quaternion_error_degrees


class ConversionTests(unittest.TestCase):
    def test_binary_and_ascii_stl_coordinate_winding(self):
        vertices = (0, 0, 0, 1, 0, 0, 0, 1, 0)
        binary = b"x" * 80 + struct.pack("<I12fH", 1, 0, 0, 1, *vertices, 0)
        self.assertEqual(len(list(stl_triangles(binary))), 1)
        ascii_stl = b"solid test\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendsolid test\n"
        self.assertEqual(list(stl_triangles(binary)), list(stl_triangles(ascii_stl)))
        self.assertEqual(unity_position((1, 2, 3)), (-2, 3, 1))
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "test.stl", Path(directory) / "test.avmesh"
            source.write_bytes(binary); write_mesh(source, target)
            self.assertEqual(struct.unpack("<9f", target.read_bytes()[8:]), (0, 0, 0, -1, 0, 0, 0, 0, 1))

    def test_deterministic_converter_and_invalid_hierarchy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tiny.urdf"
            source.write_text('<robot name="tiny"><link name="base"/><link name="tip"/><joint name="q" type="revolute"><parent link="base"/><child link="tip"/><origin xyz="1 2 3"/><axis xyz="0 0 1"/><limit lower="-1" upper="1"/></joint></robot>')
            _, a = convert(source, root, root / "a")
            _, b = convert(source, root, root / "b")
            self.assertEqual(a, b)
            source.write_text('<robot name="bad"><link name="a"/><link name="b"/></robot>')
            with self.assertRaises(ValueError): convert(source, root, root / "bad")

    def test_quaternion_sign_and_position_error(self):
        self.assertEqual(quaternion_error_degrees([1, 0, 0, 0], [-1, 0, 0, 0]), 0)
        fixture = dict(coordinate_frame="usd_world_rh_z_up", poses=[dict(name="synthetic", joint_names=["q"],joint_positions=[0],links=[dict(name="tip",position=[0,0,0],quaternion_wxyz=[1,0,0,0])])])
        actual = json.loads(json.dumps(fixture)); actual["poses"][0]["links"][0]["position"][0] = .006
        _, result = compare(fixture, actual)
        self.assertAlmostEqual(result["max_position_error_mm"], 6)
        self.assertFalse(result["all_observed_links_within_proposed_tolerance"])


if __name__ == "__main__": unittest.main()
