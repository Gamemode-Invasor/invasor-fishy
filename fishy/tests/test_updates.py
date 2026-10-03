import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import updates

LAYER_JSON = {"layer": {"name": "VK_LAYER_MAKO_render", "library_path": "../../../lib/libmako-render.so"}}


def release(tag, version=None, draft=False, prerelease=False, asset=True):
    version = version or tag.removeprefix("render-v")
    name = updates.asset_name(version)
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "assets": [{"name": name, "browser_download_url": f"{updates.DOWNLOAD_PREFIX}{tag}/{name}"}] if asset else []}


def fake_library(*versions):
    """Bytes shaped like the layer library: versions as NUL-terminated strings."""
    body = b"\x7fELF\x00junk\x00VK_LAYER_MAKO_render\x00"
    return body + b"".join(v.encode() + b"\x00" for v in versions) + b"more\x00"


# A stand-in for "Install MAKO Renderer": puts the layer and its state where MAKO's
# installer would, from the archive it sits in, and says it did.
INSTALLER = """#!/bin/sh
set -e
[ "$1" = "--install" ] && [ "$MAKO_INSTALLER_ASSUME_YES" = 1 ] && [ "$MAKO_INSTALLER_NO_LAUNCH" = 1 ] || exit 9
[ -z "$DISPLAY" ] || exit 8
here="$(dirname "$0")"
p="$MAKO_INSTALL_PREFIX"
mkdir -p "$p/lib" "$p/share/vulkan/implicit_layer.d" "$p/share/mako-render"
cp "$here/lib/libmako-render.so" "$p/lib/"
cp "$here/share/vulkan/implicit_layer.d/VkLayer_MAKO_render.json" "$p/share/vulkan/implicit_layer.d/"
printf '{"owner": "standalone", "version": "%s"}' "$(cat "$here/MAKO-Renderer-version.txt")" > "$p/share/mako-render/active-renderer.json"
echo "MAKO Renderer is installed." >&2
"""


def tarball(version="4.1.0", lib_version=None, installer=INSTALLER, extra=None, links=(), skip=()):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:xz") as tar:
        def add(name, data, mode=0o644):
            if name.removeprefix("./") in skip:
                return
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
        add("./Install MAKO Renderer", installer.encode(), 0o755)
        add("./MAKO-Renderer-version.txt", f"{version}\n".encode())
        add("./MAKO-Renderer-install-manifest.txt", b"x  lib/libmako-render.so\n")
        add("./README.txt", b"readme")
        add("./bin/mako-cli", b"#!cli", 0o755)
        add("./lib/libmako-render.so", fake_library(lib_version or version))
        add("./lib32/libmako-render.so", fake_library(lib_version or version))
        add(f"./share/vulkan/implicit_layer.d/{updates.LAYER_JSON}", json.dumps(LAYER_JSON).encode())
        for name, data in (extra or {}).items():
            add(name, data)
        for name in links:
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            tar.addfile(info)
    return out.getvalue()


class Versions(unittest.TestCase):
    def test_order_and_compare(self):
        names = ["3.3.0", "4.0.0-rc1", "4.0.0", "4.0.1", "4.1.0"]
        self.assertEqual(sorted(names, key=updates.parse_version), names)
        self.assertEqual(updates.compare("4.0.0", "4.1.0"), "newer_available")
        self.assertEqual(updates.compare("4.1.0", "4.1.0"), "up_to_date")
        self.assertEqual(updates.compare("4.2.0", "4.1.0"), "ahead")
        self.assertEqual(updates.compare(None, "4.1.0"), "newer_available")

    def test_compatibility(self):
        cases = {"4.0.0": "tested", "4.0.3": "tested", "4.1.0": "newer_minor", "3.3.0": "unsupported",
                 "5.0.0": "unsupported", None: None}
        for version, expected in cases.items():
            with self.subTest(version=version):
                self.assertEqual(updates.compatibility(version), expected)

    def test_latest_stable_renderer_release(self):
        releases = [
            release("render-v4.2.0", prerelease=True),
            release("render-v4.1.5", draft=True),
            {"tag_name": "plugin-v4.1.0", "draft": False, "prerelease": False, "assets": []},
            release("render-v4.1.0"),
            release("render-v4.0.0"),
            release("render-v4.1.1", asset=False),
        ]
        self.assertEqual(updates.latest_stable(releases),
                         {"version": "4.1.0", "url": f"{updates.DOWNLOAD_PREFIX}render-v4.1.0/MAKO-Renderer-v4.1.0-linux.tar.xz"})
        odd = release("render-v4.3.0")
        odd["assets"][0]["browser_download_url"] = "https://evil.example/x.tar.xz"
        self.assertEqual(updates.latest_stable([odd, release("render-v4.0.0")])["version"], "4.0.0")
        for bad in ([], {}, [release("plugin-v4.0.0")], None):
            with self.subTest(releases=bad), self.assertRaises(updates.UpdateError):
                updates.latest_stable(bad)


