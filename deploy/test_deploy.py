"""Run with the Python environment from deploy/ansible/requirements.txt."""
import importlib.util
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import tarfile
import threading
import tomllib
import unittest
from unittest.mock import patch

from ansible.errors import AnsibleFilterError
from jinja2 import Environment, StrictUndefined
import yaml

BASE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("enderpin_filters", BASE / "ansible/filter_plugins/enderpin.py")
filters = importlib.util.module_from_spec(spec)
spec.loader.exec_module(filters)
spec = importlib.util.spec_from_file_location("enderpin_deployment", BASE / "ansible/filter_plugins/deployment.py")
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
spec = importlib.util.spec_from_file_location("enderpin_cd", BASE / "cd.py")
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)
spec = importlib.util.spec_from_file_location("enderpin_backup", BASE / "ansible/files/backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
spec = importlib.util.spec_from_file_location("enderpin_box", BASE / "box/creative.py")
box = importlib.util.module_from_spec(spec)
spec.loader.exec_module(box)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "servers/backend"
        self.source.mkdir(parents=True)
        self.manifest = 'format=3\nminecraft="1.21.1"\n[server]\nloader="paper"\n'
        (self.source / "enderpin.toml").write_text(self.manifest)
        (self.source / "enderpin.lock").write_text('[runtimes.server]\nloader="paper"\n')
        self.instance = dict(name="backend", workspace="servers/backend", address="192.0.2.2", port=25566, memory_mib=1024)
        self.inventory = yaml.safe_load((BASE / "ansible/inventory.example.yml").read_text())["all"]["vars"]

    def test_public_files_do_not_publish_connection_addresses(self):
        root = BASE.parent
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root, text=True).split("\0")
        self.assertNotIn("deploy/inventory.yml", tracked)
        self.assertNotIn("deploy/secrets.yml", tracked)
        documentation = [ipaddress.ip_network(network) for network in ["192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"]]
        constants = {"0.0.0.0", "127.0.0.1", "255.255.255.0", "255.255.255.248", "255.255.255.255"}
        for name in tracked:
            if not name or Path(name).suffix not in [".yml", ".yaml", ".md", ".py"]:
                continue
            content = (root / name).read_text()
            for address in re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", content):
                self.assertTrue(address in constants or any(ipaddress.ip_address(address) in network for network in documentation),
                                f"{name} contains a non-documentation address")

    def test_workspace_boundaries_and_required_fabric_proxy(self):
        self.assertEqual(filters.workspace(self.root, "servers/backend")["runtime"], "paper")
        for path in ["..", str(self.source), "."]:
            with self.assertRaises(AnsibleFilterError):
                filters.workspace(self.root, path)
        (self.root / "shortcut").symlink_to(self.source)
        with self.assertRaises(AnsibleFilterError):
            filters.workspace(self.root, "shortcut")
        inputs = self.source / "files/server"
        inputs.mkdir(parents=True)
        (inputs / "server.properties").write_text("online-mode=false")
        with self.assertRaises(AnsibleFilterError):
            filters.workspace(self.root, "servers/backend")
        (inputs / "server.properties").unlink()
        discord = inputs / "plugins/discordintegrationvelocity/config.json"
        discord.parent.mkdir(parents=True)
        discord.write_text('{}')
        with self.assertRaises(AnsibleFilterError):
            filters.workspace(self.root, "servers/backend")
        discord.unlink()
        for scope, name in [("server", "config/voicechat/voicechat-server.properties"),
                            ("common", "plugins/voicechat/voicechat-proxy.properties"),
                            ("server", "plugins/Geyser-Velocity/config.yml"),
                            ("server", "plugins/floodgate/config.yml")]:
            managed = self.source / "files" / scope / name
            managed.parent.mkdir(parents=True, exist_ok=True)
            managed.write_text("managed by Ansible")
            with self.assertRaises(AnsibleFilterError):
                filters.workspace(self.root, "servers/backend")
            managed.unlink()
        manifest = self.manifest.replace('loader="paper"', 'loader="fabric"') + '[server.packages.proxy]\nmodrinth="fabricproxy-lite"\n'
        (self.source / "enderpin.toml").write_text(manifest)
        with self.assertRaises(AnsibleFilterError):
            filters.workspace(self.root, "servers/backend")
        (self.source / "enderpin.lock").write_text('[runtimes.server]\nloader="fabric"\n[[targets.server.packages]]\nname="fabricproxy-lite"\nkind="mod"\nroots=["proxy"]\n')
        self.assertEqual(filters.workspace(self.root, "servers/backend")["runtime"], "fabric")
        (self.source / "enderpin.toml").write_text(manifest.replace('[server]\n', '[server]\ndisabled=["proxy"]\n'))
        with self.assertRaises(AnsibleFilterError):
            filters.workspace(self.root, "servers/backend")

    def test_instance_and_vpn_validation(self):
        filters.instances([self.instance])
        voice = self.instance | dict(voicechat_port=24454)
        bedrock = self.instance | dict(name="proxy", port=25565, voicechat_port=24455, bedrock_port=19132)
        filters.instances([voice, bedrock])
        filters.instances([self.instance | dict(voicechat_port=25566)])  # UDP may share a TCP port.
        for overrides in [dict(port=True), dict(name="../../etc"), dict(properties={"motd":"hi\nonline-mode=false"}),
                          dict(properties={"motd":"hi\\"}), dict(fabric_proxy_hack_early_send="true"), dict(memory_mib=-1)]:
            with self.assertRaises(AnsibleFilterError):
                filters.instances([self.instance | overrides])
        for field in ["voicechat_port", "bedrock_port"]:
            for value in [True, 0, 65536, "24454"]:
                with self.subTest(field=field, value=value), self.assertRaises(AnsibleFilterError):
                    filters.instances([self.instance | {field: value}])
        for instances in [[voice, bedrock | dict(voicechat_port=24454)],
                          [voice, bedrock | dict(bedrock_port=24454)],
                          [bedrock | dict(bedrock_port=25565)],
                          [self.instance | dict(port=19132), bedrock],
                          [bedrock, self.instance | dict(port=19132)]]:
            with self.subTest(instances=instances), self.assertRaises(AnsibleFilterError):
                filters.instances(instances)
        with self.assertRaises(AnsibleFilterError):
            filters.instances([self.instance, self.instance | dict(name="other")])
        network = filters.vpn(self.inventory["enderpin_vpn"], self.inventory["enderpin_vpn_clients"])
        self.assertEqual(network["netmask"], "255.255.255.0")
        for clients in [{"home":"192.0.2.1"}, {"ca":"192.0.2.2"}, {"home":"192.0.2.2","other":"192.0.2.2"}]:
            with self.assertRaises(AnsibleFilterError):
                filters.vpn(self.inventory["enderpin_vpn"], clients)
        filters.backends(self.inventory["enderpin_backends"])
        with self.assertRaises(AnsibleFilterError):
            filters.backends({"try":"192.0.2.2:25566"})

    def test_optional_tcp_vpn_validation(self):
        udp, udp_clients = self.inventory["enderpin_vpn"], self.inventory["enderpin_vpn_clients"]
        tcp, tcp_clients = self.inventory["enderpin_vpn_tcp"], self.inventory["enderpin_vpn_tcp_clients"]
        self.assertEqual(filters.vpn_tcp(tcp, tcp_clients, udp, udp_clients),
                         {"network": "198.51.100.0", "netmask": "255.255.255.248"})
        for config, clients in [(tcp, {}), (tcp, {"home": "198.51.100.2"}), (tcp, {"box": "198.51.100.1"}),
                                (tcp, {"box": "198.51.100.9"}), (tcp | dict(network="192.0.2.0/29", server_address="192.0.2.1"),
                                                                {"box": "192.0.2.2"}),
                                (tcp | dict(port="443"), tcp_clients), (tcp | dict(server_address="198.51.100.2"), tcp_clients)]:
            with self.subTest(config=config, clients=clients), self.assertRaises(AnsibleFilterError):
                filters.vpn_tcp(config, clients, udp, udp_clients)

    def test_creative_backend_is_added_only_when_configured(self):
        playbook = self.root / "backends.yml"
        output = self.root / "backends.json"
        playbook.write_text(yaml.safe_dump([dict(hosts="localhost", gather_facts=False,
            vars_files=[str(BASE / "ansible/group_vars/minecraft.yml")],
            tasks=[{"ansible.builtin.copy": {"content": "{{ {'backends': enderpin_backends | enderpin_backends, 'try': enderpin_try} | to_json }}",
                                             "dest": str(output)}}])]))
        (self.root / "filter_plugins").symlink_to(BASE / "ansible/filter_plugins")
        command = [str(Path(sys.executable).with_name("ansible-playbook")), "-i", "localhost,", "-c", "local", str(playbook),
                   "-e", "enderpin_backend_address=192.0.2.2"]
        for extra, expected in [([], {"survival": "192.0.2.2:25566"}),
                                (["-e", "enderpin_creative_address=198.51.100.2"],
                                 {"survival": "192.0.2.2:25566", "creative": "198.51.100.2:25566"}),
                                (["-e", "enderpin_creative_address=198.51.100.2", "-e", "enderpin_creative_port=25567"],
                                 {"survival": "192.0.2.2:25566", "creative": "198.51.100.2:25567"})]:
            result = subprocess.run(command + extra, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(result.returncode, 0, result.stdout)
            rendered = json.loads(output.read_text())
            self.assertEqual(rendered, {"backends": expected, "try": ["survival"]})

    def test_creative_workspace_and_box_assets(self):
        self.assertEqual(filters.workspace(BASE.parent, "servers/creative")["runtime"], "fabric")
        secret = self.root / "forwarding.secret"
        secret.write_text("a" * 64 + "\n")
        local = self.root / "local.yml"
        example = yaml.safe_load((BASE / "box/local.example.yml").read_text())
        local.write_text(yaml.safe_dump(example | dict(forwarding_secret_file=str(secret))))
        config = box.load(local)
        generated, rendered = box.render(config)
        properties = generated["files/server/server.properties"]
        for line in ["gamemode=creative", "force-gamemode=true", "max-players=10", "server-ip=198.51.100.2",
                     "server-port=25566", "online-mode=true", "enable-rcon=false"]:
            self.assertIn(line + "\n", properties)
        self.assertEqual(tomllib.loads(generated["files/server/config/FabricProxy-Lite.toml"])["secret"], "a" * 64)
        voice = generated["files/server/config/voicechat/voicechat-server.properties"]
        self.assertIn("bind_address=198.51.100.2\n", voice)
        self.assertIn('iifname "epvpn" ip saddr 198.51.100.1 tcp dport 25566 accept', rendered["firewall"])
        self.assertIn('iifname "epvpn" ip saddr 198.51.100.1 udp dport 24454 accept', rendered["firewall"])
        self.assertLess(rendered["firewall"].index("tcp dport 25566 accept"), rendered["firewall"].index("tcp dport 25566 drop"))
        client = rendered["openvpn"]
        for line in ["proto tcp-client", "http-proxy 127.0.0.1 8791", "remote vps.example.com 443", "route-nopull",
                     "verify-x509-name enderpin-gateway name", "tun-mtu 1400", "mssfix 1360"]:
            self.assertIn(line + "\n", client)
        self.assertNotIn("a" * 64, json.dumps(rendered))
        # The box must render byte-identical files to Ansible's template engine.
        playbook = self.root / "templates.yml"
        output = self.root / "ansible.json"
        playbook.write_text(yaml.safe_dump([dict(hosts="localhost", gather_facts=False,
            vars=dict(instance=config["instance"], source=config["source"], enderpin_forwarding_secret="a" * 64),
            tasks=[{"ansible.builtin.copy": {"content": "{{ {'p': lookup('ansible.builtin.template', 'templates/server.properties.j2'), "
                                                        "'f': lookup('ansible.builtin.template', 'templates/FabricProxy-Lite.toml.j2'), "
                                                        "'v': lookup('ansible.builtin.template', 'templates/voicechat-server.properties.j2')} | to_json }}",
                                             "dest": str(output)}, "no_log": True}])]))
        (self.root / "templates").symlink_to(BASE / "ansible/templates")
        result = subprocess.run([str(Path(sys.executable).with_name("ansible-playbook")), "-i", "localhost,", "-c", "local",
                                 str(playbook)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, 0, result.stdout)
        ansible = json.loads(output.read_text())
        self.assertEqual(ansible["p"], properties)
        self.assertEqual(ansible["f"], generated["files/server/config/FabricProxy-Lite.toml"])
        self.assertEqual(ansible["v"], voice)
        for invalid in [dict(creative_address="198.51.100.1"), dict(http_proxy="127.0.0.1"), dict(root="/srv"),
                        dict(vpn_endpoint="vps.example.com; rm"), dict(vpn_port=True)]:
            local.write_text(yaml.safe_dump(example | dict(forwarding_secret_file=str(secret)) | invalid))
            with self.subTest(invalid=invalid), self.assertRaises(SystemExit):
                box.load(local)
        secret.write_text("short")
        local.write_text(yaml.safe_dump(example | dict(forwarding_secret_file=str(secret))))
        with self.assertRaises(SystemExit):
            box.load(local)

    def test_bedrock_public_address_resolution(self):
        self.assertEqual(filters.resolve_ipv4("192.0.2.10"), "192.0.2.10")
        with patch.object(filters.socket, "gethostbyname", return_value="192.0.2.10") as resolve:
            self.assertEqual(filters.resolve_ipv4("vpn.example.com"), "192.0.2.10")
            resolve.assert_called_once_with("vpn.example.com")
        with patch.object(filters.socket, "gethostbyname", side_effect=OSError("unavailable")):
            with self.assertRaises(AnsibleFilterError):
                filters.resolve_ipv4("unavailable.example.com")

    def test_public_firewall_ports_and_gateway_conditions(self):
        tasks = yaml.safe_load((BASE / "ansible/deploy.yml").read_text())
        firewall = [task for play in tasks for task in play["pre_tasks"]
                    if "ansible.builtin.iptables" in task]
        self.assertEqual(len(firewall), 2)
        for task in firewall:
            rule = task.pop("ansible.builtin.iptables")
            task["ansible.builtin.debug"] = {"msg": rule["protocol"] + ":" + rule["destination_port"]}
        variables = dict(enderpin_instances=[self.instance | dict(port=25565, voicechat_port=24454, bedrock_port=19132),
                                            self.instance | dict(name="plain", port=25567)])
        playbook = self.root / "firewall.yml"
        playbook.write_text(yaml.safe_dump([dict(hosts="localhost", gather_facts=False, vars=variables, tasks=firewall)]))
        inventory = self.root / "firewall-inventory.yml"
        command = [str(Path(sys.executable).with_name("ansible-playbook")), "-i", str(inventory), "-c", "local", str(playbook)]
        for gateway in [True, False]:
            inventory.write_text(yaml.safe_dump({"all": {"hosts": {"localhost": {}},
                "children": {"vpn_gateway": {"hosts": {"localhost": {}}}} if gateway else {}}}))
            result = subprocess.run(command,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(result.returncode, 0, result.stdout)
            ports = re.findall(r'"msg": "((?:tcp|udp):[0-9]+)"', result.stdout)
            self.assertEqual(ports, ["tcp:25565", "tcp:19132", "tcp:25567", "udp:24454", "udp:19132"] if gateway else [], result.stdout)

    def test_deployment_fingerprint_tracks_only_effective_instance_inputs(self):
        binary = self.root / "enderpin"
        binary.write_bytes(b"version 0.1.5")
        source = filters.workspace(self.root, "servers/backend")
        generated = {"files/server/server.properties": "online-mode=false\n"}
        def fingerprint():
            return deployment.fingerprint(source, binary, generated, "service")
        original = fingerprint()
        (self.root / "client").mkdir()
        (self.root / "client/enderpin.toml").write_text("client change")
        (self.root / "servers/other").mkdir()
        (self.root / "servers/other/enderpin.toml").write_text("other server change")
        (self.source / "run").mkdir()
        (self.source / "run/world").write_text("never deploy a live world")
        (self.source / "enderpin.toml").touch()
        self.assertEqual(fingerprint(), original)
        for path in [self.source / "enderpin.toml", self.source / "enderpin.lock", binary]:
            before = path.read_bytes()
            path.write_bytes(before + b"\nchanged")
            self.assertNotEqual(fingerprint(), original)
            path.write_bytes(before)
        generated["files/server/forwarding.secret"] = "a" * 64
        self.assertNotEqual(fingerprint(), original)
        del generated["files/server/forwarding.secret"]
        self.assertNotEqual(deployment.fingerprint(source, binary, generated, "new memory setting"), original)
        shared = self.source / "files/common/config.json"
        shared.parent.mkdir(parents=True)
        shared.write_text("{}")
        source = filters.workspace(self.root, "servers/backend")
        added = fingerprint()
        self.assertNotEqual(added, original)
        shared.chmod(0o600)
        private = fingerprint()
        shared.chmod(0o644)
        self.assertNotEqual(fingerprint(), private)
        shared.unlink()
        self.assertNotEqual(fingerprint(), added)

    def test_bundle_contains_only_managed_inputs_and_preserves_private_modes(self):
        binary = self.root / "enderpin"
        binary.write_bytes(b"version 0.1.5")
        (self.source / "run").mkdir()
        (self.source / "run/world").write_text("do not ship")
        shared = self.source / "files/common/script.sh"
        shared.parent.mkdir(parents=True)
        shared.write_text("#!/bin/sh\n")
        shared.chmod(0o755)
        generated = {"files/server/config/FabricProxy-Lite.toml": 'secret="' + "a" * 64 + '"\n'}
        source = filters.workspace(self.root, "servers/backend")
        archive_path = self.root / "inputs.tar.gz"
        subprocess.run([sys.executable, str(BASE / "ansible/filter_plugins/deployment.py")],
                       input=json.dumps(dict(source=source, binary=str(binary), generated=generated,
                                             destination=str(archive_path))), text=True, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with tarfile.open(archive_path) as archive:
            names = archive.getnames()
            self.assertFalse(any("run" in Path(name).parts for name in names))
            self.assertTrue(all(name.startswith(("workspace/", "bin/")) for name in names))
            for name, mode in [("workspace/enderpin.toml", 0o600), ("bin/enderpin", 0o700),
                               ("workspace/files/common/script.sh", 0o755),
                               ("workspace/files/server/config/FabricProxy-Lite.toml", 0o600)]:
                self.assertEqual(archive.getmember(name).mode, mode)
            self.assertEqual(archive.extractfile("workspace/files/server/config/FabricProxy-Lite.toml").read().decode(),
                             generated["files/server/config/FabricProxy-Lite.toml"])
        with self.assertRaises(ValueError):
            deployment.bundle(source, binary, {"files/server/../../../escape": "secret"}, archive_path)
        shared.unlink()
        shared.symlink_to(binary)
        with self.assertRaises(ValueError):
            deployment.bundle(source, binary, generated, archive_path)

    @unittest.skipUnless(sys.platform == "linux", "Ansible unarchive requires GNU tar; exercised on Linux CI")
    def test_ansible_expands_one_bundle_for_staging_and_install_without_touching_worlds(self):
        binary = self.root / "enderpin"
        binary.write_text("0.1.5")
        archive = self.root / "inputs.tar.gz"
        deployment.bundle(filters.workspace(self.root, "servers/backend"), binary,
                          {"files/server/config/test.toml": 'secret="' + "a" * 64 + '"\n'}, archive)
        staged = self.root / "stage"
        staged.mkdir()
        shutil.copy(archive, staged / "inputs.tar.gz")
        installed = self.root / "installed"
        world = installed / "workspace/run/world/level.dat"
        world.parent.mkdir(parents=True)
        world.write_text("existing world")
        obsolete = installed / "workspace/files/server/old.toml"
        obsolete.parent.mkdir(parents=True)
        obsolete.write_text("obsolete")
        block = yaml.safe_load((BASE / "ansible/apply.yml").read_text())[-1]["block"]
        tasks = []
        for task in block:
            if "ansible.builtin.unarchive" in task:
                task["ansible.builtin.unarchive"].pop("owner")
                task["ansible.builtin.unarchive"].pop("group")
                tasks.append(task)
            elif task["name"] == "Remove obsolete shared inputs after backup":
                tasks.append(task)
        playbook = self.root / "extract.yml"
        playbook.write_text(yaml.safe_dump([dict(hosts="localhost", gather_facts=False,
            vars=dict(staged=dict(path=str(staged)), instance_root=str(installed)), tasks=tasks)]))
        result = subprocess.run([str(Path(sys.executable).with_name("ansible-playbook")), "-i", "localhost,",
                                 "-c", "local", str(playbook)], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(world.read_text(), "existing world")
        self.assertFalse(obsolete.exists())
        for directory in [staged, installed]:
            self.assertEqual((directory / "bin/enderpin").stat().st_mode & 0o777, 0o700)
            self.assertEqual((directory / "workspace/files/server/config/test.toml").stat().st_mode & 0o777, 0o600)

    def test_ansible_skips_successful_instances_and_retries_failed_updates(self):
        # Exercise real task conditions and template lookups; the cutover itself is a local stand-in.
        playbooks = self.root / "playbooks"
        playbooks.mkdir()
        shutil.copy(BASE / "ansible/instance.yml", playbooks)
        shutil.copytree(BASE / "ansible/templates", playbooks / "templates")
        shutil.copytree(BASE / "ansible/filter_plugins", playbooks / "filter_plugins")
        shutil.copytree(BASE / "ansible/files", playbooks / "files")
        shutil.copy(BASE / "ansible/deploy.yml", playbooks)
        (playbooks / "apply.yml").write_text('''
- name: Invalidate the previous success before simulated cutover
  ansible.builtin.file:
    path: "{{ instance_root }}/.deployment-sha256"
    state: absent
- name: Simulate failure before recording a successful deployment
  ansible.builtin.fail:
    msg: simulated failure
  when: simulate_failure | default(false) | bool
- name: Record the simulated successful deployment
  ansible.builtin.copy:
    content: "{{ deployment_fingerprint }}\\n"
    dest: "{{ instance_root }}/.deployment-sha256"
    mode: '0600'
- name: Capture generated files for assertions
  ansible.builtin.copy:
    content: "{{ deployment_files | to_json }}"
    dest: "{{ instance_root }}/generated.json"
    mode: '0600'
  no_log: true
''')
        binary = self.root / "enderpin"
        binary.write_text("0.1.5")
        instance_root = self.root / "installed/backend"
        instance_root.mkdir(parents=True)
        variables = self.inventory | dict(enderpin_source_root=str(self.root), enderpin_root=str(instance_root.parent),
                       enderpin_binary=str(binary), enderpin_forwarding_secret="a" * 64,
                       ansible_host="192.0.2.10",
                       enderpin_discord_token='test-token-"\\private', enderpin_discord_channel_id="123456789012345678",
                       instance=self.instance, enderpin_accept_eula=True)
        variables.pop("enderpin_vpn")
        (playbooks / "test.yml").write_text(yaml.safe_dump([dict(hosts="localhost", gather_facts=False,
            vars=variables, tasks=[{"ansible.builtin.include_tasks": "instance.yml"}])]))
        command = [str(Path(sys.executable).with_name("ansible-playbook")), "-i", "localhost,", "-c", "local", "test.yml"]
        def run(*arguments):
            return subprocess.run(command + list(arguments), cwd=playbooks, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        first = run()
        self.assertEqual(first.returncode, 0, first.stdout)
        marker = instance_root / ".deployment-sha256"
        before = marker.read_bytes()
        generated = json.loads((instance_root / "generated.json").read_text())
        self.assertFalse(yaml.safe_load(generated["files/server/spigot.yml"])["settings"]["bungeecord"])
        optional = ("files/server/config/voicechat/voicechat-server.properties",
                    "files/server/plugins/voicechat/voicechat-proxy.properties",
                    "files/server/plugins/Geyser-Velocity/config.yml")
        self.assertTrue(all(path not in generated for path in optional))
        second = run("-e", "enderpin_revision=" + "b" * 40)
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertIn("inputs unchanged; skipping transfer", second.stdout)
        self.assertIn("changed=0", second.stdout)
        self.assertNotIn("Record the simulated successful deployment", second.stdout)
        self.assertNotIn("a" * 64, first.stdout + second.stdout)
        binary.write_text("changed binary")
        failed = run("-e", "simulate_failure=true")
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertFalse(marker.exists())
        binary.write_text("0.1.5")
        retry = run()
        self.assertEqual(retry.returncode, 0, retry.stdout)
        self.assertIn("Record the simulated successful deployment", retry.stdout)
        self.assertEqual(marker.read_bytes(), before)
        for runtime in ["fabric", "velocity"]:
            manifest = self.manifest.replace('loader="paper"', f'loader="{runtime}"')
            lock = f'[runtimes.server]\nloader="{runtime}"\n'
            if runtime == "fabric":
                manifest += '[server.packages.proxy]\nmodrinth="fabricproxy-lite"\n'
                lock += '[[targets.server.packages]]\nname="fabricproxy-lite"\nkind="mod"\nroots=["proxy"]\n'
            (self.source / "enderpin.toml").write_text(manifest)
            (self.source / "enderpin.lock").write_text(lock)
            enabled = self.instance | dict(voicechat_port=24454)
            if runtime == "velocity":
                enabled["bedrock_port"] = 19132
            result = run("-e", json.dumps({"instance": enabled}))
            self.assertEqual(result.returncode, 0, result.stdout)
            generated = json.loads((instance_root / "generated.json").read_text())
            if runtime == "fabric":
                self.assertIn("online-mode=true", generated["files/server/server.properties"])
                self.assertEqual(tomllib.loads(generated["files/server/config/FabricProxy-Lite.toml"])["secret"], "a" * 64)
                self.assertNotIn("files/server/plugins/discordintegrationvelocity/config.json", generated)
                self.assertIn("port=24454", generated[optional[0]])
                self.assertNotIn(optional[1], generated)
                self.assertNotIn(optional[2], generated)
            else:
                self.assertEqual(generated["files/server/forwarding.secret"], "a" * 64)
                self.assertEqual(tomllib.loads(generated["files/server/velocity.toml"])["player-info-forwarding-mode"], "modern")
                discord = json.loads(generated["files/server/plugins/discordintegrationvelocity/config.json"])
                self.assertTrue(discord["authentication"]["enabled"])
                self.assertTrue(tomllib.loads(generated["files/server/velocity.toml"])["online-mode"])
                self.assertEqual(discord["discord"]["token"], variables["enderpin_discord_token"])
                self.assertEqual(discord["discord"]["channelId"], variables["enderpin_discord_channel_id"])
                self.assertEqual(discord["minecraft"]["syncedServers"], list(self.inventory["enderpin_backends"]))
                self.assertNotIn("test-token", result.stdout)
                self.assertNotIn(optional[0], generated)
                self.assertIn("port=24454", generated[optional[1]])
                geyser = yaml.safe_load(generated[optional[2]])
                self.assertEqual(geyser["bedrock"]["transport"], "nethernet")
                self.assertEqual(geyser["bedrock"]["port"], 19132)
                self.assertEqual(geyser["bedrock"]["signaling"]["builtin"]["advertise-addresses"], ["192.0.2.10"])
                self.assertEqual(geyser["java"]["auth-type"], "floodgate")
                floodgate = yaml.safe_load(generated["files/server/plugins/floodgate/config.yml"])
                self.assertEqual(floodgate["username-prefix"], ".")
                self.assertFalse(floodgate["player-link"]["enabled"])
                self.assertFalse(floodgate["send-floodgate-data"])
                self.assertFalse(floodgate["metrics"]["enabled"])
                self.assertTrue(floodgate["disconnect"]["invalid-key"])
        before_port_change = marker.read_bytes()
        changed = run("-e", json.dumps({"instance": enabled | dict(voicechat_port=24455)}))
        self.assertEqual(changed.returncode, 0, changed.stdout)
        self.assertIn("Record the simulated successful deployment", changed.stdout)
        self.assertNotEqual(marker.read_bytes(), before_port_change)
        self.assertIn("port=24455", json.loads((instance_root / "generated.json").read_text())[optional[1]])
        before_port_change = marker.read_bytes()
        changed = run("-e", json.dumps({"instance": enabled | dict(bedrock_port=19133)}))
        self.assertEqual(changed.returncode, 0, changed.stdout)
        self.assertNotEqual(marker.read_bytes(), before_port_change)
        self.assertEqual(yaml.safe_load(json.loads((instance_root / "generated.json").read_text())[optional[2]])
                         ["bedrock"]["port"], 19133)
        disabled = run()
        self.assertEqual(disabled.returncode, 0, disabled.stdout)
        self.assertTrue(all(path not in json.loads((instance_root / "generated.json").read_text()) for path in optional))
        before = marker.read_bytes()
        for invalid in ["enderpin_discord_token=", "enderpin_discord_channel_id=invalid"]:
            rejected = run("-e", invalid)
            self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
            self.assertNotIn("Record the simulated successful deployment", rejected.stdout)
            self.assertNotIn("test-token", rejected.stdout)
            self.assertEqual(marker.read_bytes(), before)

    def test_rendered_configuration_and_secret_consistency(self):
        environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
        environment.filters.update(filters.FilterModule().filters())
        environment.filters["to_json"] = json.dumps
        variables = self.inventory | dict(instance=self.instance | dict(motd="サーバー 🐈"),
                       source=dict(runtime="velocity"), enderpin_forwarding_secret="a" * 64,
                       enderpin_instances=[self.instance], enderpin_source_root=str(self.root),
                       inventory_hostname="home", groups=dict(vpn_clients=["home"]),
                       ansible_facts=dict(os_family="Debian"),
                       hostvars=dict(localhost=dict(vpn_network=dict(network="192.0.2.0", netmask="255.255.255.0"),
                                                    vpn_tcp_network=dict(network="198.51.100.0", netmask="255.255.255.248"))))
        def render(name):
            return environment.from_string((BASE / "ansible/templates" / name).read_text()).render(**variables)
        proxy = tomllib.loads(render("velocity.toml.j2"))
        self.assertEqual(proxy["player-info-forwarding-mode"], "modern")
        self.assertEqual(proxy["forced-hosts"], {})
        self.assertEqual(proxy["motd"], "サーバー 🐈")
        fabric = tomllib.loads(render("FabricProxy-Lite.toml.j2"))
        paper = yaml.safe_load(render("paper-global.yml.j2"))
        self.assertEqual(fabric["secret"], paper["proxies"]["velocity"]["secret"])
        self.assertTrue(fabric["hackOnlineMode"])
        variables["source"]["runtime"] = "fabric"
        properties = render("server.properties.j2")
        self.assertIn("online-mode=true", properties)
        variables["source"]["runtime"] = "paper"
        self.assertIn("online-mode=false", render("server.properties.j2"))
        firewall = render("firewall.nft.j2")
        self.assertIn('ip saddr 192.0.2.1 tcp dport 25566 accept', firewall)
        self.assertIn('tcp dport 25566 drop', firewall)
        self.assertNotIn('udp dport', firewall)
        variables["enderpin_instances"] = [self.instance | dict(voicechat_port=24454)]
        variables["instance"] = self.instance | dict(voicechat_port=24454)
        self.assertIn('port=24454', render("voicechat-server.properties.j2"))
        self.assertIn('bind_address=192.0.2.2', render("voicechat-server.properties.j2"))
        voice_firewall = render("firewall.nft.j2")
        self.assertIn('iifname "epvpn" ip saddr 192.0.2.1 udp dport 24454 accept', voice_firewall)
        self.assertIn('udp dport 24454 drop', voice_firewall)
        self.assertLess(voice_firewall.index('udp dport 24454 accept'), voice_firewall.index('udp dport 24454 drop'))
        variables["source"]["runtime"] = "velocity"
        self.assertIn('port=24454', render("voicechat-proxy.properties.j2"))
        self.assertIn('bind_address=192.0.2.2', render("voicechat-proxy.properties.j2"))
        self.assertNotIn('flush ruleset', firewall)
        service = render("enderpin.service.j2")
        self.assertIn("--service --offline", service)
        self.assertIn("KillMode=mixed", service)
        self.assertIn("Requires=openvpn-client@enderpin.service", service)
        self.assertIn("route-nopull", render("openvpn-client.conf.j2"))
        variables["ansible_facts"]["os_family"] = "RedHat"
        self.assertIn("group nobody", render("openvpn-client.conf.j2"))
        self.assertNotIn("ca.key", render("openvpn-server.conf.j2"))
        tcp = render("openvpn-server-tcp.conf.j2")
        for line in ["port 443", "proto tcp4-server", "dev epvpn-tcp", "server 198.51.100.0 255.255.255.248 nopool",
                     "client-config-dir /etc/openvpn/server/enderpin-tcp/ccd", "ccd-exclusive", "max-clients 1",
                     "tls-crypt /etc/openvpn/server/enderpin/tls-crypt.key", "remote-cert-tls client"]:
            self.assertIn(line + "\n", tcp)
        self.assertNotIn("ca.key", tcp)
        self.assertNotIn("client-to-client", tcp)
        for name in ["openvpn-server.conf.j2", "openvpn-server-tcp.conf.j2", "openvpn-client.conf.j2"]:
            self.assertIn("tun-mtu 1400", render(name))
            self.assertIn("mssfix 1360", render(name))

    def test_vpn_pki_is_persistent(self):
        inventory = yaml.safe_load((BASE / "ansible/inventory.example.yml").read_text())
        inventory["all"]["vars"]["enderpin_pki_directory"] = str(self.root / "pki")
        (self.root / "inventory.yml").write_text(yaml.safe_dump(inventory))
        command = [str(Path(sys.executable).with_name("ansible-playbook")), "-i", str(self.root / "inventory.yml"),
                   "vpn.yml", "--limit", "localhost"]
        identities = None
        for attempt in range(2):
            result = subprocess.run(command, cwd=BASE / "ansible", text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(result.returncode, 0, result.stdout)
            current = {p.name: p.read_bytes() for p in (self.root / "pki").iterdir() if p.suffix in [".key", ".crt"]}
            if attempt:
                self.assertEqual(identities, current)
                self.assertIn("changed=0", result.stdout)
            identities = current
        self.assertIn("box.crt", identities)
        subprocess.run(["openssl", "verify", "-CAfile", str(self.root / "pki/ca.crt"), "-purpose", "sslclient",
                        str(self.root / "pki/box.crt")], check=True, capture_output=True)
        key = (self.root / "pki/tls-crypt.key").read_text().splitlines()
        self.assertEqual(len(key), 18)
        self.assertTrue(all(re.fullmatch(r"[a-f0-9]{32}", line) for line in key[1:-1]))

    def test_cd_commit_credentials_and_cleanup(self):
        environment = dict(GITHUB_SHA="b" * 40, ENDERPIN_REVISION="a" * 40,
                           ENDERPIN_INVENTORY="all: {}", ENDERPIN_VAULT="encrypted",
                           ENDERPIN_VAULT_PASSWORD="secret", ENDERPIN_MANAGE_VPN="true",
                           HACKCLUB_CDN_API_KEY="sk_cdn_test", ENDERPIN_BACKUP_AGE_RECIPIENT="age1test")
        calls = []
        def run(arguments, **kwargs):
            directory = Path(arguments[2]).parent
            self.assertEqual((directory / "password").stat().st_mode & 0o777, 0o600)
            self.assertNotIn("ENDERPIN_VAULT_PASSWORD", kwargs["env"])
            self.assertNotIn("HACKCLUB_CDN_API_KEY", kwargs["env"])
            self.assertEqual((directory / "backup.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((directory / "backup.json").read_text())["enderpin_backup_api_key"],
                             "sk_cdn_test")
            self.assertEqual(json.loads(arguments[-2])["enderpin_revision"], "a" * 40)
            calls.append((arguments[-1], directory))
        with patch.dict(os.environ, environment, clear=True), patch.object(cd.subprocess, "run", side_effect=run):
            cd.main()
        self.assertEqual([call[0] for call in calls], ["vpn.yml", "deploy.yml"])
        self.assertFalse(calls[0][1].exists())
        with patch.dict(os.environ, environment | dict(ENDERPIN_REVISION="branch"), clear=True):
            with self.assertRaises(ValueError):
                cd.main()
        with patch.dict(os.environ, environment, clear=True), patch.object(cd.subprocess, "run", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                cd.main()
        with patch.dict(os.environ, environment | dict(HACKCLUB_CDN_API_KEY=""), clear=True):
            with self.assertRaises(ValueError):
                cd.main()


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive = self.root / ("backend-" + "a" * 40 + "-20261019T010203.tar.gz.age")

    def server(self, status=201, overrides=None):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests.append((self.path, self.headers, body))
                filename = re.search(rb'filename="([^"]+)"', body.split(b"\r\n\r\n", 1)[0])[1].decode()
                payload = body.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
                identifier = f"test-upload-{len(requests)}"
                receipt = dict(id=identifier, filename=filename, size=len(payload),
                               url=f"https://cdn.hackclub.com/{identifier}/{filename}")
                receipt.update(overrides or {})
                self.send_response(status if isinstance(status, int) else status[len(requests) - 1])
                self.end_headers()
                self.wfile.write(json.dumps(receipt).encode())
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(cleanup)
        def connect(host, timeout):
            self.assertEqual(host, "cdn.hackclub.com")
            self.assertEqual(timeout, 180)
            return http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=timeout)
        return patch.object(backup.http.client, "HTTPSConnection", side_effect=connect), requests

    def test_streamed_backup_upload_contains_only_ciphertext_and_can_be_restored(self):
        identity = self.root / "identity"
        subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
        recipient = subprocess.check_output(["age-keygen", "-y", str(identity)], text=True).strip()
        workspace = self.root / "workspace"
        world = workspace / "run/server/world/level.dat"
        world.parent.mkdir(parents=True)
        world.write_text("private world data")
        secret = workspace / "files/server/forwarding.secret"
        secret.parent.mkdir(parents=True)
        secret.write_text("never upload this secret as plaintext")
        connection, requests = self.server()
        with connection:
            receipt = backup.backup(workspace, self.archive, recipient, "sk_cdn_test")
        path, headers, body = requests[0]
        self.assertEqual(path, "/api/v4/upload")
        self.assertEqual(headers["Authorization"], "Bearer sk_cdn_test")
        self.assertIn(b'name="file"', body)
        self.assertNotIn(secret.read_bytes(), body)
        self.assertNotIn(b"sk_cdn_test", body)
        self.assertTrue(self.archive.read_bytes().startswith(b"age-encryption.org/v1"))
        self.assertEqual(receipt["sha256"], hashlib.sha256(self.archive.read_bytes()).hexdigest())
        self.assertEqual(json.loads(Path(str(self.archive) + ".json").read_text()), receipt)
        plaintext = self.root / "restored.tar.gz"
        subprocess.run(["age", "--decrypt", "--identity", str(identity), "--output", str(plaintext), str(self.archive)],
                       check=True, capture_output=True)
        with tarfile.open(plaintext) as restored:
            self.assertEqual(restored.extractfile("./run/server/world/level.dat").read(), world.read_bytes())
            self.assertEqual(restored.extractfile("./files/server/forwarding.secret").read(), secret.read_bytes())

    def test_upload_size_boundaries_and_ordered_chunk_checksums(self):
        for size in [31, 32, 33, 64, 65]:
            with self.subTest(size=size):
                data = os.urandom(size)
                self.archive.write_bytes(data)
                connection, requests = self.server()
                with connection, patch.object(backup, "PART_SIZE", 32):
                    receipt = backup.upload_backup(self.archive, "sk_cdn_test")
                parts = receipt.get("parts", [receipt])
                self.assertEqual(len(requests), (size + 31) // 32)
                self.assertEqual(receipt["size"], size)
                self.assertEqual(receipt["sha256"], hashlib.sha256(data).hexdigest())
                if size > 32:
                    self.assertTrue(receipt["complete"])
                payloads = []
                for index, ((_, headers, body), part) in enumerate(zip(requests, parts)):
                    payload = body.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
                    self.assertEqual(len(body), int(headers["Content-Length"]))
                    self.assertEqual(part["size"], len(payload))
                    self.assertLessEqual(len(payload), 32)
                    self.assertEqual(part["sha256"], hashlib.sha256(payload).hexdigest())
                    expected_name = self.archive.name + (f".part-{index:06d}" if size > 32 else "")
                    self.assertEqual(part["archive"], expected_name)
                    payloads.append(payload)
                self.assertEqual(b"".join(payloads), data)
                receipt_path = Path(str(self.archive) + ".json")
                self.assertEqual(json.loads(receipt_path.read_text()), receipt)
                self.assertFalse(Path(str(receipt_path) + ".tmp").exists())
                receipt_path.unlink()

    def test_failed_second_chunk_retains_original_and_incomplete_manifest(self):
        data = os.urandom(100)
        self.archive.write_bytes(data)
        connection, requests = self.server([201, 402])
        with connection, patch.object(backup, "PART_SIZE", 32), self.assertRaises(RuntimeError):
            backup.upload_backup(self.archive, "sk_cdn_test")
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.archive.read_bytes(), data)
        receipt = json.loads(Path(str(self.archive) + ".json").read_text())
        self.assertFalse(receipt["complete"])
        self.assertEqual(len(receipt["parts"]), 1)
        self.assertEqual(receipt["parts"][0]["id"], "test-upload-1")
        self.assertEqual(receipt["parts"][0]["sha256"], hashlib.sha256(data[:32]).hexdigest())

    def test_upload_failure_or_invalid_receipt_never_records_success(self):
        self.archive.write_bytes(b"encrypted contents")
        for status, override in [(401, {}), (402, {}), (500, {}), (201, {"size": -1}),
                                 (201, {"url": "https://cdn.hackclub.com/test-upload-1/wrong"}),
                                 (201, {"url": "https://example.com/wrong"})]:
            with self.subTest(status=status, override=override):
                connection, _ = self.server(status, override)
                with connection, self.assertRaises((RuntimeError, ValueError)):
                    backup.upload(self.archive, "sk_cdn_test")
                self.assertTrue(self.archive.exists())
                self.assertFalse(Path(str(self.archive) + ".json").exists())
        with self.assertRaises(ValueError):
            backup.upload(self.archive, "sk_cdn_test\r\nInjected: header")

    def test_bad_recipient_never_uploads_or_leaves_a_partial_backup(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        (workspace / "world").write_text("secret")
        with patch.object(backup, "upload") as upload, self.assertRaises(RuntimeError):
            backup.backup(workspace, self.archive, "invalid-recipient", "sk_cdn_test")
        upload.assert_not_called()
        self.assertFalse(self.archive.exists())


if __name__ == "__main__":
    unittest.main()
