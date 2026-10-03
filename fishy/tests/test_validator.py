import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

import validator

GOOD = 'version = 2\n\n[global]\nallow_fp16 = true\n\n[[profile]]\nname = "x"\nmultiplier = 2\n'


def fake_cli(folder, exit_code, *lines, sleep=0):
    """A stand-in for mako-cli that answers like the real one."""
    p = Path(folder) / "mako-cli"
    p.write_text("#!/bin/sh\n" f"sleep {sleep}\n" + "".join(f"echo '{ln}'\n" for ln in lines) + f"exit {exit_code}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


class Validator(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.dir = Path(d.name)
        self.conf = self.dir / "conf.toml"
        self.conf.write_text(GOOD)

    def test_accepted_and_rejected(self):
        self.assertIsNone(validator.validate(self.conf, fake_cli(self.dir, 0, "Validation success")))
        cli = fake_cli(self.dir, 1, "Validation failed: multiplier must be between 2 and 5")
        with self.assertRaisesRegex(validator.Rejected, "^multiplier must be between 2 and 5$"):
            validator.validate(self.conf, cli)
        cli = fake_cli(self.dir, 1, "Validation failed: unable to parse configuration", "- Error while parsing")
        with self.assertRaisesRegex(validator.Rejected, "^unable to parse configuration - Error while parsing$"):
            validator.validate(self.conf, cli)
        with self.assertRaisesRegex(validator.Rejected, "exited with 3"):
            validator.validate(self.conf, fake_cli(self.dir, 3))

    def test_hung_or_missing_validator_is_unavailable(self):
        old, validator.TIMEOUT = validator.TIMEOUT, 0.5
        self.addCleanup(setattr, validator, "TIMEOUT", old)
        with self.assertRaises(TimeoutError):
            validator.validate(self.conf, fake_cli(self.dir, 0, sleep=3))
        with self.assertRaises(OSError):
            validator.validate(self.conf, str(self.dir / "nope"))

    def test_finds_the_cli_next_to_the_library(self):
        (self.dir / "bin").mkdir()
        (self.dir / "lib").mkdir()
        cli = fake_cli(self.dir / "bin", 0)
        self.assertEqual(validator.find_cli(self.dir / "lib/libmako-render.so"), cli)


REAL = shutil.which("mako-cli") or str(Path.home() / ".local/bin/mako-cli")


@unittest.skipUnless(os.access(REAL, os.X_OK), "mako-cli not installed")
class RealValidator(unittest.TestCase):
    """The real mako-cli, on temporary files only."""

    def test_real_answers(self):
        with tempfile.TemporaryDirectory() as d:
            conf = Path(d) / "conf.toml"
            conf.write_text(GOOD)
            self.assertIsNone(validator.validate(conf, REAL))
            conf.write_text(GOOD.replace("multiplier = 2", "multiplier = 9"))
            with self.assertRaisesRegex(validator.Rejected, "multiplier"):
                validator.validate(conf, REAL)
            conf.write_text(GOOD.replace("multiplier = 2", 'scaling_method = "xx"'))
            with self.assertRaisesRegex(validator.Rejected, "scaling method"):
                validator.validate(conf, REAL)


if __name__ == "__main__":
    unittest.main()
