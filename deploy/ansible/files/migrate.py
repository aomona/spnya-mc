"""Move a stopped, backed-up deployed format 3 workspace to its named target (Python 3.11+)."""
import argparse
from contextlib import ExitStack
import fcntl
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import tomllib


def migrate(root, target, check=False):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("migration paths cannot contain symbolic links")
    root = root.resolve()
    if target not in {"survival", "creative", "proxy"}:
        raise ValueError("unsupported deployment target")
    old = root / "run/server"
    new = root / target / "run"
    state = root / ".enderpin/server/state.toml"
    destination = root / ".enderpin" / target / "state.toml"
    paths = [root, root / "enderpin.toml", old, new, state, destination,
             root / ".enderpin/operation.lock", state.parent / "run.lock", destination.parent / "run.lock"]
    for path in paths:
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError("migration paths cannot contain symbolic links")
    if not (root / "enderpin.toml").exists():
        if old.exists():
            raise ValueError("legacy runtime has no manifest")
        return False
    manifest = tomllib.loads((root / "enderpin.toml").read_text())
    if manifest.get("format") == 4:
        if old.exists():
            raise ValueError("both format 4 inputs and an unmigrated legacy runtime exist")
        return False
    if manifest.get("format") != 3:
        raise ValueError("only format 3 deployments can be migrated")
    if (manifest.get("server", {}).get("loader") == "velocity") != (target == "proxy"):
        raise ValueError("legacy runtime does not match the deployment target type")
    if (root / ".enderpin/transaction").exists():
        raise ValueError("recover the pending format 3 transaction with the old binary first")
    original_state = state.read_text()
    previous = tomllib.loads(original_state)
    if previous.get("format") != 3 or set(previous) - {"format", "files", "shared"}:
        raise ValueError("unsupported legacy installed state")
    lines = ["format = 4\n"]
    for section in ["files", "shared"]:
        for name, record in sorted(previous.get(section, {}).items()):
            relative = PurePosixPath(name)
            if not name.startswith("run/server/") or ".." in relative.parts or "\\" in name or relative.as_posix() != name:
                raise ValueError("installed state contains an unsafe or different target path")
            if (set(record) != {"sha512", "size"} or not re.fullmatch(r"[0-9a-f]{128}", record["sha512"])
                    or type(record["size"]) is not int or record["size"] < 0):
                raise ValueError("invalid installed file record")
            mapped = target + "/run/" + name.removeprefix("run/server/")
            lines += [f'[{section}.{json.dumps(mapped)}]', f'sha512 = "{record["sha512"]}"', f'size = {record["size"]}\n']
    translated = "\n".join(lines)
    if destination.exists() and destination.read_text() != translated:
        raise ValueError("destination installed state already exists and differs")
    if new.exists():
        if not old.exists() and destination.exists() and new.is_dir():
            return False  # Resume after a completed rename, before the new manifest was installed.
        raise ValueError("destination runtime already exists; refusing to merge worlds")
    if not old.is_dir():
        raise ValueError("legacy runtime is missing")
    if check:
        return True
    with ExitStack() as stack:
        destination.parent.mkdir(parents=True, exist_ok=True)
        for path in [root / ".enderpin/operation.lock", state.parent / "run.lock", destination.parent / "run.lock"]:
            handle = stack.enter_context(path.open("a+"))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if state.read_text() != original_state or (root / ".enderpin/transaction").exists():
            raise ValueError("legacy state changed during migration; retry after stopping all operations")
        # Keep the old state and permissions for backup inspection; never reuse an approval.
        with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, delete=False) as temporary:
            temporary.write(translated)
        Path(temporary.name).replace(destination)
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = migrate(args.workspace, args.target, args.check)
    print("migration required" if args.check and changed else "migrated" if changed else "no migration required")
