"""mako-launch's own settings (launcher.conf), in its strict format.

Pure stdlib, no Invasor imports: tested on its own (tests/test_launcher.py). Ported from
MAKO render-v4.0.0 engine/mako-common/src/configuration/launch.cpp. These apply to every
game started through mako-launch, at its next start; they never go in conf.toml.

    version=1
    enable_zink=0|1
    force_alsa_audio=0|1
"""
import os
import tempfile
from pathlib import Path

KEYS = ("enable_zink", "force_alsa_audio")


class Invalid(ValueError):
    """The file isn't in mako-launch's format (mako-launch then ignores it too)."""


def path(env=None):
    env = os.environ if env is None else env
    if env.get("MAKO_LAUNCH_CONFIG"):
        return Path(env["MAKO_LAUNCH_CONFIG"])
    if env.get("XDG_CONFIG_HOME"):
        return Path(env["XDG_CONFIG_HOME"]) / "mako-render" / "launcher.conf"
    return Path(env.get("HOME") or Path.home()) / ".config" / "mako-render" / "launcher.conf"


def parse(text):
    """{enable_zink, force_alsa_audio} from launcher.conf's text; Invalid as mako-launch would."""
    out = {k: False for k in KEYS}
    seen = set()
    for line in text.split("\n"):
        content = line.strip(" \t\r\n")
        if not content or content.startswith("#"):
            continue
        if content.count("=") != 1:
            raise Invalid("invalid launcher configuration line")
        key, value = (p.strip(" \t\r\n") for p in content.split("="))
        if key in seen:
            raise Invalid(f"duplicate {key} launcher setting")
        seen.add(key)
        if key == "version":
            if value != "1":
                raise Invalid("unsupported launcher configuration version")
        elif key in KEYS:
            if value not in ("0", "1"):
                raise Invalid(f"launcher setting {key} must be 0 or 1")
            out[key] = value == "1"
        else:
            raise Invalid(f"unknown launcher setting: {key}")
    if "version" not in seen:
        raise Invalid("launcher configuration version is missing")
    return out


def dumps(settings):
    return "version=1\n" + "".join(f"{k}={int(bool(settings[k]))}\n" for k in KEYS)


def load(p=None):
    """The settings (defaults if the file doesn't exist). Invalid if it's broken."""
    try:
        return parse(Path(p or path()).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {k: False for k in KEYS}


def save(settings, p=None):
    p = Path(p or path())
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = p.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps(settings))
        os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        os.unlink(tmp)
        raise
