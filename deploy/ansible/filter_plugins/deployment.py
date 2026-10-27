"""Hash and bundle only the inputs installed into one server instance."""
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tarfile
import tomllib


def inputs(source, binary, generated):
    root = Path(source["path"])
    paths = [root / "enderpin.toml", root / "enderpin.lock"]
    for relative in source["inputs"]:
        directory = root / relative
        paths.extend([directory, *sorted(directory.rglob("*"))])
    entries = {}
    for path in paths:
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError("deployment inputs must be regular files or directories")
        mode = stat.S_IMODE(info.st_mode) & 0o777
        if path.name in ["enderpin.toml", "enderpin.lock"] and path.parent == root:
            mode = 0o600
        entries["workspace/" + path.relative_to(root).as_posix()] = (
            mode, None if path.is_dir() else path.read_bytes())
    for name, content in generated.items():
        if not name.startswith(source["target"] + "/files/") or ".." in Path(name).parts:
            raise ValueError("generated inputs must stay inside the selected target files directory")
        entries["workspace/" + name] = (0o600, content.encode())
    entries["bin/enderpin"] = (0o700, Path(binary).read_bytes())
    # Ansible only applies ownership to explicit archive members, including parents.
    for name in list(entries):
        for parent in Path(name).parents:
            if parent != Path("."):
                entries.setdefault(parent.as_posix(), (0o700, None))
    return entries


def fingerprint(source, binary, generated, service):
    entries = inputs(source, binary, generated)
    # A client-only change must not restart servers sharing the same manifest.
    lock = tomllib.loads((Path(source["path"]) / "enderpin.lock").read_text())
    selected = {"format": lock["format"], "target": lock["targets"][source["target"]],
                "runtime": lock["runtimes"][source["target"]]}
    entries["workspace/enderpin.toml"] = (0o600, json.dumps(source["config"], sort_keys=True).encode())
    entries["workspace/enderpin.lock"] = (0o600, json.dumps(selected, sort_keys=True).encode())
    records = [(name, mode, None if data is None else hashlib.sha256(data).hexdigest())
               for name, (mode, data) in sorted(entries.items())]
    # Apply logic changes must also retry an otherwise identical deployment.
    directory = Path(__file__).resolve().parent.parent
    implementation = [path.read_text() for path in [Path(__file__), directory / "instance.yml",
                                                   directory / "apply.yml", directory / "deploy.yml",
                                                   directory / "files/backup.py", directory / "files/migrate.py"]]
    return hashlib.sha256(json.dumps([records, service, implementation], ensure_ascii=False).encode()).hexdigest()


def bundle(source, binary, generated, destination):
    with tarfile.open(destination, "w:gz") as archive:
        for name, (mode, data) in sorted(inputs(source, binary, generated).items()):
            entry = tarfile.TarInfo(name)
            entry.mode = mode
            if data is None:
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
            else:
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))


class FilterModule:
    def filters(self):
        return {"enderpin_fingerprint": fingerprint}


if __name__ == "__main__":
    bundle(**json.load(sys.stdin))
