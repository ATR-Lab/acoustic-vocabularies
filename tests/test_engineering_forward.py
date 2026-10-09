"""The engineering SSH forward must keep TCP_NODELAY semantics and loopback-only binds (#148)."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("engineering_forward", Path(__file__).resolve().parents[1] / "tools/engineering_forward.py")
forward = importlib.util.module_from_spec(spec)
spec.loader.exec_module(forward)


class EngineeringForwardTests(unittest.TestCase):
    def test_interactive_transport_without_keystroke_quantization(self):
        argv = forward.forward_command("mlws", 3000)
        options = [argv[i + 1] for i, value in enumerate(argv) if value == "-o"]
        # A pty-backed session is what makes both OpenSSH ends set TCP_NODELAY;
        # forwarding-only -N leaves Nagle on and reproduced the late replies.
        self.assertIn("RequestTTY=force", options)
        self.assertIn("ObscureKeystrokeTiming=no", options)
        self.assertIn("ExitOnForwardFailure=yes", options)
        self.assertIn("BatchMode=yes", options)
        self.assertNotIn("-N", argv)
        self.assertNotIn("-f", argv)
        self.assertEqual(argv[-3:], ["mlws", "sleep", "3000"])

    def test_only_documented_loopback_forwards(self):
        argv = forward.forward_command("mlws", 60)
        specs = [argv[i + 1] for i, value in enumerate(argv) if value == "-L"]
        self.assertEqual(specs, ["127.0.0.1:18765:127.0.0.1:18766", "127.0.0.1:18767:127.0.0.1:18768"])
        self.assertFalse(any(value in ("-R", "-D", "-g", "-W") for value in argv))

    def test_refuses_option_injection_unbounded_lifetime_and_bad_ports(self):
        for host in ("-oProxyCommand=x", "user@mlws", "ml ws", "", None):
            with self.assertRaises(ValueError):
                forward.forward_command(host, 60)
        for seconds in (0, 4, 3601, 60.0, True, "60"):
            with self.assertRaises(ValueError):
                forward.forward_command("mlws", seconds)
        for pairs in ((), ((80, 18766),), ((18765, 70000),), ((18765, 18766), (18765, 18768))):
            with self.assertRaises(ValueError):
                forward.forward_command("mlws", 60, pairs)


if __name__ == "__main__":
    unittest.main()
