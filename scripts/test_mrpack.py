"""Check upstream verification and the contents of the distributable archive."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location("mrpack", Path(__file__).with_name("build-mrpack.py"))
mrpack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mrpack)


class MrpackTests(unittest.TestCase):
    def setUp(self):
        self.data = b"upstream package"
        self.package = dict(filename="example.jar", kind="mod", url="https://cdn.modrinth.com/example.jar",
                            size=len(self.data), sha512=hashlib.sha512(self.data).hexdigest())

    def test_upstream_hashes_and_destinations(self):
        for kind, folder in mrpack.FOLDERS.items():
            with self.subTest(kind=kind), patch.object(mrpack.urllib.request, "urlopen", return_value=io.BytesIO(self.data)):
                entry = mrpack.package_file(self.package | dict(kind=kind))
                self.assertEqual(entry["path"], folder + "/example.jar")
                self.assertEqual(entry["hashes"]["sha1"], hashlib.sha1(self.data).hexdigest())
                self.assertEqual(entry["downloads"], [self.package["url"]])

    def test_bad_downloads_and_paths_fail(self):
        for change in [dict(size=0), dict(sha512="0" * 128), dict(url="file:///tmp/example.jar"),
                       dict(filename="../example.jar"), dict(filename="C:example.jar"), dict(filename="a\\b.jar")]:
            with self.subTest(change=change), patch.object(mrpack.urllib.request, "urlopen", return_value=io.BytesIO(self.data)):
                with self.assertRaises(ValueError):
                    mrpack.package_file(self.package | change)

    def test_archive_contains_only_index_and_tracked_settings(self):
        lock = tomllib.loads((mrpack.ROOT / "client/enderpin.lock").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "client.mrpack"
            # Use the real lock and tracked config tree without network access.
            with patch.object(mrpack, "package_file", side_effect=lambda p: {
                "path": mrpack.FOLDERS[p["kind"]] + "/" + p["filename"], "downloads": [p["url"]],
            }):
                mrpack.build("test-version", output)
                first = output.read_bytes()
                mrpack.build("test-version", output)
                self.assertEqual(first, output.read_bytes())
            with zipfile.ZipFile(output) as archive:
                index = json.loads(archive.read("modrinth.index.json"))
                self.assertEqual(index["versionId"], "test-version")
                self.assertEqual(len(index["files"]), len(lock["targets"]["client"]["packages"]))
                self.assertEqual(index["dependencies"], {"minecraft": lock["runtimes"]["client"]["minecraft"],
                                                       "fabric-loader": lock["runtimes"]["client"]["loader_version"]})
                self.assertIn("client-overrides/options.txt", archive.namelist())
                self.assertEqual(archive.read("client-overrides/options.txt"),
                                 (mrpack.ROOT / "client/files/client/options.txt").read_bytes())
                self.assertTrue(all(name == "modrinth.index.json" or name.startswith("client-overrides/")
                                    for name in archive.namelist()))
                self.assertFalse(any(name.endswith(".jar") for name in archive.namelist()))

    def test_non_config_files_cannot_be_bundled(self):
        manifest = tomllib.loads((mrpack.ROOT / "client/enderpin.toml").read_text())
        lock = tomllib.loads((mrpack.ROOT / "client/enderpin.lock").read_text())
        lock["targets"]["client"]["packages"] = [self.package]
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(mrpack, "package_file", return_value={"path": "mods/example.jar"}), \
                    patch.object(mrpack.subprocess, "check_output", return_value=b"client/files/client/accounts.json\0"), \
                    patch.object(mrpack.tomllib, "loads", side_effect=[manifest, lock]):
                with self.assertRaisesRegex(ValueError, "Only regular shared"):
                    mrpack.build("test", Path(temporary) / "client.mrpack")


if __name__ == "__main__":
    unittest.main()
