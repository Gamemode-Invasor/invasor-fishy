import tempfile
import unittest
from pathlib import Path

import launcher


class Launcher(unittest.TestCase):
    def test_format(self):
        self.assertEqual(launcher.parse("version=1\nenable_zink=0\nforce_alsa_audio=0\n"), {"enable_zink": False, "force_alsa_audio": False})
        self.assertEqual(launcher.parse("# c\n version = 1 \nenable_zink=1\n"), {"enable_zink": True, "force_alsa_audio": False})
        self.assertEqual(launcher.dumps({"enable_zink": True, "force_alsa_audio": False}), "version=1\nenable_zink=1\nforce_alsa_audio=0\n")
        for bad in ("enable_zink=1\n", "version=2\n", "version=1\nenable_zink=2\n", "version=1\nfoo=1\n",
                    "version=1\nenable_zink=1\nenable_zink=0\n", "version=1\na=b=c\n"):
            with self.subTest(text=bad), self.assertRaises(launcher.Invalid):
                launcher.parse(bad)

    def test_paths_and_files(self):
        self.assertEqual(launcher.path({"MAKO_LAUNCH_CONFIG": "/x.conf"}), Path("/x.conf"))
        self.assertEqual(launcher.path({"XDG_CONFIG_HOME": "/c"}), Path("/c/mako-render/launcher.conf"))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "launcher.conf"
            self.assertEqual(launcher.load(p), {"enable_zink": False, "force_alsa_audio": False})
            launcher.save({"enable_zink": False, "force_alsa_audio": True}, p)
            self.assertEqual(launcher.load(p), {"enable_zink": False, "force_alsa_audio": True})
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
