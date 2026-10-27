"""Exercise world/secret preservation and fail-closed format 3 cutover without a live server."""
import fcntl
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import tomllib
import unittest

spec = importlib.util.spec_from_file_location("migration", Path(__file__).parent / "ansible/files/migrate.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "enderpin.toml").write_text('format=3\n[server]\nloader="fabric"\n')
        self.run = self.root / "run/server"
        self.run.mkdir(parents=True)
        for name in ["world/level.dat", "world/playerdata/test.dat", "plugins/floodgate/key.pem",
                     "plugins/discordintegrationvelocity/discord-whitelist.json", "eula.txt", "config/custom.json"]:
            path = self.run / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("keep " + name).encode())
        state = self.root / ".enderpin/server"
        state.mkdir(parents=True)
        self.state = state / "state.toml"
        self.state.write_text('format = 3\n[files."run/server/mods/example.jar"]\nsha512 = "' + "a" * 128
                              + '"\nsize = 123\n[shared."run/server/server.properties"]\nsha512 = "' + "b" * 128 + '"\nsize = 42\n')
        (state / "permissions.toml").write_text("old approval must not be reused")

    def test_preserves_all_runtime_bytes_and_retries_without_reusing_approval(self):
        before = {p.relative_to(self.run): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.run.rglob("*") if p.is_file()}
        self.assertTrue(migration.migrate(self.root, "survival", check=True))
        self.assertTrue(self.run.exists())
        self.assertFalse((self.root / "survival").exists())
        self.assertTrue(migration.migrate(self.root, "survival"))
        new = self.root / "survival/run"
        after = {p.relative_to(new): hashlib.sha256(p.read_bytes()).hexdigest() for p in new.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        converted = tomllib.loads((self.root / ".enderpin/survival/state.toml").read_text())
        self.assertEqual(converted["format"], 4)
        self.assertEqual(converted["files"]["survival/run/mods/example.jar"]["size"], 123)
        self.assertEqual(converted["shared"]["survival/run/server.properties"]["sha512"], "b" * 128)
        self.assertFalse((self.root / ".enderpin/survival/permissions.toml").exists())
        self.assertFalse(migration.migrate(self.root, "survival"))
        (self.root / "enderpin.toml").write_text('format=4\n')
        self.assertFalse(migration.migrate(self.root, "survival"))

    def test_refuses_existing_runtime_or_bad_state_without_touching_world(self):
        target = self.root / "survival/run"
        target.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "already exists"):
            migration.migrate(self.root, "survival")
        target.rmdir()
        original = self.state.read_text()
        for bad in [original.replace('run/server/mods', 'run/client/mods'),
                    original.replace('run/server/mods', 'run/server/../mods'),
                    original.replace('size = 123', 'size = -1')]:
            self.state.write_text(bad)
            with self.assertRaises(ValueError):
                migration.migrate(self.root, "survival")
            self.assertTrue((self.run / "world/level.dat").is_file())
            self.assertFalse(target.exists())

    def test_refuses_symlinks_running_game_and_wrong_target_type(self):
        with self.assertRaises(ValueError):
            migration.migrate(self.root, "proxy")
        with (self.state.parent / "run.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                migration.migrate(self.root, "survival")
        (self.root / "survival").symlink_to(self.root / "run", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic"):
            migration.migrate(self.root, "survival")
        self.assertTrue((self.run / "world/level.dat").is_file())

    def test_refuses_pending_old_transaction(self):
        (self.root / ".enderpin/transaction").mkdir()
        with self.assertRaisesRegex(ValueError, "pending format 3 transaction"):
            migration.migrate(self.root, "survival")
        self.assertTrue((self.run / "world/level.dat").is_file())

    def test_resumes_state_write_before_runtime_rename(self):
        from unittest.mock import patch
        with patch.object(Path, "rename", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                migration.migrate(self.root, "survival")
        self.assertTrue(self.run.exists())
        self.assertTrue(migration.migrate(self.root, "survival"))


if __name__ == "__main__":
    unittest.main()
