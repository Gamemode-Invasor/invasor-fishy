import importlib.util
import json
import os
import logging
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
# Invasor's core (ctx.toml, forms): $INVASOR_CORE (set by tools/check_module.py), else the
# core this module sits in (modules/<id>), else a checkout next to this repository
# (<repo>/<id>/ -> ../invasor).
_core = os.environ.get("INVASOR_CORE") or next(
    (str(p) for p in (HERE.parents[1], HERE.parents[1] / "invasor") if (p / "backend/invasor").is_dir()), "")
sys.path.insert(0, str(Path(_core) / "backend"))

from invasor import schema, tomlio  # noqa: E402
from invasor.modules import Form  # noqa: E402

# The shape of a real MAKO v2 config (made-up values).
CONF = """version = 2

[global]
allow_fp16 = true

[[profile]]
active_in = [ "1000" ]
adaptive = false
flow_scale = 0.75
frame_generation_enabled = true
multiplier = 2
name = "default"
pacing = "none"
performance_mode = true
scaling_enabled = false
unknown_future_key = "kept"
"""


def load_backend():
    name = "fishy_backend_under_test"
    for n in [n for n in sys.modules if n == name or n.startswith(name + ".")]:
        del sys.modules[n]
    spec = importlib.util.spec_from_file_location(name, HERE / "backend.py", submodule_search_locations=[str(HERE)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeGame:
    def info(self, appid):
        return {"1000": {"appid": "1000", "name": "Test Game", "shortcut": False}}.get(str(appid))

    def shortcut_exe(self, appid):
        return {"3000000001": '/games/Thing/Thing.exe'}.get(str(appid))


class FakeCtx:
    InvalidArgument = schema.InvalidArgument
    Unavailable = schema.Unavailable
    toml = tomlio

    def __init__(self):
        manifest = schema.parse_manifest(json.loads((HERE / "module.json").read_text()), "fishy")
        self.forms = {n: Form(f) for n, f in manifest["form_fields"].items()}
        self.game = FakeGame()
        self.log = logging.getLogger("test.fishy")


class Backend(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / "mako-render" / "conf.toml"
        self.path.parent.mkdir()
        self.path.write_text(CONF)
        self.b = load_backend()
        self.b.mako.config_path = lambda env=None: self.path
        self.b._cli = lambda: None  # tests never ask the real mako-cli unless they say so
        self.b.setup(FakeCtx())

    def file(self):
        return tomllib.loads(self.path.read_text())

    def test_profile_values_are_validated_and_everything_else_kept(self):
        self.assertEqual(self.b.profile_get("default")["flow_scale"], 0.75)
        self.assertEqual(self.b.profile_set("default", "multiplier", 99), 5)  # clamped by the form
        self.assertEqual(self.b.profile_set("default", "flow_scale", 0.31), 0.3)
        with self.assertRaises(schema.InvalidArgument):
            self.b.profile_set("default", "pacing", "none")  # not a form field: kept, never set
        with self.assertRaises(schema.InvalidArgument):
            self.b.profile_set("default", "scaling_method", "xx")
        self.assertEqual(self.b.profile_set("default", "gpu", "AMD"), "AMD")
        self.assertEqual(self.b.profile_set("default", "gpu", ""), "")
        self.assertNotIn("gpu", self.file()["profile"][0])
        with self.assertRaises(schema.InvalidArgument):
            self.b.profile_set("default", "name", "x")
        p = self.file()["profile"][0]
        self.assertEqual((p["multiplier"], p["flow_scale"], p["unknown_future_key"], p["pacing"], p["active_in"]), (5, 0.3, "kept", "none", ["1000"]))
        self.assertTrue(self.path.with_name("conf.toml.invasor-backup").exists())

    def test_per_game_profile(self):
        self.b.profile_create("3x")
        self.assertEqual(self.b.game_profile("1000"), {"entry": "1000", "profile": "default", "custom": True})
        self.b.set_game_profile("1000", False, "3x")
        self.b.set_game_profile("3000000001", True, "3x")
        self.assertEqual(self.file()["profile"][1]["active_in"], ["1000", "3000000001"])
        self.assertNotIn("active_in", self.file()["profile"][0])
        self.assertEqual(self.b.profiles()[1]["games"], [{"entry": "1000", "name": "Test Game"}, {"entry": "3000000001", "name": None}])
        self.b.set_game_profile("1000", False, None)
        self.assertIsNone(self.b.game_profile("1000")["profile"])
        with self.assertRaises(schema.InvalidArgument):
            self.b.set_game_profile("1000", False, "ghost")

    def test_custom_profile_for_a_game(self):
        self.b.set_game_profile("2000", False, "default")  # "default" is now shared by two games
        self.assertFalse(self.b.game_profile("1000")["custom"])
        made = self.b.make_custom("1000")  # title from the game's name
        self.assertEqual(made, {"entry": "1000", "profile": "Test Game", "custom": True})
        self.assertEqual(self.b.game_profile("1000"), {"entry": "1000", "profile": "Test Game", "custom": True})
        own = self.file()["profile"][1]
        self.assertEqual((own["name"], own["flow_scale"], own["active_in"]), ("Test Game", 0.75, ["1000"]))
        self.assertEqual(self.file()["profile"][0]["active_in"], ["2000"])
        self.assertEqual(self.b.make_custom("1000")["profile"], "Test Game")  # already its own: kept

    def test_options_that_change_others(self):
        self.b.profile_set("default", "ultra_performance", True)
        p = self.file()["profile"][0]
        self.assertEqual((p["flow_scale"], p["performance_mode"], self.file()["global"]["allow_fp16"]), (0.7, True, True))
        with self.assertRaisesRegex(schema.InvalidArgument, "Ultra performance"):
            self.b.profile_set("default", "flow_scale", 0.9)
        self.assertEqual(self.b.profile_get("default")["flow_scale"], 0.7)

    def test_global(self):
        self.assertEqual(self.b.global_get(), {"allow_fp16": True, "dll": ""})
        self.b.global_set("allow_fp16", False)
        self.b.global_set("dll", "/x/Lossless.dll")
        self.assertEqual(self.file()["global"]["dll"], "/x/Lossless.dll")
        self.b.global_set("dll", "")
        self.assertEqual(self.file()["global"], {"allow_fp16": False})

    def test_profile_management_errors_are_clean(self):
        for call, args in (("profile_create", ("default",)), ("profile_rename", ("ghost", "x")),
                           ("profile_delete", ("ghost",)), ("profile_get", ("ghost",))):
            with self.subTest(call=call), self.assertRaises(schema.InvalidArgument) as cm:
                getattr(self.b, call)(*args)
            self.assertNotIn("'\"", str(cm.exception))

    def test_unsupported_or_broken_files_are_never_written(self):
        self.path.write_text("version = 3\n")
        with self.assertRaises(schema.Unavailable):
            self.b.profile_create("x")
        self.assertEqual(self.path.read_text(), "version = 3\n")
        self.assertIn("version", self.b.status()["error"])
        self.path.write_text("version = = 2")
        with self.assertRaises(schema.InvalidArgument):
            self.b.profiles()

    def test_new_profiles_match_mako_ui(self):
        # A profile as mako-ui creates it (MAKO 4.0.0), keys in its order.
        mako_ui = tomllib.loads("""[[profile]]
adaptive = false
adaptive_auto_base_fps_cap = false
adaptive_fractional_real_frame_priority = "auto"
adaptive_max_multiplier = 3
adaptive_stable_cadence = true
base_fps_cap = 0
dynamic_cadence_probe_interval_seconds = 2.0
dynamic_cadence_recovery = false
flow_scale = 0.8
frame_generation_enabled = true
frame_generation_provisioned = true
frame_generation_refresh_threshold = 0
gamescope_vrr_mode = "follow-steam"
multiplier = 2
name = "nuevo"
pacing = "none"
performance_mode = false
scaling_enabled = false
scaling_factor = 1.5
scaling_method = "ls1"
scaling_sharpness = 0.8
scaling_supersampling = false
swapchain_image_count_compatibility = false
target_fps = 120
ultra_performance = false
""")["profile"][0]
        self.b.profile_create("nuevo")
        ours = self.file()["profile"][1]
        self.assertEqual(list(ours.items()), list(mako_ui.items()))
        self.assertEqual([type(v) for v in ours.values()], [type(v) for v in mako_ui.values()])  # 2.0 stays a float

    def test_missing_file_is_created_on_first_change(self):
        self.path.unlink()
        self.assertEqual(self.b.profiles(), [])
        self.b.profile_create("default")
        self.assertEqual(self.file()["version"], 2)
        self.assertEqual(self.b.profiles()[0]["name"], "default")


    def test_updates_only_install_when_newer(self):
        u = self.b.updates
        calls = []
        u.fetch_latest = lambda opener=None: {"version": "4.1.0", "url": "x"}
        u.download = lambda version, opener=None: calls.append(version) or b"data"
        u.install = lambda data, expected, prefix=None: calls.append(expected) or "done"
        mine = {"path": "/x", "local": True, "owner": "standalone"}
        for have, state in ((None, "not_installed"), ({**mine, "version": "4.1.0"}, "up_to_date"),
                            ({**mine, "version": "4.2.0"}, "ahead"),
                            ({**mine, "version": "4.0.0", "owner": "decky"}, "decky"),
                            ({**mine, "version": "4.0.0", "local": False}, "system"),
                            ({**mine, "version": "4.0.0"}, "newer_available")):
            u.installed = lambda have=have: have
            with self.subTest(state=state):
                self.assertEqual(self.b.update_status()["state"], state)
                if state in ("up_to_date", "ahead", "decky", "system"):
                    with self.assertRaises(schema.InvalidArgument):
                        self.b.update_install()
                else:
                    self.assertEqual(self.b.update_install(), {"installed": "4.1.0"})
        self.assertEqual(calls, ["4.1.0", "4.1.0", "4.1.0", "4.1.0"])

    def test_a_new_major_version_is_never_installed(self):
        u = self.b.updates
        u.fetch_latest = lambda opener=None: {"version": "5.0.0", "url": "x"}
        u.installed = lambda: {"version": "4.0.0", "path": "/x", "local": True, "owner": "standalone"}
        u.download = lambda *a, **k: self.fail("must not download")
        st = self.b.update_status()
        self.assertEqual((st["state"], st["latest_compat"], st["installed_compat"]), ("newer_available", "unsupported", "tested"))
        with self.assertRaisesRegex(schema.InvalidArgument, "new major version"):
            self.b.update_install()

    def test_launch_option_like_mako_ui(self):
        self.assertEqual(self.b.launch_option("default")["line"], "MAKO_PROFILE='default' ~/.local/bin/mako-launch %command%")
        self.b.profile_create("it's")
        self.assertEqual(self.b.launch_option("it's")["line"], "MAKO_PROFILE='it'\\''s' ~/.local/bin/mako-launch %command%")
        self.assertEqual(self.b.launch_option()["line"], "~/.local/bin/mako-launch %command%")
        with self.assertRaises(schema.InvalidArgument):
            self.b.launch_option("ghost")

    def test_rename_and_delete_touch_only_conf_toml(self):
        before = sorted(p.name for p in self.path.parent.iterdir())
        self.b.profile_rename("default", "renamed")
        self.assertEqual(self.b.launch_option("renamed")["line"], "MAKO_PROFILE='renamed' ~/.local/bin/mako-launch %command%")
        self.b.profile_delete("renamed")
        after = sorted(p.name for p in self.path.parent.iterdir())
        self.assertEqual([n for n in after if n not in before], ["conf.toml.invasor-backup"])

    def test_launcher(self):
        import os
        os.environ["MAKO_LAUNCH_CONFIG"] = str(self.path.parent / "launcher.conf")
        self.addCleanup(os.environ.pop, "MAKO_LAUNCH_CONFIG")
        self.assertEqual(self.b.launcher_get(), {"enable_zink": False, "force_alsa_audio": False})
        self.assertTrue(self.b.launcher_set("enable_zink", True))
        self.assertEqual((self.path.parent / "launcher.conf").read_text(), "version=1\nenable_zink=1\nforce_alsa_audio=0\n")
        (self.path.parent / "launcher.conf").write_text("version=9\n")
        with self.assertRaises(schema.Unavailable):
            self.b.launcher_set("enable_zink", False)
        self.assertEqual((self.path.parent / "launcher.conf").read_text(), "version=9\n")  # never overwritten

    def fake_cli(self, code, message=""):
        p = self.path.parent / "fake-cli"
        p.write_text(f"#!/bin/sh\necho '{message}'\nexit {code}\n")
        p.chmod(0o755)
        self.b._cli = lambda: str(p)

    def test_mako_validates_every_change(self):
        before = self.path.read_text()
        self.fake_cli(1, "Validation failed: multiplier must be between 2 and 5")
        with self.assertRaisesRegex(schema.InvalidArgument, "MAKO rejected the change: multiplier must be"):
            self.b.profile_set("default", "multiplier", 3)
        self.assertEqual(self.path.read_text(), before)  # original untouched
        self.assertIn("multiplier must be", self.b.status()["mako_problem"])
        self.fake_cli(0, "Validation success")
        self.assertEqual(self.b.profile_set("default", "multiplier", 3), 3)
        self.assertEqual(self.file()["profile"][0]["multiplier"], 3)
        self.assertIsNone(self.b.status()["mako_problem"])

    def test_a_broken_validator_does_not_block(self):
        self.b._cli = lambda: str(self.path.parent / "missing-cli")
        with self.assertLogs("test.fishy", "WARNING"):
            self.assertEqual(self.b.profile_set("default", "multiplier", 4), 4)


    def test_older_core_without_the_validate_hook(self):
        real_save = self.b.ctx.toml.save

        class OldToml:
            dumps = staticmethod(self.b.ctx.toml.dumps)
            load = staticmethod(self.b.ctx.toml.load)

            @staticmethod
            def save(path, data, expected_mtime=None, backup=True):
                return real_save(path, data, expected_mtime, backup)

        self.b.ctx.toml = OldToml
        before = self.path.read_text()
        self.fake_cli(1, "Validation failed: unknown pacing method: x")
        with self.assertRaisesRegex(schema.InvalidArgument, "rejected"):
            self.b.profile_set("default", "multiplier", 5)
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual([p.name for p in self.path.parent.iterdir() if ".check-" in p.name], [])
        self.fake_cli(0)
        self.assertEqual(self.b.profile_set("default", "multiplier", 5), 5)


if __name__ == "__main__":
    unittest.main()
