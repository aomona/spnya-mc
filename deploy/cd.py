"""Deploy one checked-out commit using a pre-provisioned, trusted controller."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main():
    revision = os.environ.get("ENDERPIN_REVISION", os.environ.get("GITHUB_SHA", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("deployment revision must identify one commit")
    root = Path(__file__).resolve().parent.parent
    ansible = Path(sys.executable).with_name("ansible-playbook")
    # Keep credentials outside the checkout and remove them even when Ansible fails.
    with tempfile.TemporaryDirectory(prefix="enderpin-cd-") as directory:
        temporary = Path(directory)
        for name, variable in [("inventory.yml", "ENDERPIN_INVENTORY"),
                               ("vault.yml", "ENDERPIN_VAULT"),
                               ("password", "ENDERPIN_VAULT_PASSWORD")]:
            value = os.environ[variable]
            if not value.strip():
                raise ValueError(f"{variable} cannot be empty")
            path = temporary / name
            path.touch(mode=0o600)
            path.write_text(value)
        backup = {"enderpin_backup_api_key": os.environ["HACKCLUB_CDN_API_KEY"],
                  "enderpin_backup_age_recipient": os.environ["ENDERPIN_BACKUP_AGE_RECIPIENT"]}
        if not all(value.strip() for value in backup.values()):
            raise ValueError("CDN API key and age recipient are required")
        backup_path = temporary / "backup.json"
        backup_path.touch(mode=0o600)
        backup_path.write_text(json.dumps(backup))
        command = [str(ansible), "-i", str(temporary / "inventory.yml"),
                   "--vault-password-file", str(temporary / "password"),
                   "-e", "@" + str(temporary / "vault.yml"),
                   "-e", "@" + str(backup_path),
                   "-e", json.dumps({"enderpin_revision": revision, "enderpin_source_root": str(root)})]
        environment = {key: value for key, value in os.environ.items()
                       if key not in ["ENDERPIN_INVENTORY", "ENDERPIN_VAULT", "ENDERPIN_VAULT_PASSWORD",
                                      "HACKCLUB_CDN_API_KEY", "ENDERPIN_BACKUP_AGE_RECIPIENT"]}
        if os.environ.get("ENDERPIN_MANAGE_VPN", "false").lower() == "true":
            subprocess.run(command + ["vpn.yml"], cwd=root / "deploy/ansible", env=environment, check=True)
        subprocess.run(command + ["deploy.yml"], cwd=root / "deploy/ansible", env=environment, check=True)


if __name__ == "__main__":
    main()
