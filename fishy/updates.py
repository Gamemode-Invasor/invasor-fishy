"""MAKO Renderer updates: find the latest stable Renderer release on GitHub and install
it with MAKO's own installer, the way MAKO documents for standalone installs ("run the
installer again to update").

Pure stdlib, no Invasor imports: tested on its own (tests/test_updates.py).

- What's installed is read from MAKO itself: its layer library carries its version as
  a string ("4.0.0"), and ~/.local/share/mako-render/active-renderer.json (written by
  both of MAKO's installers) says who owns it, "standalone" or "decky". A Renderer
  managed by MAKO Decky (or installed outside ~/.local) is never touched.
- Releases: GitHub's release list for eugeniosegala/MAKO. Renderer releases are tagged
  render-vX.Y.Z (MAKO Decky's are plugin-v…); drafts and pre-releases are skipped.
- Installing: every archive entry is checked first (relative paths, regular files and
  folders only, nothing outside what a Renderer archive holds), the archive is unpacked
  to a temporary folder, its version file must say the expected version, and then its
  own "Install MAKO Renderer" runs without questions (MAKO_INSTALLER_ASSUME_YES=1,
  MAKO_INSTALLER_NO_LAUNCH=1, no display). That installer checks each file against its
  manifest, installs in one transaction with rollback and keeps the profiles.
"""
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

REPO = "eugeniosegala/MAKO"
RELEASES = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
# GitHub serves release files from github.com, which redirects to its asset hosts.
HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
USER_AGENT = "invasor-fishy"
TIMEOUT = 20
INSTALL_TIMEOUT = 180
MAX_DOWNLOAD = 64 << 20
PREFIX = Path.home() / ".local"
LAYER_JSON = "VkLayer_MAKO_render.json"
STATE = "share/mako-render/active-renderer.json"
# Where Vulkan looks for implicit layers: the user's first, then the system's.
LAYER_DIRS = (
    PREFIX / "share/vulkan/implicit_layer.d",
    Path("/usr/local/share/vulkan/implicit_layer.d"),
    Path("/usr/share/vulkan/implicit_layer.d"),
    Path("/etc/vulkan/implicit_layer.d"),
)

# What a Renderer archive holds at its top level.
INSTALLER = "Install MAKO Renderer"
VERSION_FILE = "MAKO-Renderer-version.txt"
MANIFEST = "MAKO-Renderer-install-manifest.txt"
TOP_DIRS = ("bin", "lib", "lib32", "share")
TOP_FILES = (INSTALLER, VERSION_FILE, MANIFEST, "README.txt")

TAG = re.compile(r"^render-v(\d+\.\d+\.\d+)$")
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?")
# Version strings inside the layer library: whole NUL-separated strings.
EMBEDDED = re.compile(rb"(?<![\w.-])(\d{1,3}\.\d{1,3}\.\d{1,3}(?:-[0-9A-Za-z.]{1,20})?)\x00")

# The MAKO series this Fishy was written and tested against. Same major, newer minor:
# usable (new options just don't show here). Another major: never installed by Fishy.
TESTED = (4, 0)
SUPPORTED_MAJOR = 4


class UpdateError(Exception):
    pass


def parse_version(text):
    """Sortable version, or None: 4.0.0-rc1 < 4.0.0 < 4.0.1."""
    m = VERSION.search(text or "")
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    stage = (0, pre) if pre else (1, "")  # a pre-release sorts before its release
    return (int(major), int(minor), int(patch), stage)


def compatibility(version):
    """"tested", "newer_minor", "older" or "unsupported" (another major version); None if unknown."""
    v = parse_version(version)
    if v is None:
        return None
    if v[0] != SUPPORTED_MAJOR:
        return "unsupported"
    if (v[0], v[1]) == TESTED:
        return "tested"
    return "newer_minor" if (v[0], v[1]) > TESTED else "older"


def compare(installed, latest):
    """"newer_available", "up_to_date" or "ahead" (installed is past the latest stable).
    An unknown installed version can always take the stable."""
    new = parse_version(latest)
    if new is None:
        raise UpdateError(f"can't read the latest version {latest!r}")
    have = parse_version(installed)
    if have is None or have < new:
        return "newer_available"
    return "up_to_date" if have == new else "ahead"


def asset_name(version):
    return f"MAKO-Renderer-v{version}-linux.tar.xz"


def latest_stable(releases):
    """{version, url} of the newest stable Renderer release in GitHub's release list."""
    best = None
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict) or r.get("draft") or r.get("prerelease"):
            continue
        m = TAG.match(str(r.get("tag_name", "")))
        if not m:
            continue  # MAKO Decky's releases and anything else
        version = m.group(1)
        url = next((a.get("browser_download_url") for a in r.get("assets") or []
                    if isinstance(a, dict) and a.get("name") == asset_name(version)), None)
        if url != f"{DOWNLOAD_PREFIX}render-v{version}/{asset_name(version)}":
            continue  # no Linux archive (or an unexpected link)
        if best is None or parse_version(version) > parse_version(best["version"]):
            best = {"version": version, "url": url}
    if best is None:
        raise UpdateError("GitHub lists no stable MAKO Renderer release")
    return best


# ---------- what's installed ----------

