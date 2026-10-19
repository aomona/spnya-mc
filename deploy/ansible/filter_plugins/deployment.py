"""Hash and bundle only the inputs installed into one server instance."""
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tarfile


def inputs(source, binary, generated):
    root = Path(source["path"])
    paths = [root / "enderpin.toml", root / "enderpin.lock"]
    if source["files"]:
        paths.extend([root / "files", *sorted((root / "files").rglob("*"))])
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
        if not name.startswith("files/server/") or ".." in Path(name).parts:
            raise ValueError("generated inputs must stay inside files/server")
        entries["workspace/" + name] = (0o600, content.encode())
    entries["bin/enderpin"] = (0o700, Path(binary).read_bytes())
    return entries


def fingerprint(source, binary, generated, service):
    records = [(name, mode, None if data is None else hashlib.sha256(data).hexdigest())
               for name, (mode, data) in sorted(inputs(source, binary, generated).items())]
    # Apply logic changes must also retry an otherwise identical deployment.
    directory = Path(__file__).resolve().parent.parent
    implementation = [path.read_text() for path in [Path(__file__), directory / "instance.yml",
                                                   directory / "apply.yml", directory / "deploy.yml"]]
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
