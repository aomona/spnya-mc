"""Operate the creative backend by hand on a Linux machine without systemd.

Run from the repository root as root with the Python environment from deploy/ansible/requirements.txt:
    sudo .venv/bin/python deploy/box/creative.py {check,vpn-up,vpn-down,firewall,prepare,start,stop,status}
The generated game files reuse the Ansible templates, so creative matches what deploy.yml renders for survival.
"""
import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "deploy/ansible"
BOX = ROOT / "deploy/box"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


filters = module("enderpin_filters", ANSIBLE / "filter_plugins/enderpin.py")
deployment = module("enderpin_deployment", ANSIBLE / "filter_plugins/deployment.py")

USER = "ep_creative"
INTERFACE = "epvpn"
VPN_DIRECTORY = Path("/etc/openvpn/client/enderpin-tcp")
VPN_CONFIG = Path("/etc/openvpn/client/enderpin-tcp.conf")
VPN_PID = Path("/run/enderpin-tcp-openvpn.pid")
VPN_LOG = Path("/var/log/enderpin-tcp-openvpn.log")
SERVER_PID = Path("/run/enderpin-creative.pid")
FIREWALL = Path("/etc/enderpin/firewall.nft")
# Hosts contacted by `enderpin sync` for the locked server runtime and packages.
DOWNLOAD_HOSTS = ["piston-meta.mojang.com", "piston-data.mojang.com", "launchermeta.mojang.com",
                  "libraries.minecraft.net", "resources.download.minecraft.net", "meta.fabricmc.net",
                  "maven.fabricmc.net", "api.modrinth.com", "cdn.modrinth.com", "github.com",
                  "objects.githubusercontent.com", "release-assets.githubusercontent.com"]
# Placeholder public addresses that exist only inside a private network namespace. Enderpin ignores
# HTTP proxy variables and refuses private download addresses, so each host is mapped to one of these
# and relayed through the CONNECT proxy. TLS remains end to end and locked SHA-512 hashes still apply.
PLACEHOLDER_BASE = 0x0B000000


def fail(message):
    raise SystemExit(f"creative: {message}")


def load(local_path=BOX / "local.yml", read_secret=True):
    if not local_path.is_file():
        fail(f"copy deploy/box/local.example.yml to {local_path} and set the VPN addresses")
    local = yaml.safe_load(local_path.read_text())
    try:
        creative = ipaddress.IPv4Address(local["creative_address"])
        proxy = ipaddress.IPv4Address(local["proxy_address"])
        proxy_host, proxy_port = str(local["http_proxy"]).rsplit(":", 1)
    except (KeyError, ValueError) as error:
        fail(f"invalid local.yml: {error}")
    if creative == proxy or not re.fullmatch(r"[A-Za-z0-9.-]+", str(local["vpn_endpoint"])):
        fail("creative_address must differ from proxy_address and vpn_endpoint must be a hostname or IPv4 address")
    for port in [local["vpn_port"], int(proxy_port) if proxy_port.isdigit() else None]:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            fail("vpn_port and the http_proxy port must be 1..65535")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", proxy_host):
        fail("http_proxy must be host:port")
    root = str(local.get("root", "/srv/enderpin"))
    if not re.fullmatch(r"/[a-zA-Z0-9/_-]+", root) or root in ["/", "/srv", "/var", "/home", "/usr", "/etc"]:
        fail("root must be a dedicated absolute directory")
    secret = None
    if read_secret:
        secret = Path(local["forwarding_secret_file"]).read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", secret):
            fail("the forwarding secret must be the existing 64 hexadecimal characters")
    instance = yaml.safe_load((BOX / "creative.yml").read_text()) | {"address": str(creative)}
    filters.instances([instance])
    source = filters.workspace(ROOT, instance["workspace"])
    if source["runtime"] != "fabric":
        fail("the creative workspace must be a Fabric server")
    group = yaml.safe_load((ANSIBLE / "group_vars/minecraft.yml").read_text())
    return dict(instance=instance, source=source, secret=secret, root=Path(root) / instance["name"],
                accept_eula=group["enderpin_accept_eula"] is True,
                box=dict(creative_address=str(creative), proxy_address=str(proxy),
                         vpn_endpoint=local["vpn_endpoint"], vpn_port=local["vpn_port"],
                         http_proxy=dict(host=proxy_host, port=int(proxy_port))))


def environment():
    templates = Environment(loader=FileSystemLoader([str(ANSIBLE / "templates"), str(BOX)]),
                            undefined=StrictUndefined, keep_trailing_newline=True, trim_blocks=True)
    templates.filters.update(filters.FilterModule().filters())
    templates.filters["to_json"] = lambda value, **options: json.dumps(value, **options)
    return templates


