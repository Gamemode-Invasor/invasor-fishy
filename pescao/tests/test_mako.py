import unittest
from pathlib import Path

import mako


def config():
    return {"version": 2, "global": {"allow_fp16": True}, "profile": [
        {"active_in": ["1000", "Other.exe"], "multiplier": 2, "name": "default"},
        {"multiplier": 3, "name": "3x"},
    ]}


class Paths(unittest.TestCase):
    def test_config_path(self):
        self.assertEqual(mako.config_path({"MAKO_CONFIG": "/x/c.toml"}), Path("/x/c.toml"))
        self.assertEqual(mako.config_path({"XDG_CONFIG_HOME": "/cfg"}), Path("/cfg/mako-render/conf.toml"))
        self.assertEqual(mako.config_path({}), Path.home() / ".config/mako-render/conf.toml")


class Profiles(unittest.TestCase):
    def test_check(self):
        mako.check(config())
        for bad in ({"version": 1}, {"version": 2, "global": []}, {"version": 2, "profile": {"name": "x"}}):
            with self.subTest(data=bad), self.assertRaises(ValueError):
                mako.check(bad)

    def test_create_copy_rename_delete(self):
        data = config()
        p = mako.create(data, " New ", {"multiplier": 2, "flow_scale": 1.0})
        self.assertEqual(list(p), ["flow_scale", "multiplier", "name", "pacing"])  # alphabetical, like mako-ui
        self.assertEqual(p["name"], "New")
        copy = mako.create(data, "Copy", {}, copy_from="default")
        self.assertNotIn("active_in", copy)  # its games stay with the original
        self.assertEqual(copy["multiplier"], 2)
        mako.rename(data, "Copy", "Renamed")
        self.assertEqual(mako.names(data), ["default", "3x", "New", "Renamed"])
        mako.delete(data, "Renamed")
        self.assertNotIn("Renamed", mako.names(data))
        for bad in ("", "  ", "3x", "x" * 65):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                mako.create(data, bad, {})
        with self.assertRaises(KeyError):
            mako.rename(data, "ghost", "z")

    def test_values_and_global(self):
        data = config()
        mako.set_value(mako.find(data, "3x"), "flow_scale", 0.5)
        self.assertEqual(mako.find(data, "3x")["flow_scale"], 0.5)
        for key in ("name", "active_in"):
            with self.assertRaises(ValueError):
                mako.set_value(mako.find(data, "3x"), key, "x")
        mako.set_global(data, "dll", "/x/Lossless.dll")
        self.assertEqual(data["global"]["dll"], "/x/Lossless.dll")
        mako.set_global(data, "dll", "")
        self.assertNotIn("dll", data["global"])
        mako.set_value(mako.find(data, "3x"), "gpu", "AMD")
        self.assertEqual(mako.find(data, "3x")["gpu"], "AMD")
        mako.set_value(mako.find(data, "3x"), "gpu", "")
        self.assertNotIn("gpu", mako.find(data, "3x"))  # empty = automatic: left out
        self.assertNotIn("gpu", mako.create(data, "Fresh", {"gpu": "", "multiplier": 2}))


class PerGame(unittest.TestCase):
    def test_entries_are_numeric_ids_for_steam_and_shortcuts(self):
        self.assertEqual(mako.game_entry("1245620"), "1245620")
        self.assertEqual(mako.game_entry(3000000001, True), "3000000001")
        for bad in ("../1", "", "Game.exe", "12a"):
            with self.subTest(appid=bad), self.assertRaises(ValueError):
                mako.game_entry(bad)

    def test_one_profile_per_game_and_other_entries_untouched(self):
        data = config()
        self.assertEqual(mako.game_profile(data, "1000"), "default")
        mako.assign(data, "1000", "3x")
        self.assertEqual(mako.game_profile(data, "1000"), "3x")
        self.assertEqual(mako.find(data, "default")["active_in"], ["Other.exe"])  # not ours: kept
        mako.assign(data, "1000", "3x")  # twice: still once
        self.assertEqual(mako.find(data, "3x")["active_in"], ["1000"])
        mako.assign(data, "1000", None)
        self.assertIsNone(mako.game_profile(data, "1000"))
        self.assertNotIn("active_in", mako.find(data, "3x"))  # empty list removed
        with self.assertRaises(KeyError):
            mako.assign(data, "1000", "ghost")



