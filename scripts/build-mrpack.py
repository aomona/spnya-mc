"""Export the locked client with upstream downloads and tracked shared settings."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
from urllib.parse import urlsplit
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}


def package_file(package):
    filename = package["filename"]
    if not filename or filename in (".", "..") or any(c in filename for c in "/\\:"):
        raise ValueError("Unsafe package filename: " + filename)
    url = package["url"]
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Package must have a public HTTPS download URL: " + filename)
    sha1, sha512 = hashlib.sha1(), hashlib.sha512()
    size = 0
    request = urllib.request.Request(url, headers={"User-Agent": "spnya-mc-mrpack/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            sha1.update(chunk)
            sha512.update(chunk)
    if size != package["size"] or sha512.hexdigest() != package["sha512"]:
        raise ValueError("Upstream size or SHA-512 mismatch: " + filename)
    return {
        "path": FOLDERS[package["kind"]] + "/" + filename,
        "hashes": {"sha1": sha1.hexdigest(), "sha512": sha512.hexdigest()},
        "env": {"client": "required", "server": "unsupported"},
        "downloads": [url],
        "fileSize": size,
    }


def build(version, output):
    manifest = tomllib.loads((ROOT / "enderpin.toml").read_text())
    lock = tomllib.loads((ROOT / "enderpin.lock").read_text())
    target, runtime = lock["targets"]["client"], lock["runtimes"]["client"]
    if (manifest["format"] != 4 or lock["format"] != 4
            or manifest["minecraft"] != target["minecraft"]
            or manifest["minecraft"] != runtime["minecraft"]
            or manifest["targets"]["client"]["loader"] != target["loader"]
            or runtime["loader"] != "fabric"
            or manifest["targets"]["client"]["loader_version"] != runtime["loader_version"]):
        raise ValueError("Client manifest/runtime mismatch or unsupported format/loader")
    with ThreadPoolExecutor(max_workers=8) as pool:
        files = list(pool.map(package_file, target["packages"]))
    if len({file["path"] for file in files}) != len(files):
        raise ValueError("Duplicate package destinations")
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": version,
        "name": "Spnya Minecraft",
        "files": files,
        "dependencies": {"minecraft": runtime["minecraft"], "fabric-loader": runtime["loader_version"]},
    }
    shared = ROOT / "client/files"
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "client/files/"], cwd=ROOT,
    ).decode().split("\0")
    overrides = []
    for name in filter(None, paths):
        path = ROOT / name
        relative = path.relative_to(shared)
        if path.is_symlink() or not path.is_file() or not (relative.parts[0] == "config" or relative.as_posix() == "options.txt"):
            raise ValueError("Only regular shared config/options files may be bundled: " + name)
        overrides.append(("client-overrides/" + relative.as_posix(), path.read_bytes()))
    output.parent.mkdir(parents=True, exist_ok=True)
    # Fixed timestamps make a retry of the same commit produce the same archive.
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in [("modrinth.index.json", json.dumps(index, ensure_ascii=False, indent=2).encode()), *overrides]:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    print(f"Built {output}: {len(files)} upstream downloads, {len(overrides)} shared settings")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.version, args.output)
