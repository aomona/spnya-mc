# Spnya Minecraft

Minecraft **26.3**。VPSのVelocity 4.2.0 build 30から、OpenVPN内のFabric Loader 0.19.5へ接続します。FabricProxy-Lite v2.12.0でVelocityのmodern forwardingを検証します。

```text
プレイヤー → 公開ゲームアドレス:25565/TCP → Velocity (VPS)
                                            ↓ OpenVPN
                                         Fabric (自宅)
```

Velocityはオンライン認証を有効にし、FabricはVPNアドレスにbindします。自宅のルーターでゲーム用ポートを開放する必要はありません。初期割り当てはVelocity 256MiB、Fabric 2048MiB、最大10人です。

## 設定の編集

- `servers/proxy/enderpin.toml`：Velocityのバージョン。
- `servers/survival/enderpin.toml`：FabricとMOD。変更後は新しいEnderpinで`sync --target server`を実行し、`enderpin.lock`もコミットします。
- `deploy/ansible/host_vars/home.yml`：Fabricのメモリ、MOTD、最大人数、描画距離など。
- `deploy/ansible/host_vars/edge.yml`：Velocityのメモリ、MOTDなど。
- `deploy/ansible/group_vars/minecraft.yml`：共通の配置先や接続するサーバー名。
- `deploy/inventory.yml`：IP、SSHユーザー、接続方法。**Git管理外**のローカル設定。
- `servers/<名前>/files/server/`：追加の共有設定。`server.properties`、`velocity.toml`と転送認証設定はAnsibleが生成するため、ここに重ねて置きません。

実際のIP、ワールド、プレイヤーデータ、実行時ファイル、SSH秘密鍵、転送用secretはGitへ入れません。公開するinventoryの例とテストには、説明用のアドレスだけを使用します。以前のコミット履歴は保持しています。

## mainへのpushによる自動配布

`main`へpushすると、GitHub Actionsが次の順で処理します。

1. 設定・テストを検証し、固定コミットのEnderpinをLinux向けにビルド。
2. 両サーバーのlockを検証し、その実行の成果物を保存。
3. 成功したpushのコミットと成果物を使い、GitHubホストのrunnerからAnsibleを実行。
4. Fabric、Velocityの順で、停止・バックアップ・更新・起動確認。

PRの検証からは配布しません。配布処理は同時に走らず、実行中の更新を新しいpushで中断しません。更新に失敗したインスタンスは停止し、ワールドを自動で巻き戻しません。Actionsの`Deploy Minecraft`で失敗箇所を確認してください。

GitHub Secretsは以下を登録します。接続先はコードと分離し、専用SSH鍵でVPSを経由して自宅に接続します。自宅ルーターのSSHポート開放は不要です。

| Secret | 内容 |
| --- | --- |
| `ENDERPIN_INVENTORY` | 実際の接続先・VPNアドレスを含むinventory |
| `ENDERPIN_SSH_KEY` | Actions専用のSSH秘密鍵 |
| `ENDERPIN_SSH_KNOWN_HOSTS` | 本人確認済みの両ホストのSSH公開ホスト鍵 |
| `ENDERPIN_VAULT` | Ansible Vaultで暗号化した共有転送secret |
| `ENDERPIN_VAULT_PASSWORD` | Vaultの復号パスワード |

1Passwordの個人用SSH鍵はGitHubへ渡しません。SSHはホスト鍵を必ず検証し、実際のIPは配布ログでもマスクします。追加・変更時は、秘密値をコマンド引数に直書きせず`gh secret set NAME < 非公開ファイル`などで登録してください。

## 配布

Macから手動で配布することもできます。ローカルの`deploy/inventory.yml`はVPSを踏み台に自宅のVPNアドレスへ接続するため、自宅LANの外でも使用できます。VPSには1PasswordのSSH agent、自宅には`~/.ssh/enderpin_ed25519`を使用します。SSHのknown_hostsは事前に確認してください。VPNは既存の構成を使用します。

別のコントローラーを用意する場合は、`deploy/inventory.example.yml`からローカルの`deploy/inventory.yml`を作り、非公開の接続先へ置き換えてください。ゲーム設定はplaybookディレクトリ内の`group_vars`と`host_vars`から読み込みます。

VPNのMTUは1400、`mssfix`は1360に設定し、VPN越しのSSHで大きな鍵交換パケットが途切れる問題を避けています。

```sh
python3 -m venv .venv
.venv/bin/pip install -r deploy/ansible/requirements.txt
mkdir -p .local
# 対象コミットの成功したCheck workflowのrun IDを指定
gh run download RUN_ID -R aomona/spnya-mc -n enderpin-linux-x86_64 -D .local
(cd .local && shasum -a 256 -c enderpin.sha256)
```

ツールはEnderpinのコミット`9595d767a12b9659cdc982234e53dd53c2a024ca`からビルドします。配信版0.1.3には必要な機能がないため、同じバージョン表示でも置き換えないでください。

共有転送secretは設定済みです。`deploy/secrets.yml`は`.gitignore`対象で、以後の配布でも同じ値を使います。inventory・SSH鍵と一緒に非公開の場所へバックアップしてください。以下は新規環境で初回だけ実行する生成例です。

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

サービス名は`enderpin-survival`と`enderpin-proxy`です。実行ファイル・キャッシュ・ワールドは各ホストの`/srv/enderpin/<名前>/`、更新前のバックアップは同ディレクトリの`backups/`に保存します。復元時は停止してから内容を確認し、ワールドを自動で巻き戻さないでください。自宅には`systemd-timesyncd`も導入し、認証・署名付きチャットに必要な時刻同期を維持します。

VPSの公開ゲームポートはTCP 25565です。OCIのSecurity Listでも許可してください。AnsibleはVPSの既存iptablesにゲーム用の許可を追加して永続化し、バックエンド用のnftablesテーブルで自宅のTCP 25566への接続をVPSのVPNアドレスからに限定します。現在のバックアップは各ホスト内への更新前バックアップで、別ホストへの定期バックアップは含みません。

配布テンプレートとテストは`aomona/enderpin`由来（GPL-3.0-only、`LICENSE`参照）です。