def render(config):
    """Return the same generated files instance.yml renders for a Fabric backend, plus box-only files."""
    templates = environment()
    variables = dict(instance=config["instance"], source=config["source"], box=config["box"],
                     enderpin_forwarding_secret=config["secret"] or "0" * 64,
                     enderpin_instances=[config["instance"]], enderpin_source_root=str(ROOT),
                     enderpin_backend_interface=INTERFACE, enderpin_proxy_address=config["box"]["proxy_address"])
    def lookup(name):
        return templates.get_template(name).render(**variables)
    generated = {"files/server/server.properties": lookup("server.properties.j2"),
                 "files/server/config/FabricProxy-Lite.toml": lookup("FabricProxy-Lite.toml.j2")}
    if "voicechat_port" in config["instance"]:
        generated["files/server/config/voicechat/voicechat-server.properties"] = lookup("voicechat-server.properties.j2")
    return generated, {"firewall": lookup("firewall.nft.j2"), "openvpn": lookup("openvpn-client-tcp.conf.j2")}


def require_root():
    if os.geteuid() != 0:
        fail("run with sudo; OpenVPN, nftables and the isolated game account require root")


def alive(pidfile):
    try:
        pid = int(pidfile.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def vpn_address():
    result = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", INTERFACE], text=True, capture_output=True)
    return re.findall(r"inet ([0-9.]+)/", result.stdout) if result.returncode == 0 else []


def write_private(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as handle:
        handle.write(content)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def as_user(command, home):
    """Drop to the isolated game account with a minimal environment, like the systemd unit's User=."""
    return ["setpriv", "--reuid", USER, "--regid", USER, "--init-groups", "--inh-caps=-all",
            "env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin", f"HOME={home}", "LANG=C.UTF-8", *command]


def vpn_up(config, rendered):
    require_root()
    for name in ["ca.crt", "client.crt", "client.key", "tls-crypt.key"]:
        path = VPN_DIRECTORY / name
        if not path.is_file() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
            fail(f"place {path} owned by root with mode 0600 (see docs/deployment.md)")
    if VPN_DIRECTORY.stat().st_mode & 0o077:
        fail(f"{VPN_DIRECTORY} must be mode 0700")
    write_private(VPN_CONFIG, rendered["openvpn"])
    if alive(VPN_PID):
        print("creative: OpenVPN is already running; run vpn-down first to apply a changed configuration")
    else:
        subprocess.run(["openvpn", "--config", str(VPN_CONFIG), "--daemon", "enderpin-tcp",
                        "--writepid", str(VPN_PID), "--log-append", str(VPN_LOG)], check=True)
    # The first TLS attempt can time out after OpenVPN's 60 second hand-window and succeed on retry.
    for _ in range(180):
        if config["box"]["creative_address"] in vpn_address():
            print(f"creative: {INTERFACE} is up with the assigned address")
            return
        time.sleep(1)
    fail(f"the tunnel did not receive its static address; inspect {VPN_LOG}")


def vpn_down():
    require_root()
    pid = alive(VPN_PID)
    if pid:
        os.kill(pid, signal.SIGTERM)
        print("creative: stopped OpenVPN")


def firewall(rendered):
    require_root()
    if shutil.which("nft") is None:
        fail("install nftables first")
    write_private(FIREWALL, rendered["firewall"], 0o644)
    subprocess.run(["nft", "-c", "-f", str(FIREWALL)], check=True)
    subprocess.run(["nft", "-f", str(FIREWALL)], check=True)
    print("creative: applied the inet enderpin table")


def verified_binary(path):
    properties = dict(line.split("=", 1) for line in (ROOT / "client/enderpin-wrapper.properties").read_text().splitlines()
                      if "=" in line and not line.startswith("#"))
    if hashlib.sha256(path.read_bytes()).hexdigest() != properties["binary.linux-x64.sha256"]:
        fail(f"{path} does not match the pinned Enderpin {properties['version']} linux-x64 SHA-256")
    return path


def relay_sync(config, command):
    """Run a command in a private network namespace whose download hosts are relayed through the proxy."""
    name = f"enderpin-fetch-{os.getpid()}"
    proxy = config["box"]["http_proxy"]
    addresses = {host: str(ipaddress.IPv4Address(PLACEHOLDER_BASE + index)) for index, host in enumerate(DOWNLOAD_HOSTS, 1)}
    accepted, listeners = queue.Queue(), []

    def pipe(source, destination):
        try:
            while data := source.recv(65536):
                destination.sendall(data)
        except OSError:
            pass
        finally:
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def relay(client, host):
        try:
            upstream = socket.create_connection((proxy["host"], proxy["port"]), timeout=30)
            upstream.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
            response = b""
            while b"\r\n\r\n" not in response and len(response) < 8192:
                chunk = upstream.recv(1)
                if not chunk:
                    break
                response += chunk
            if not re.match(rb"HTTP/1\.[01] 200", response):
                raise OSError("proxy refused CONNECT")
            upstream.settimeout(None)
            threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
            pipe(upstream, client)
        except OSError:
            client.close()

    def dispatch():  # Started before entering the namespace, so relays reach the proxy on the host network.
        while (item := accepted.get()) is not None:
            threading.Thread(target=relay, args=item, daemon=True).start()

    def listen(address, host, ready):
        descriptor = os.open(f"/run/netns/{name}", os.O_RDONLY)
        os.setns(descriptor, os.CLONE_NEWNET)  # Affects only this listener thread.
        os.close(descriptor)
        server = socket.socket()
        server.bind((address, 443))
        server.listen(64)
        listeners.append(server)
        ready.set()
        while True:
            try:
                accepted.put((server.accept()[0], host))
            except OSError:
                return

    subprocess.run(["ip", "netns", "add", name], check=True)
    try:
        subprocess.run(["ip", "-n", name, "link", "set", "lo", "up"], check=True)
        for address in addresses.values():
            subprocess.run(["ip", "-n", name, "addr", "add", address + "/32", "dev", "lo"], check=True)
        subprocess.run(["ip", "-n", name, "route", "add", "default", "dev", "lo"], check=True)
        threading.Thread(target=dispatch, daemon=True).start()
        for host, address in addresses.items():
            ready = threading.Event()
            threading.Thread(target=listen, args=(address, host, ready), daemon=True).start()
            ready.wait(5)
        with tempfile.NamedTemporaryFile("w", prefix="enderpin-hosts-", delete=False) as hosts:
            hosts.write("127.0.0.1 localhost\n" + "".join(f"{a} {h}\n" for h, a in addresses.items()))
        os.chmod(hosts.name, 0o644)
        try:
            subprocess.run(["nsenter", f"--net=/run/netns/{name}", "unshare", "-m", "sh", "-c",
                            'mount --bind "$0" /etc/hosts && exec "$@"', hosts.name, *command], check=True)
        finally:
            os.unlink(hosts.name)
    finally:
        for server in listeners:
            server.close()
        accepted.put(None)
        subprocess.run(["ip", "netns", "del", name], check=False)


def prepare(config, generated, binary, direct):
    require_root()
    if not config["accept_eula"]:
        fail("review the Minecraft EULA and enderpin_accept_eula in group_vars first")
    if alive(SERVER_PID):
        fail("stop the creative server before preparing a new deployment")
    binary = verified_binary(binary)
    root = config["root"]
    try:
        subprocess.run(["id", "-u", USER], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        subprocess.run(["useradd", "--system", "--home-dir", str(root), "--no-create-home",
                        "--shell", "/usr/sbin/nologin", "--user-group", USER], check=True)
    root.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    for directory in ["", "bin", "cache", "workspace", "backups"]:
        path = root / directory
        path.mkdir(exist_ok=True)
        shutil.chown(path, USER, USER)
        path.chmod(0o700)
    enderpin = [str(root / "bin/enderpin")]
    staged = Path(tempfile.mkdtemp(prefix="stage-", dir=root))
    try:
        shutil.chown(staged, USER, USER)
        archive = staged / "inputs.tar.gz"
        deployment.bundle(config["source"], binary, generated, archive)
        shutil.chown(archive, USER, USER)
        archive.chmod(0o600)
        subprocess.run(as_user(["tar", "-xzf", str(archive), "-C", str(staged)], root), check=True)
        sync = as_user([str(staged / "bin/enderpin"), "-C", str(staged / "workspace"), "--cache-dir", str(root / "cache"),
                        "sync", "--target", "server", "--locked"], root)
        subprocess.run(sync, check=True) if direct else relay_sync(config, sync)
        if (root / "workspace/run").exists():
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            backup = root / "backups" / f"creative-{stamp}.tar.gz"
            subprocess.run(["tar", "-czf", str(backup), "-C", str(root / "workspace"), "."], check=True)
            backup.chmod(0o600)
            print(f"creative: local unencrypted backup {backup}")
        for name in ["files", ".enderpinignore"]:
            path = root / "workspace" / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.exists() or path.is_symlink():
                path.unlink()
        subprocess.run(as_user(["tar", "-xzf", str(archive), "-C", str(root)], root), check=True)
        common = ["-C", str(root / "workspace"), "--cache-dir", str(root / "cache")]
        subprocess.run(as_user(enderpin + common + ["restore", "--target", "server", "--offline", "--no-ignore"], root), check=True)
        subprocess.run(as_user(enderpin + common + ["sync", "--target", "server", "--locked", "--offline"], root), check=True)
        plan = json.loads(subprocess.run(as_user(enderpin + common + ["permissions", "show", "--target", "server", "--json"], root),
                                         check=True, text=True, capture_output=True).stdout)
        if plan["plan"]["folders"]:
            fail("refusing host-local external folder grants")
        subprocess.run(as_user(enderpin + common + ["permissions", "--target", "server", "--no-interactive",
                                                    "approve", plan["fingerprint"]], root), check=True, capture_output=True)
        write_private(root / "approved-plan.json", json.dumps(without_shared_files(plan["plan"]), sort_keys=True))
        print("creative: workspace prepared and its permission plan approved")
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def without_shared_files(plan):
    return {key: value for key, value in plan.items() if key != "shared_files"}


def reapprove_rewritten_settings(root):
    """Minecraft and Distant Horizons rewrite shared settings at runtime, which clears approval.

    Approve again only when everything except those file contents matches the plan approved by prepare,
    the same check docs/deployment.md asks for before restarting survival by hand.
    """
    common = [str(root / "bin/enderpin"), "-C", str(root / "workspace"), "--cache-dir", str(root / "cache"), "permissions"]
    current = json.loads(subprocess.run(as_user(common + ["show", "--target", "server", "--json"], root),
                                        check=True, text=True, capture_output=True).stdout)
    if current["approved"]:
        return
    recorded = json.loads((root / "approved-plan.json").read_text())
    if current["plan"]["folders"] or without_shared_files(current["plan"]) != recorded:
        fail("permissions changed beyond rewritten settings; review them and run prepare again")
    subprocess.run(as_user(common + ["--target", "server", "--no-interactive", "approve", current["fingerprint"]], root),
                   check=True, capture_output=True)
    print("creative: re-approved settings rewritten by the previous run")


def start(config):
    require_root()
    if alive(SERVER_PID):
        fail("the creative server is already running")
    if config["box"]["creative_address"] not in vpn_address():
        fail("start the VPN first (vpn-up)")
    if subprocess.run(["nft", "list", "table", "inet", "enderpin"], capture_output=True).returncode != 0:
        fail("apply the firewall first (firewall)")
    root, instance = config["root"], config["instance"]
    reapprove_rewritten_settings(root)
    command = as_user([str(root / "bin/enderpin"), "-C", str(root / "workspace"), "--cache-dir", str(root / "cache"),
                       "run", "--target", "server", "--no-interactive", "--service", "--offline",
                       "--memory", str(instance["memory_mib"]), "--accept-eula"], root)
    log = os.open(root / "console.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    process = subprocess.Popen(command, cwd=root / "workspace", stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True, umask=0o077)
    os.close(log)
    write_private(SERVER_PID, f"{process.pid}\n")
    for _ in range(180):
        if process.poll() is not None:
            fail(f"the server exited with {process.returncode}; inspect {root / 'console.log'}")
        try:
            socket.create_connection((instance["address"], instance["port"]), timeout=1).close()
            print("creative: the server is listening on the VPN address")
            return
        except OSError:
            time.sleep(1)
    fail(f"the server did not listen within 180 seconds; inspect {root / 'console.log'}")


def stop():
    require_root()
    pid = alive(SERVER_PID)
    if not pid:
        print("creative: not running")
        return
    os.kill(pid, signal.SIGTERM)  # --service stops the game gracefully, like systemd's stop.
    for _ in range(180):
        if not alive(SERVER_PID):
            break
        time.sleep(1)
    else:
        os.killpg(pid, signal.SIGKILL)
        print("creative: graceful stop timed out after 180 seconds; killed")
    SERVER_PID.unlink(missing_ok=True)
    print("creative: stopped")


def status(config):
    print(json.dumps({"vpn": bool(alive(VPN_PID)), "vpn_address": config["box"]["creative_address"] in vpn_address(),
                      "server": bool(alive(SERVER_PID))}))


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "vpn-up", "vpn-down", "firewall", "prepare", "start", "stop", "status"])
    parser.add_argument("--local", type=Path, default=BOX / "local.yml")
    parser.add_argument("--binary", type=Path, default=ROOT / ".local/enderpin")
    parser.add_argument("--direct", action="store_true", help="download without the CONNECT-proxy relay")
    options = parser.parse_args(arguments)
    if options.command in ["vpn-down", "stop"]:
        return vpn_down() if options.command == "vpn-down" else stop()
    config = load(options.local, read_secret=options.command == "prepare")
    generated, rendered = render(config)
    if options.command == "check":
        print(generated["files/server/server.properties"], end="")
        print(f"creative: rendered {', '.join(sorted(generated))}, the firewall and the OpenVPN client")
    elif options.command == "vpn-up":
        vpn_up(config, rendered)
    elif options.command == "firewall":
        firewall(rendered)
    elif options.command == "prepare":
        prepare(config, generated, options.binary, options.direct)
    elif options.command == "start":
        start(config)
    else:
        status(config)


if __name__ == "__main__":
    main(sys.argv[1:])
