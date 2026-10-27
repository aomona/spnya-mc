"""Build/test the pinned Floodgate patch; --check verifies the committed server JAR."""
import argparse
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "cdd5955f69afe47a67d205031d0f7d92efef7e64"
SOURCE_SHA256 = "03cf335600db3a73bdd06d48d5e9bf1cf259bd1ce013921ef092a11113dd8931"
FLOODGATE_URL = "https://download.geysermc.org/v2/projects/floodgate/versions/2.2.5/builds/141/downloads/velocity"
FLOODGATE_SHA256 = "649835022de37ff09255fc626d4bfe7bc2a3b926d07b0dfaf1f51e12bf37001d"
JAR = "discord-integration-velocity-1.0.3-spnya.1.jar"
DESTINATION = ROOT / "proxy/files/plugins" / JAR


def download(url, expected):
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Download SHA-256 mismatch: " + url)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="spnya-discord-") as temporary:
        directory = Path(temporary)
        archive = download("https://codeload.github.com/zunoser/discord-integration-velocity/tar.gz/" + SOURCE,
                           SOURCE_SHA256)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(directory, filter="data")
        project = directory / ("discord-integration-velocity-" + SOURCE)
        subprocess.run(["git", "apply", str(ROOT / "patches/discord-integration-floodgate.patch")],
                       cwd=project, check=True)
        library = project / "velocity/libs/floodgate-velocity.jar"
        library.parent.mkdir()
        library.write_bytes(download(FLOODGATE_URL, FLOODGATE_SHA256))
        subprocess.run(["sh", "gradlew", ":velocity:check", ":velocity:jar", "--no-daemon"],
                       cwd=project, check=True)
        built = project / "velocity/build/libs" / JAR
        if args.check:
            if built.read_bytes() != DESTINATION.read_bytes():
                raise ValueError("Committed Discord plugin differs; rebuild with this script.")
        else:
            DESTINATION.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(built, DESTINATION)
        print("Discord plugin verified: " + hashlib.sha256(built.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
