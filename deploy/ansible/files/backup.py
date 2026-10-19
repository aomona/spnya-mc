"""Encrypt a stopped workspace and upload only ciphertext to Hack Club CDN v4."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
import uuid


def upload(archive, api_key):
    if not re.fullmatch(r"sk_cdn_[A-Za-z0-9_-]+", api_key):
        raise ValueError("invalid CDN API key")
    boundary = "enderpin-" + uuid.uuid4().hex
    prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
              f"filename=\"{archive.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    size = archive.stat().st_size
    digest = hashlib.sha256()
    connection = http.client.HTTPSConnection("cdn.hackclub.com", timeout=60)
    try:
        connection.putrequest("POST", "/api/v4/upload")
        connection.putheader("Authorization", "Bearer " + api_key)
        connection.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
        connection.putheader("Content-Length", str(len(prefix) + size + len(suffix)))
        connection.endheaders()
        connection.send(prefix)
        with archive.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                connection.send(chunk)
        connection.send(suffix)
        response = connection.getresponse()
        if response.status != 201:
            raise RuntimeError(f"CDN upload failed (HTTP {response.status}); encrypted backup retained locally")
        receipt = json.loads(response.read(65536))
    finally:
        connection.close()
    url = urlsplit(receipt.get("url", ""))
    if (not isinstance(receipt.get("id"), str) or not receipt["id"] or
            type(receipt.get("size")) is not int or receipt["size"] != size or
            receipt.get("filename") != archive.name or
            url.scheme != "https" or url.netloc != "cdn.hackclub.com" or
            not url.path.startswith("/" + receipt["id"] + "/")):
        raise ValueError("CDN returned an invalid upload receipt; encrypted backup retained locally")
    return {"id": receipt["id"], "url": receipt["url"], "size": size,
            "sha256": digest.hexdigest(), "archive": archive.name}


def backup(workspace, archive, recipient, api_key):
    if not re.fullmatch(r"[a-z0-9_-]+-[0-9a-f]{40}-[0-9TZ]+\.tar\.gz\.age", archive.name):
        raise ValueError("invalid backup filename")
    if not workspace.is_dir():
        raise ValueError("workspace does not exist")
    # Stream directly into age so no unencrypted backup is written to disk or uploaded.
    with archive.open("xb") as encrypted:
        tar = subprocess.Popen(["tar", "-czf", "-", "-C", str(workspace), "."],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            result = subprocess.run(["age", "--encrypt", "--recipient", recipient],
                                    stdin=tar.stdout, stdout=encrypted, stderr=subprocess.PIPE)
        finally:
            tar.stdout.close()
            tar_status = tar.wait()
    if result.returncode or tar_status:
        archive.unlink()
        raise RuntimeError("backup encryption failed; nothing was uploaded")
    receipt = upload(archive, api_key)
    receipt_path = archive.with_name(archive.name + ".json")
    with receipt_path.open("x") as output:
        json.dump(receipt, output, indent=2)
        output.write("\n")
    return receipt


if __name__ == "__main__":
    os.umask(0o077)
    # Neither tar nor age needs the API credential in its environment.
    key = os.environ.pop("HACKCLUB_CDN_API_KEY")
    try:
        backup(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], key)
    except Exception:
        sys.exit("Encrypted CDN backup failed; inspect the retained local backup before retrying.")