def library_versions(path):
    """Every version-looking string MAKO compiled into its layer library."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    return [m.group(1).decode() for m in EMBEDDED.finditer(data)]


def find_layer(dirs=LAYER_DIRS):
    """(layer json, library path) of the MAKO layer Vulkan finds first, or None."""
    for d in dirs:
        manifest = Path(d) / LAYER_JSON
        try:
            lib = json.loads(manifest.read_text())["layer"]["library_path"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        lib_path = Path(lib) if os.path.isabs(lib) else (manifest.parent / lib)
        return manifest, Path(os.path.normpath(lib_path))
    return None


def state(prefix=PREFIX):
    """active-renderer.json ({owner, version}) or {}."""
    try:
        data = json.loads((Path(prefix) / STATE).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def installed(dirs=LAYER_DIRS, prefix=PREFIX):
    """{version, path, local, owner}: MAKO's version (from its library, else from
    active-renderer.json; None if unknown), the library's path, whether it lives under
    ~/.local, and who installed it ("standalone", "decky" or None). None if MAKO isn't
    installed."""
    found = find_layer(dirs)
    if not found:
        return None
    _, lib = found
    try:
        local = Path(lib).resolve().is_relative_to(Path(prefix).resolve())
    except OSError:
        local = False
    st = state(prefix) if local else {}
    recorded = st.get("version") if isinstance(st.get("version"), str) else None
    found_versions = library_versions(lib)
    if recorded and recorded in found_versions:
        version = recorded
    elif len(found_versions) == 1:
        version = found_versions[0]
    else:
        version = recorded
    owner = st.get("owner") if isinstance(st.get("owner"), str) else None
    return {"version": version, "path": str(lib), "local": local, "owner": owner}


# ---------- downloading and installing ----------

class _OnlyGitHub(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to GitHub's own hosts, over https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlparse(newurl)
        if parts.scheme != "https" or parts.hostname not in HOSTS:
            raise UpdateError(f"refusing to follow a redirect to {parts.hostname or newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_OnlyGitHub()).open


def _get(url, limit, opener=None, accept=None):
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        raise UpdateError(f"refusing to download from {parts.hostname or url!r}")
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with (opener or _opener)(req, timeout=TIMEOUT) as res:
            data = res.read(limit + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"can't reach GitHub ({getattr(e, 'reason', e)})") from None
    if len(data) > limit:
        raise UpdateError("download too large")
    return data


def fetch_latest(opener=None):
    raw = _get(RELEASES, 4 << 20, opener, accept="application/vnd.github+json")
    try:
        releases = json.loads(raw)
    except ValueError:
        raise UpdateError("GitHub's answer isn't valid JSON") from None
    return latest_stable(releases)


def download(version, opener=None):
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(version)):
        raise UpdateError(f"not a stable release: {version!r}")
    return _get(f"{DOWNLOAD_PREFIX}render-v{version}/{asset_name(version)}", MAX_DOWNLOAD, opener)


def _members(tar):
    """Every entry, checked; (relative path, member) of the regular files to unpack."""
    files = []
    for m in tar.getmembers():
        path = PurePosixPath(m.name)
        parts = [p for p in path.parts if p not in (".", "")]
        if path.is_absolute() or ".." in parts:
            raise UpdateError(f"unsafe path in the archive: {m.name!r}")
        if not parts:
            continue
        loose_file = len(parts) == 1 and not m.isdir()
        if parts[0] not in (TOP_FILES if loose_file else TOP_DIRS):
            raise UpdateError(f"unexpected file in the archive: {m.name!r}")
        if m.isdir():
            continue
        if not m.isfile():
            raise UpdateError(f"the archive contains a link or special file: {m.name!r}")
        files.append(("/".join(parts), m))
    names = {rel for rel, _ in files}
    for needed in (INSTALLER, VERSION_FILE, MANIFEST):
        if needed not in names:
            raise UpdateError(f"this archive has no {needed!r}: not a MAKO Renderer archive")
    return files


def _tail(text, lines=4):
    return " / ".join([ln.strip() for ln in (text or "").splitlines() if ln.strip()][-lines:])


def install(data, expected, prefix=PREFIX):
    """Install a Renderer archive with its own installer into `prefix`. Nothing runs
    unless the whole archive is valid and says it's `expected`; afterwards the installed
    Renderer must report `expected`. Returns the installer's last words."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:xz")
    except (tarfile.TarError, EOFError, OSError) as e:
        raise UpdateError(f"not a valid .tar.xz: {e}") from None
    prefix = Path(prefix)
    work_parent = prefix / "share"
    work_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".fishy-mako-update-", dir=work_parent))
    try:
        with tar:
            for rel, m in _members(tar):
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)
        version = (work / VERSION_FILE).read_text(errors="replace").strip()
        if version != expected:
            raise UpdateError(f"the archive is MAKO Renderer {version!r}, not {expected!r}")
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env.update(MAKO_INSTALLER_ASSUME_YES="1", MAKO_INSTALLER_NO_LAUNCH="1", MAKO_INSTALL_PREFIX=str(prefix))
        try:
            run = subprocess.run([str(work / INSTALLER), "--install"], cwd=work, env=env, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, timeout=INSTALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise UpdateError(f"MAKO's installer didn't finish in {INSTALL_TIMEOUT}s") from None
        except OSError as e:
            raise UpdateError(f"can't run MAKO's installer ({e})") from None
        if run.returncode != 0:
            raise UpdateError(f"MAKO's installer failed: {_tail(run.stderr) or _tail(run.stdout) or run.returncode}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    found = installed([prefix / "share/vulkan/implicit_layer.d"], prefix)
    if not found or found["version"] != expected:
        raise UpdateError(f"installed, but MAKO reports {found and found['version']!r} instead of {expected!r}")
    return _tail(run.stderr) or _tail(run.stdout)
