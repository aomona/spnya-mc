# Spnya Minecraft

Minecraft **26.3**。VPSのVelocity 4.2.0 build 30から、OpenVPN内のFabric Loader 0.19.5へ接続します。FabricProxy-Lite v2.12.0でVelocityのmodern forwardingを検証します。

```text
プレイヤー → 公開ゲームアドレス:25565/TCP → Velocity (VPS)
                                            ↓ OpenVPN
                                         Fabric (自宅)
```

Velocityはオンライン認証を有効にし、FabricはVPNアドレスにbindします。自宅のルーターでゲーム用ポートを開放する必要はありません。初期割り当てはVelocity 256MiB、Fabric 2048MiB、最大10人です。

サーバーの軽量化MODはFabricサーバー側だけに導入し、参加するクライアントへの追加は不要です。

- Lithium 0.26.2：ゲーム処理のCPU負荷を軽減。
- FerriteCore 9.0.0：メモリ使用量を削減。

バニラの挙動を優先し、`servers/survival/files/server/config/lithium.properties`でLithiumの`minimal_nonvanilla`、`experimental`、`ai.poi.fast_portals`、`world.explosions.block_raycast.skip_air`を無効化します。ポータル探索時のチャンク読み込みや、爆発で生成されたブロックの破壊もバニラの処理を使います。両MODはMinecraft 26.3対応版に固定し、更新時にはLithiumの[該当バージョンの最適化一覧](https://github.com/CaffeineMC/lithium/blob/mc26.3-0.26.2/lithium-fabric-mixin-config.md)で挙動差のある項目を再確認してください。実際のワールドでの性能や装置の動作は別途確認が必要です。

## 軽量クライアント用プロファイル

任意で使えるEnderpin用プロファイルを`client/`に用意しています。Minecraft 26.3 / Fabric Loader 0.19.5に、Sodium 0.9.2（正式版）、Lithium 0.26.2、FerriteCore 9.0.0を固定しています。描画・メモリ・シングルプレイの内部サーバー処理を軽量化し、ゲームルールや描画距離の変更は加えません。Lithiumはサーバーと同じ挙動維持設定です。

このリポジトリを取得し、後述する固定コミットのEnderpinを利用するOS向けにビルドして実行します。配信版0.1.3はこのプロファイルに対応していません。

```sh
cd client
enderpin sync --target client --locked
# 初回のみMicrosoftアカウントへログイン
enderpin login
enderpin run
```

`enderpin`はカレントディレクトリの`enderpin.toml`を読み込むため、`client/`内では`-C`の指定は不要です。

起動時に表示される権限を確認して承認してください。ワールド・設定・実行時データは`client/run/client/`に保存され、Git管理外です。Modを変更する場合は`client/enderpin.toml`を編集し、`sync --target client`で`enderpin.lock`を更新します。

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
2. 両サーバーとクライアント用プロファイルのlockを検証し、その実行の成果物を保存。
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
