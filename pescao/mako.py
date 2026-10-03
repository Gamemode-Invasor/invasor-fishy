"""MAKO Renderer's configuration (v2), as plain data: where it lives and how its profiles
and per-game matching (`active_in`) are changed. No I/O besides finding paths; tested
on its own (tests/test_mako.py).

    version = 2
    [global]            allow_fp16, dll (optional)
    [[profile]]         name, active_in = ["<steam appid>", "Game.exe", …], multiplier, …

MAKO applies the profile whose `active_in` contains the game's Steam app id (as a
string) or its executable/process name (MAKO_PROFILE, set at launch, wins over that).
A game should be in one profile at most. The same format as lsfg-vk 2, which MAKO
comes from; mako-ui's own side files (profile-metadata.json…) aren't touched.
"""
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

VERSION = 2
LAYER_FILE = "VkLayer_MAKO_render.json"
LAYER_DIRS = (
    Path.home() / ".local/share/vulkan/implicit_layer.d",
    Path("/usr/share/vulkan/implicit_layer.d"),
    Path("/usr/local/share/vulkan/implicit_layer.d"),
    Path("/etc/vulkan/implicit_layer.d"),
)
MAX_NAME = 64


def config_path(env=None):
    """Where MAKO reads its config: $MAKO_CONFIG, else $XDG_CONFIG_HOME/mako-render/conf.toml."""
    env = os.environ if env is None else env
    if env.get("MAKO_CONFIG"):
        return Path(env["MAKO_CONFIG"])
    base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "mako-render" / "conf.toml"


def installed(dirs=LAYER_DIRS):
    return any((d / LAYER_FILE).is_file() for d in dirs)


def check(data):
    """Raise ValueError unless `data` is a config we can safely change (v2, right shape)."""
    if data.get("version", VERSION) != VERSION:
        raise ValueError(f"MAKO config version {data.get('version')!r} isn't supported (only {VERSION})")
    if not isinstance(data.get("global", {}), dict):
        raise ValueError("MAKO config: [global] isn't a table")
    profiles = data.get("profile", [])
    if not isinstance(profiles, list) or not all(isinstance(p, dict) for p in profiles):
        raise ValueError("MAKO config: profiles aren't [[profile]] tables")


def new_config():
    return {"version": VERSION, "global": {}, "profile": []}


def profiles(data):
    return data.setdefault("profile", [])


def names(data):
    return [p.get("name") for p in data.get("profile", []) if isinstance(p.get("name"), str)]


def find(data, name):
    for p in data.get("profile", []):
        if p.get("name") == name:
            return p
    raise KeyError(f"no profile named {name!r}")


def check_name(data, name, current=None):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("a profile needs a name")
    name = name.strip()
    if len(name) > MAX_NAME:
        raise ValueError(f"profile names are {MAX_NAME} characters at most")
    if name != current and name in names(data):
        raise ValueError(f"there's already a profile named {name!r}")
    return name


def create(data, name, values, copy_from=None):
    """Add a profile: a copy of `copy_from` (without its games) or `values`. Keys are
    written alphabetically, as mako-ui does."""
    name = check_name(data, name)
    if copy_from:
        base = dict(find(data, copy_from))
    else:
        base = {**FIXED, **{k: v for k, v in values.items() if not (k in OPTIONAL_TEXT and v == "")}}
    base.pop("active_in", None)
    base["name"] = name
    profile = {k: base[k] for k in sorted(base)}
    profiles(data).append(profile)
    return profile


def rename(data, old, new):
    """Rename a profile; returns its new (trimmed) name."""
    profile = find(data, old)
    profile["name"] = check_name(data, new, current=old)
    return profile["name"]


def delete(data, name):
    data["profile"] = [p for p in data.get("profile", []) if p.get("name") != name]


# Profile keys mako-ui writes in every new profile that aren't options here ("none" is
# the only pacing MAKO supports).
FIXED = {"pacing": "none"}

# Text options where empty means "automatic": the key is left out instead.
OPTIONAL_TEXT = ("gpu", "dll")


def set_value(profile, key, value):
    if key in ("name", "active_in"):
        raise ValueError(f"{key} isn't changed this way")
    if key in OPTIONAL_TEXT and value == "":
        profile.pop(key, None)  # empty = let MAKO choose the GPU
    else:
        profile[key] = value


# Ultra performance is a preset: it sets these and locks them while it's on (as mako-ui does).
ULTRA_FLOW_SCALE = 0.7
DEFAULT_FLOW_SCALE = 0.8


