"""Pescao: MAKO Renderer profiles (frame generation, scaling) from Invasor's panel.

The source of truth is MAKO's own conf.toml (mako.config_path()), never Invasor's
settings: every call reads it, changes what was asked, and writes it back with
ctx.toml (all keys kept, verified, atomic, refused if mako-ui saved meanwhile).
A game uses a profile when its id (Steam or non-Steam shortcut) is in that profile's
active_in, and MAKO only runs in games launched through mako-launch.
"""
import inspect
import os
import tempfile
from pathlib import Path

from . import launcher, mako, updates, validator

ctx = None


def setup(context):
    global ctx
    ctx = context


def _msg(e):
    return str(e.args[0]) if e.args else str(e)


def _read():
    path = mako.config_path()
    data, mtime = ctx.toml.load(path)
    if not data:
        data = mako.new_config()
    try:
        mako.check(data)
    except ValueError as e:
        raise ctx.Unavailable(str(e)) from None
    return path, data, mtime


_cli_missing_logged = False


def _cli():
    """mako-cli of the MAKO in use, or None (then Pescao's own checks are all there is)."""
    global _cli_missing_logged
    found = updates.find_layer()
    cli = validator.find_cli(found[1] if found else None)
    if cli is None and not _cli_missing_logged:
        _cli_missing_logged = True
        ctx.log.info("mako-cli not found: conf.toml changes are checked by Pescao only")
    return cli


def _mako_validate(tmp_path):
    """ctx.toml.save hook: MAKO's own validator judges the new file before it replaces
    conf.toml. A validator that can't run doesn't block the change."""
    cli = _cli()
    if cli is None:
        return
    try:
        validator.validate(tmp_path, cli)
    except validator.Rejected as e:
        raise ctx.InvalidArgument(f"MAKO rejected the change: {e}") from None
    except (OSError, TimeoutError) as e:
        ctx.log.warning("mako-cli couldn't validate (%s): saving with Pescao's own checks", e)


def mako_problem(path=None):
    """MAKO's complaint about the current conf.toml, or None if it accepts it (or can't
    be asked)."""
    path = path or mako.config_path()
    cli = _cli()
    if cli is None or not path.exists():
        return None
    try:
        validator.validate(path, cli)
    except validator.Rejected as e:
        return str(e)
    except (OSError, TimeoutError):
        return None
    return None


def _change(fn):
    """Read, apply fn(data), write back (checked by MAKO itself when it can).
    ValueError/KeyError from fn -> clean 400."""
    path, data, mtime = _read()
    try:
        result = fn(data)
    except (ValueError, KeyError) as e:
        raise ctx.InvalidArgument(_msg(e)) from None
    _save(path, data, mtime)
    return result


def _save(path, data, mtime):
    """ctx.toml.save with MAKO's validation. Invasor cores older than the `validate`
    hook get the same check here: the new file is written aside, validated, removed."""
    if "validate" in inspect.signature(ctx.toml.save).parameters:
        ctx.toml.save(path, data, mtime, validate=_mako_validate)
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.check-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ctx.toml.dumps(data))
        _mako_validate(Path(tmp))
    finally:
        os.unlink(tmp)
    ctx.toml.save(path, data, mtime)


def status():
    path = mako.config_path()
    out = {"installed": mako.installed(), "path": str(path), "exists": path.exists(), "error": None, "mako_problem": None}
    try:
        _read()
    except (ctx.Unavailable, ctx.InvalidArgument) as e:
        out["error"] = str(e)
    else:
        out["mako_problem"] = mako_problem(path)
    return out


def profiles():
    """[{name, games: [{entry, name}]}]: every profile and the games that use it."""
    _, data, _ = _read()
    out = []
    for p in data.get("profile", []):
        games = []
        for entry in mako.games(p):
            label = None
            if isinstance(entry, str) and entry.isascii() and entry.isdigit():
                label = (ctx.game.info(entry) or {}).get("name")
            games.append({"entry": entry, "name": label})
        out.append({"name": p.get("name"), "games": games})
    return out


def profile_get(name):
    _, data, _ = _read()
    try:
        return ctx.forms["profile"].clean(mako.find(data, name))
    except KeyError as e:
        raise ctx.InvalidArgument(_msg(e)) from None


def profile_set(name, key, value):
    """Set one option; options that depend on it change too, as in mako-ui (mako.change)."""
    value = ctx.forms["profile"].coerce(key, value)
    _change(lambda data: mako.change(data, mako.find(data, name), key, value))
    return value


def profile_create(name, copy_from=None):
    _change(lambda data: mako.create(data, name, ctx.forms["profile"].defaults(), copy_from))
    return True


def profile_rename(old, new):
    _change(lambda data: mako.rename(data, old, new))
    return True


def profile_delete(name):
    _change(lambda data: (mako.find(data, name), mako.delete(data, name)))
    return True


def global_get():
    _, data, _ = _read()
    values = ctx.forms["global"].clean(data.get("global", {}))
    return values


def global_set(key, value):
    value = ctx.forms["global"].coerce(key, value)
    _change(lambda data: mako.set_global(data, key, value))
    return value


def _entry(appid, shortcut):
    try:
        return mako.game_entry(appid, shortcut)
    except ValueError as e:
        raise ctx.InvalidArgument(str(e)) from None