class Custom(unittest.TestCase):
    def test_own_profile_copies_the_current_one(self):
        data = config()  # "1000" uses "default" (shared with Other.exe)
        name = mako.make_custom(data, "1000", "Test Game", {"multiplier": 9})
        self.assertEqual(name, "Test Game")
        own = mako.find(data, "Test Game")
        self.assertEqual((own["multiplier"], own["active_in"]), (2, ["1000"]))  # copied from "default"
        self.assertEqual(mako.find(data, "default")["active_in"], ["Other.exe"])
        self.assertTrue(mako.is_custom(data, "Test Game", "1000"))

    def test_already_own_is_kept_and_defaults_when_no_profile(self):
        data = config()
        first = mako.make_custom(data, "1000", "Test Game", {})
        self.assertEqual(mako.make_custom(data, "1000", "Renamed In Steam", {}), first)  # no duplicate
        fresh = mako.make_custom(data, "2000", "Other Game", {"multiplier": 4, "flow_scale": 1.0})
        self.assertEqual(mako.find(data, fresh)["multiplier"], 4)

    def test_names_are_unique_and_bounded(self):
        data = config()
        self.assertEqual(mako.unique_name(data, "3x"), "3x (2)")
        mako.create(data, "3x (2)", {})
        self.assertEqual(mako.unique_name(data, "3x"), "3x (3)")
        self.assertLessEqual(len(mako.unique_name(data, "x" * 200)), mako.MAX_NAME)
        self.assertEqual(mako.unique_name(data, "  "), "Game")
        self.assertFalse(mako.is_custom(data, "default", "1000"))  # shared with Other.exe



class StringActiveIn(unittest.TestCase):
    """mako-ui may write a single game as a plain string: active_in = "878670"."""

    def data(self):
        return {"version": 2, "profile": [{"active_in": "878670", "name": "default"}, {"name": "2x"}]}

    def test_whole_entries_only(self):
        data = self.data()
        self.assertEqual(mako.game_profile(data, "878670"), "default")
        self.assertIsNone(mako.game_profile(data, "87"))  # not a substring match
        self.assertTrue(mako.is_custom(data, "default", "878670"))

    def test_assign_and_remove(self):
        data = self.data()
        mako.assign(data, "1000", "default")
        self.assertEqual(mako.find(data, "default")["active_in"], ["878670", "1000"])
        data = self.data()
        mako.assign(data, "878670", "2x")
        self.assertNotIn("active_in", mako.find(data, "default"))
        self.assertEqual(mako.find(data, "2x")["active_in"], ["878670"])
        self.assertEqual(mako.games({"active_in": 5}), [])



class OffAndOn(unittest.TestCase):
    def test_turning_back_on_reuses_the_games_profile(self):
        data = config()
        name = mako.make_custom(data, "2000", "Some Game", {"multiplier": 2})
        mako.set_value(mako.find(data, name), "multiplier", 5)
        mako.assign(data, "2000", None)  # off: the profile stays, unused
        self.assertEqual(mako.make_custom(data, "2000", "Some Game", {"multiplier": 2}), "Some Game")
        self.assertEqual(mako.find(data, "Some Game")["multiplier"], 5)
        self.assertEqual(mako.names(data).count("Some Game"), 1)



class MakoUiRules(unittest.TestCase):
    """The side effects mako-ui (MAKO 4.0) applies when an option changes."""

    def profile(self, **kw):
        data = {"version": 2, "global": {"allow_fp16": False}, "profile": [{"name": "p", "flow_scale": 0.5, "performance_mode": False, **kw}]}
        return data, data["profile"][0]

    def test_ultra_performance_is_a_locking_preset(self):
        data, p = self.profile(scaling_enabled=True)
        self.assertEqual(mako.change(data, p, "ultra_performance", True), ["allow_fp16", "flow_scale", "performance_mode"])
        self.assertEqual((p["flow_scale"], p["performance_mode"], data["global"]["allow_fp16"]), (0.7, True, True))
        for key, value in (("flow_scale", 0.9), ("performance_mode", False), ("scaling_method", "mako")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Ultra performance"):
                mako.change(data, p, key, value)
        mako.change(data, p, "ultra_performance", False)
        self.assertEqual((p["flow_scale"], p["performance_mode"]), (0.8, False))
        mako.change(data, p, "flow_scale", 0.9)
        self.assertEqual(p["flow_scale"], 0.9)

    def test_scaling_method_is_free_without_scaling(self):
        data, p = self.profile(ultra_performance=True, scaling_enabled=False)
        mako.change(data, p, "scaling_method", "mako")
        self.assertEqual(p["scaling_method"], "mako")

    def test_cadence_recovery_and_real_frame_caps_exclude_each_other(self):
        data, p = self.profile(adaptive_auto_base_fps_cap=True, adaptive_fractional_real_frame_priority="high", base_fps_cap=60)
        mako.change(data, p, "dynamic_cadence_recovery", True)
        self.assertEqual((p["adaptive_auto_base_fps_cap"], p["adaptive_fractional_real_frame_priority"], p["base_fps_cap"]), (False, "auto", 0))
        for key, value in (("base_fps_cap", 30), ("adaptive_auto_base_fps_cap", True), ("adaptive_fractional_real_frame_priority", "low")):
            p["dynamic_cadence_recovery"] = True
            with self.subTest(key=key):
                mako.change(data, p, key, value)
                self.assertFalse(p["dynamic_cadence_recovery"])
        p["dynamic_cadence_recovery"] = True
        mako.change(data, p, "base_fps_cap", 0)  # removing a cap doesn't
        self.assertTrue(p["dynamic_cadence_recovery"])


if __name__ == "__main__":
    unittest.main()
