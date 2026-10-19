"""Run with the Python environment from deploy/ansible/requirements.txt."""
import importlib.util
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
        documentation = ipaddress.ip_network("192.0.2.0/24")
        constants = {"0.0.0.0", "127.0.0.1", "255.255.255.0", "255.255.255.255"}
        for name in tracked:
            if not name or Path(name).suffix not in [".yml", ".yaml", ".md", ".py"]:
                continue
            content = (root / name).read_text()
            for address in re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", content):
                self.assertTrue(address in constants or ipaddress.ip_address(address) in documentation,
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
        for overrides in [dict(port=True), dict(name="../../etc"), dict(properties={"motd":"hi\nonline-mode=false"}),
                          dict(properties={"motd":"hi\\"}), dict(fabric_proxy_hack_early_send="true"), dict(memory_mib=-1)]:
            with self.assertRaises(AnsibleFilterError):
                filters.instances([self.instance | overrides])
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
                       instance=self.instance, enderpin_accept_eula=True)
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
            result = run()
            self.assertEqual(result.returncode, 0, result.stdout)
            generated = json.loads((instance_root / "generated.json").read_text())
            if runtime == "fabric":
                self.assertIn("online-mode=true", generated["files/server/server.properties"])
                self.assertEqual(tomllib.loads(generated["files/server/config/FabricProxy-Lite.toml"])["secret"], "a" * 64)
            else:
                self.assertEqual(generated["files/server/forwarding.secret"], "a" * 64)
                self.assertEqual(tomllib.loads(generated["files/server/velocity.toml"])["player-info-forwarding-mode"], "modern")

    def test_rendered_configuration_and_secret_consistency(self):
        environment = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
        environment.filters.update(filters.FilterModule().filters())
        environment.filters["to_json"] = json.dumps
        variables = self.inventory | dict(instance=self.instance | dict(motd="サーバー 🐈"),
                       source=dict(runtime="velocity"), enderpin_forwarding_secret="a" * 64,
                       enderpin_instances=[self.instance], enderpin_source_root=str(self.root),
                       inventory_hostname="home", groups=dict(vpn_clients=["home"]),
                       hostvars=dict(localhost=dict(vpn_network=dict(network="192.0.2.0", netmask="255.255.255.0"))))
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
        self.assertNotIn('flush ruleset', firewall)
        service = render("enderpin.service.j2")
        self.assertIn("--service --offline", service)
        self.assertIn("KillMode=mixed", service)
        self.assertIn("Requires=openvpn-client@enderpin.service", service)
        self.assertIn("route-nopull", render("openvpn-client.conf.j2"))
        self.assertNotIn("ca.key", render("openvpn-server.conf.j2"))
        for name in ["openvpn-server.conf.j2", "openvpn-client.conf.j2"]:
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
        key = (self.root / "pki/tls-crypt.key").read_text().splitlines()
        self.assertEqual(len(key), 18)
        self.assertTrue(all(re.fullmatch(r"[a-f0-9]{32}", line) for line in key[1:-1]))

    def test_cd_commit_credentials_and_cleanup(self):
        environment = dict(GITHUB_SHA="b" * 40, ENDERPIN_REVISION="a" * 40,
                           ENDERPIN_INVENTORY="all: {}", ENDERPIN_VAULT="encrypted",
                           ENDERPIN_VAULT_PASSWORD="secret", ENDERPIN_MANAGE_VPN="true")
        calls = []
        def run(arguments, **kwargs):
            directory = Path(arguments[2]).parent
            self.assertEqual((directory / "password").stat().st_mode & 0o777, 0o600)
            self.assertNotIn("ENDERPIN_VAULT_PASSWORD", kwargs["env"])
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


if __name__ == "__main__":
    unittest.main()