class Installed(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.local = self.root / "home/.local"
        self.system = self.root / "usr"

    def put(self, prefix, *versions, state=None):
        (prefix / "lib").mkdir(parents=True, exist_ok=True)
        (prefix / "lib/libmako-render.so").write_bytes(fake_library(*versions))
        (prefix / "share/vulkan/implicit_layer.d").mkdir(parents=True, exist_ok=True)
        (prefix / "share/vulkan/implicit_layer.d" / updates.LAYER_JSON).write_text(json.dumps(LAYER_JSON))
        if state is not None:
            (prefix / "share/mako-render").mkdir(parents=True, exist_ok=True)
            (prefix / updates.STATE).write_text(json.dumps(state))

    def dirs(self):
        return [self.local / "share/vulkan/implicit_layer.d", self.system / "share/vulkan/implicit_layer.d"]

    def test_version_and_owner(self):
        self.assertIsNone(updates.installed(self.dirs(), self.local))
        self.put(self.local, "4.0.0", state={"owner": "standalone", "version": "4.0.0"})
        self.assertEqual(updates.installed(self.dirs(), self.local),
                         {"version": "4.0.0", "path": str(self.local / "lib/libmako-render.so"), "local": True, "owner": "standalone"})
        # several version-looking strings: the one active-renderer.json names wins
        self.put(self.local, "1.4.328", "4.0.1", state={"owner": "decky", "version": "4.0.1"})
        self.assertEqual(updates.installed(self.dirs(), self.local)["version"], "4.0.1")
        self.assertEqual(updates.installed(self.dirs(), self.local)["owner"], "decky")
        # nothing in the library: the recorded version
        self.put(self.local, state={"owner": "standalone", "version": "4.0.0"})
        self.assertEqual(updates.installed(self.dirs(), self.local)["version"], "4.0.0")
        # no state file, one string: that one
        (self.local / updates.STATE).unlink()
        self.put(self.local, "4.0.2")
        self.assertEqual(updates.installed(self.dirs(), self.local)["version"], "4.0.2")
        self.assertIsNone(updates.installed(self.dirs(), self.local)["owner"])

    def test_system_install_is_flagged(self):
        self.put(self.system, "4.0.0")
        self.assertFalse(updates.installed(self.dirs(), self.local)["local"])


class Install(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.prefix = Path(d.name) / ".local"

    def leftovers(self):
        share = self.prefix / "share"
        return [p.name for p in share.iterdir() if p.name.startswith(".")] if share.exists() else []

    def test_runs_mako_installer(self):
        said = updates.install(tarball("4.1.0"), "4.1.0", self.prefix)
        self.assertIn("installed", said)
        found = updates.installed([self.prefix / "share/vulkan/implicit_layer.d"], self.prefix)
        self.assertEqual((found["version"], found["owner"]), ("4.1.0", "standalone"))
        self.assertEqual(self.leftovers(), [])  # temporary folder gone

    def test_bad_archives_run_nothing(self):
        cases = {
            "not xz": b"nope",
            "traversal": tarball(extra={"./bin/../../evil": b"x"}),
            "absolute": tarball(extra={"/etc/evil": b"x"}),
            "unknown top file": tarball(extra={"./run-me.sh": b"x"}),
            "unknown top folder": tarball(extra={"./etc/thing": b"x"}),
            "symlink": tarball(links=["./lib/link.so"]),
            "no installer": tarball(skip={"Install MAKO Renderer"}),
            "no version file": tarball(skip={"MAKO-Renderer-version.txt"}),
            "another version": tarball("4.0.9"),
        }
        for label, data in cases.items():
            with self.subTest(case=label), self.assertRaises(updates.UpdateError):
                updates.install(data, "4.1.0", self.prefix)
        self.assertFalse((self.prefix / "lib").exists())
        self.assertEqual(self.leftovers(), [])

    def test_installer_failure_and_wrong_result(self):
        failing = "#!/bin/sh\necho 'The package file lib/x does not match its recorded checksum.' >&2\nexit 1\n"
        with self.assertRaisesRegex(updates.UpdateError, "installer failed: The package file"):
            updates.install(tarball(installer=failing), "4.1.0", self.prefix)
        with self.assertRaisesRegex(updates.UpdateError, "reports"):
            updates.install(tarball("4.1.0", lib_version="4.0.0"), "4.1.0", self.prefix)
        self.assertEqual(self.leftovers(), [])

    def test_downloads_only_from_github(self):
        for version in ("4.1.0-rc1", "../x", "latest"):
            with self.subTest(version=version), self.assertRaises(updates.UpdateError):
                updates.download(version, opener=lambda *a, **k: None)
        with self.assertRaises(updates.UpdateError):
            updates._get("https://evil.example/x", 10, opener=lambda *a, **k: None)
        with self.assertRaises(updates.UpdateError):
            updates._get("http://github.com/x", 10, opener=lambda *a, **k: None)
        handler = updates._OnlyGitHub()
        with self.assertRaises(updates.UpdateError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/x")


if __name__ == "__main__":
    unittest.main()