def change(data, profile, key, value):
    """Set one profile option with the side effects mako-ui (MAKO 4.0) applies, so a
    profile edited here ends up as mako-ui would leave it. ValueError if the option is
    locked by another one. Returns the keys changed besides `key`."""
    also = {}
    if key in ("flow_scale", "performance_mode") and profile.get("ultra_performance"):
        raise ValueError(f"{key} is set by Ultra performance: turn that off first")
    if key == "scaling_method" and profile.get("ultra_performance") and profile.get("scaling_enabled"):
        raise ValueError("the scaling method is set by Ultra performance: turn that off first")
    if key == "ultra_performance":
        also = {"flow_scale": ULTRA_FLOW_SCALE if value else DEFAULT_FLOW_SCALE, "performance_mode": bool(value)}
        data.setdefault("global", {})["allow_fp16"] = True  # mako-ui turns FP16 on either way
    elif key == "dynamic_cadence_recovery" and value:
        # Recovery rechecks the native cadence: real-frame caps would fight it.
        also = {"adaptive_auto_base_fps_cap": False, "adaptive_fractional_real_frame_priority": "auto", "base_fps_cap": 0}
    elif (key == "base_fps_cap" and value > 0) or (key == "adaptive_auto_base_fps_cap" and value) \
            or key == "adaptive_fractional_real_frame_priority":
        also = {"dynamic_cadence_recovery": False}
    set_value(profile, key, value)
    for k, v in also.items():
        profile[k] = v
    return sorted([*also, *(["allow_fp16"] if key == "ultra_performance" else [])])


def set_global(data, key, value):
    table = data.setdefault("global", {})
    if key == "dll" and value == "":
        table.pop("dll", None)  # empty = let MAKO find Lossless.dll itself
    else:
        table[key] = value


def game_entry(appid, shortcut=False, exe=None):
    """What goes in `active_in` for a game: its id as a string, for Steam games and
    non-Steam shortcuts alike (the shortcut's id from shortcuts.vdf). Never a title."""
    appid = str(appid)
    if not (appid.isascii() and appid.isdigit()) or len(appid) > 20:
        raise ValueError(f"invalid appid {appid!r}")
    return str(int(appid))


def games(profile):
    """A profile's active_in as a list. MAKO accepts one entry as a plain string
    (`active_in = "1245620"`, as mako-ui may write it) or a list of them."""
    value = profile.get("active_in")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [g for g in value if isinstance(g, str)]
    return []


def unique_name(data, base):
    """`base` as a profile name, made unique with " (2)", " (3)"… if needed."""
    base = (base or "").strip()[: MAX_NAME - 5] or "Game"
    taken = set(names(data))
    name, n = base, 2
    while name in taken:
        name, n = f"{base} ({n})", n + 1
    return name


def is_custom(data, name, entry):
    """A profile is a game's own when that game is the only entry in its active_in
    (a convention: nothing extra is stored in MAKO's file)."""
    try:
        return games(find(data, name)) == [entry]
    except KeyError:
        return False


def make_custom(data, entry, title, defaults):
    """Give the game its own profile, named after it: a copy of the profile it uses (or
    the defaults), with only this game in active_in. Returns its name. If the game's
    profile is already its own, that one is kept."""
    current = game_profile(data, entry)
    if current is not None and is_custom(data, current, entry):
        return current
    # Turned off and on again: its old profile (named after it, now unused) comes back
    # with the values it had.
    base = (title or "").strip()[: MAX_NAME - 5]
    if current is None and base in names(data) and not games(find(data, base)):
        assign(data, entry, base)
        return base
    name = unique_name(data, title)
    create(data, name, defaults, copy_from=current)
    assign(data, entry, name)
    return name


def game_profile(data, entry):
    """Name of the profile whose active_in has `entry`, or None."""
    for p in data.get("profile", []):
        if entry in games(p):  # whole entries: "87" never matches "878670"
            return p.get("name")
    return None


def assign(data, entry, name):
    """Make `entry` use profile `name` (None = no profile): removed from every other
    profile first, so a game is in one at most. Other active_in entries are untouched."""
    target = find(data, name) if name is not None else None
    for p in data.get("profile", []):
        current = games(p)
        if entry in current:
            rest = [g for g in current if g != entry]
            if rest:
                p["active_in"] = rest
            else:
                del p["active_in"]
    if target is not None:
        target["active_in"] = [*games(target), entry]  # a plain-string entry becomes a list
