# Spnya Minecraft

Minecraft **26.3**。VPSのVelocity 4.2.0 build 30から、OpenVPN内のFabric Loader 0.19.5へ接続します。FabricProxy-Lite v2.12.0でVelocityのmodern forwardingを検証します。

```text
プレイヤー → 161.33.14.67:25565/TCP → Velocity (VPS)
                                     ↓ epvpn
                                  10.77.0.2:25566 → Fabric (自宅)
```

Velocityはオンライン認証を有効にし、FabricはVPNアドレスにbindします。自宅のルーターでゲーム用ポートを開放する必要はありません。初期割り当てはVelocity 256MiB、Fabric 2048MiB、最大10人です。

## 設定の編集

- `servers/proxy/enderpin.toml`：Velocityのバージョン。
- `servers/survival/enderpin.toml`：FabricとMOD。変更後は新しいEnderpinで`sync --target server`を実行し、`enderpin.lock`もコミットします。
- `deploy/inventory.yml`：配置先、メモリ、MOTD、最大人数、描画距離など。
- `servers/<名前>/files/server/`：追加の共有設定。`server.properties`、`velocity.toml`と転送認証設定はAnsibleが生成するため、ここに重ねて置きません。

ワールド、プレイヤーデータ、実行時ファイル、SSH秘密鍵、転送用secretはGitへ入れません。現在は設定確認とLinux用ツールのビルドだけをGitHub Actionsで実行します。**pushだけでは本番を再起動しません。**

## 配布

Macなど自宅LANへSSH接続できるコントローラーから実行します。VPSには1PasswordのSSH agent、自宅には`~/.ssh/enderpin_ed25519`を使用します。SSHのknown_hostsは事前に確認してください。VPNは既存の構成を使用します。

```sh
python3 -m venv .venv
.venv/bin/pip install -r deploy/ansible/requirements.txt
mkdir -p .local
# 対象コミットの成功したCheck workflowのrun IDを指定
gh run download RUN_ID -R aomona/spnya-mc -n enderpin-linux-x86_64 -D .local
(cd .local && shasum -a 256 -c enderpin.sha256)
```

ツールはEnderpinのコミット`9595d767a12b9659cdc982234e53dd53c2a024ca`からビルドします。配信版0.1.3には必要な機能がないため、同じバージョン表示でも置き換えないでください。

初回だけ、共有転送secretを生成します。`deploy/secrets.yml`は`.gitignore`対象で、以後の配布でも同じ値を使います。SSH鍵と一緒に非公開の場所へバックアップしてください。

```sh
.venv/bin/python - <<'PY'
import os
from pathlib import Path
import secrets
path = Path('deploy/secrets.yml')
with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
    f.write('enderpin_forwarding_secret: ' + secrets.token_hex(32) + '\n')
PY
```

コミット済みの設定を配布します。Fabric、Velocityの順で更新し、各サービスを停止してバックアップした後、既存ワールドを保持したまま設定を反映します。Minecraft EULAへの同意は確認済みです。

```sh
revision=$(git rev-parse HEAD)
cd deploy/ansible
../../.venv/bin/ansible-playbook -i ../inventory.yml deploy.yml \
  -e @../secrets.yml -e "enderpin_revision=$revision"
```

サービス名は`enderpin-survival`と`enderpin-proxy`です。実行ファイル・キャッシュ・ワールドは各ホストの`/srv/enderpin/<名前>/`、更新前のバックアップは同ディレクトリの`backups/`に保存します。復元時は停止してから内容を確認し、ワールドを自動で巻き戻さないでください。

VPSの公開ゲームポートはTCP 25565です。OCIのSecurity Listでも許可してください。AnsibleはVPSの既存iptablesにゲーム用の許可を追加して永続化し、バックエンド用のnftablesテーブルで自宅のTCP 25566への接続をVPSのVPNアドレスからに限定します。

配布テンプレートとテストは`aomona/enderpin`由来（GPL-3.0-only、`LICENSE`参照）です。