def game_profile(appid, shortcut=False):
    """{entry, profile, custom}: what identifies the game in active_in, its profile (or
    None), and whether that profile is the game's own (only this game uses it)."""
    entry = _entry(appid, shortcut)
    _, data, _ = _read()
    name = mako.game_profile(data, entry)
    return {"entry": entry, "profile": name, "custom": name is not None and mako.is_custom(data, name, entry)}


def make_custom(appid, shortcut=False, title=None):
    """Give the game its own profile (named after it, starting from its current values)."""
    entry = _entry(appid, shortcut)
    title = title or (ctx.game.info(appid) or {}).get("name") or entry
    name = _change(lambda data: mako.make_custom(data, entry, title, ctx.forms["profile"].defaults()))
    ctx.log.info("game %s (%s) -> its own profile %r", appid, entry, name)
    return {"entry": entry, "profile": name, "custom": True}


def set_game_profile(appid, shortcut=False, name=None):
    """Use profile `name` for the game from its next launch; None turns it off."""
    entry = _entry(appid, shortcut)
    _change(lambda data: mako.assign(data, entry, name))
    ctx.log.info("game %s (%s) -> profile %r", appid, entry, name)
    return {"entry": entry, "profile": name}


def _profile(name):
    _, data, _ = _read()
    try:
        return mako.find(data, name)
    except KeyError as e:
        raise ctx.InvalidArgument(_msg(e)) from None


def _quote(value):
    """Shell quoting as mako-ui does it: always single quotes."""
    return "'" + str(value).replace("'", "'\\''") + "'"


# ---------- mako-launch's own settings ----------

def launcher_get():
    try:
        return launcher.load()
    except launcher.Invalid as e:
        raise ctx.Unavailable(f"{launcher.path()} isn't valid ({e}): mako-launch ignores it; fix or remove it") from None


def launcher_set(key, value):
    value = ctx.forms["launcher"].coerce(key, value)
    settings = launcher_get()
    settings[key] = value
    try:
        launcher.save(settings)
    except OSError as e:
        raise ctx.Unavailable(f"can't write {launcher.path()}: {e}") from None
    return value


def launch_option(name=None):
    """{line, found}: the launch option for a profile, as mako-ui builds it, and whether
    mako-launch is installed. MAKO_PROFILE is what makes MAKO use the profile: MAKO 4.0
    matches active_in against executables and process names, not Steam ids."""
    parts = []
    if name is not None:
        _profile(name)
        parts.append(f"MAKO_PROFILE={_quote(name)}")
    parts.append("~/.local/bin/mako-launch %command%")
    return {"line": " ".join(parts), "found": (updates.PREFIX / "bin" / "mako-launch").is_file()}


def update_status():
    """{installed, latest, state, …}: the MAKO Renderer version MAKO itself reports and the
    latest stable on GitHub (asked only now, never in the background).
    state: newer_available, up_to_date, ahead (installed is newer: nothing offered),
    decky (managed by MAKO Decky), system (outside ~/.local), not_installed."""
    have = updates.installed()
    try:
        latest = updates.fetch_latest()["version"]
    except updates.UpdateError as e:
        raise ctx.Unavailable(str(e)) from None
    if have is None:
        state = "not_installed"
    elif not have["local"]:
        state = "system"
    elif have["owner"] == "decky":
        state = "decky"
    else:
        state = updates.compare(have["version"], latest)
    return {
        "installed": have and have["version"],
        "path": have and have["path"],
        "latest": latest,
        "state": state,
        "tested": "%d.%d" % updates.TESTED,
        "installed_compat": updates.compatibility(have and have["version"]),
        "latest_compat": updates.compatibility(latest),
    }


def compat():
    """{installed, compat, tested}: whether the installed MAKO is the series Pescao was
    tested with (no network)."""
    have = updates.installed()
    version = have and have["version"]
    return {"installed": version, "compat": updates.compatibility(version), "tested": "%d.%d" % updates.TESTED}


def update_install():
    """Download the latest stable MAKO Renderer and install it with MAKO's own installer,
    only when it's newer than what's installed (or MAKO isn't installed)."""
    status = update_status()
    if status["state"] not in ("newer_available", "not_installed"):
        raise ctx.InvalidArgument({
            "up_to_date": "MAKO Renderer is already up to date",
            "ahead": "the installed MAKO Renderer is newer than the latest stable",
            "decky": "MAKO Renderer is managed by MAKO Decky: update it there",
            "system": "MAKO Renderer is installed by the system, not in ~/.local: update it there",
        }[status["state"]])
    if status["latest_compat"] == "unsupported":
        raise ctx.InvalidArgument(f"MAKO Renderer {status['latest']} is a new major version: update Pescao first")
    try:
        said = updates.install(updates.download(status["latest"]), status["latest"])
    except updates.UpdateError as e:
        raise ctx.Unavailable(str(e)) from None
    ctx.log.info("installed MAKO Renderer %s into %s (%s)", status["latest"], updates.PREFIX, said)
    return {"installed": status["latest"]}


METHODS = {
    "status": status,
    "profiles": profiles,
    "profile_get": profile_get,
    "profile_set": profile_set,
    "profile_create": profile_create,
    "profile_rename": profile_rename,
    "profile_delete": profile_delete,
    "global_get": global_get,
    "global_set": global_set,
    "game_profile": game_profile,
    "set_game_profile": set_game_profile,
    "make_custom": make_custom,
    "launch_option": launch_option,
    "launcher_get": launcher_get,
    "launcher_set": launcher_set,
    "update_status": update_status,
    "compat": compat,
    "update_install": update_install,
}
