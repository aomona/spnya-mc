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

このリポジトリを取得し、同梱のwrapperで実行します。Enderpin 0.1.5を自動取得してSHA-256を検証するため、Enderpinの事前インストールは不要です。

```sh
cd client
./enderpinw sync --target client --locked
# 初回のみMicrosoftアカウントへログイン
./enderpinw login
./enderpinw run
```

カレントディレクトリの`enderpin.toml`を読み込むため、`client/`内では`-C`の指定は不要です。WindowsのPowerShellでは`./enderpinw`を`.\enderpinw.ps1`に置き換えてください。

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

1. 設定・テストを検証し、配信済みのEnderpin 0.1.5（Linux x86_64）を取得。アーカイブと実行ファイルのSHA-256を固定値と照合。
2. 両サーバーとクライアント用プロファイルのlockを検証し、その実行の成果物を保存。
3. 成功したpushのコミットと成果物を使い、GitHubホストのrunnerからAnsibleを実行。
4. 各サーバーの実効入力を前回成功時のSHA-256と比較し、変更のあるサーバーだけFabric、Velocityの順で更新。

差分判定には、そのサーバーのmanifest・lock・共有ファイル、生成した設定・systemd unit、Enderpin実行ファイル、配布処理の実装を含めます。GitのコミットIDは含めないため、クライアントやREADMEだけを変更したpushではサーバーを更新しません。MOTD・メモリ・転送secretなどの変更は、反映先のサーバーを更新します。比較用の記録は本配置の変更前に無効化し、起動確認に成功した後で保存します。失敗後に元の設定へ戻した場合も、次回は再配布します。

更新対象の入力・生成済み設定・実行ファイルは、サーバーごとに1つの非公開アーカイブにまとめて転送し、同じアーカイブを停止前の検証と本配置に使います。ワールド・キャッシュは転送しません。SSHによるホスト・ファイアウォールの確認は毎回行い、APTの一覧更新は1時間キャッシュします。変更のあるサーバーの停止前検証、停止後のワールド全体バックアップ、権限確認、起動確認は維持しています。

初回は比較用の記録がないため両サーバーを更新します。以後、変更のないサーバーは転送・停止・バックアップ・再起動を省きます。Enderpin自体や共通の配布処理を変更した場合は両方が対象になります。比較用の記録は`/srv/enderpin/<名前>/.deployment-sha256`です。配置済みファイルの手動変更やサービス停止の自動修復はこの差分判定に含みません。強制的に同じ入力を再配布する場合は、対象サーバーのこの記録を管理者権限で削除してから実行します。

PRの検証からは配布しません。配布処理は同時に走らず、実行中の更新を新しいpushで中断しません。更新に失敗したインスタンスは停止し、ワールドを自動で巻き戻しません。Actionsの`Deploy Minecraft`で失敗箇所を確認してください。

GitHub Secretsは以下を登録します。接続先はコードと分離し、専用SSH鍵でVPSを経由して自宅に接続します。自宅ルーターのSSHポート開放は不要です。

| Secret | 内容 |
| --- | --- |
| `ENDERPIN_INVENTORY` | 実際の接続先・VPNアドレスを含むinventory |
| `ENDERPIN_SSH_KEY` | Actions専用のSSH秘密鍵 |
| `ENDERPIN_SSH_KNOWN_HOSTS` | 本人確認済みの両ホストのSSH公開ホスト鍵 |
| `ENDERPIN_VAULT` | Ansible Vaultで暗号化した共有転送secret |
| `ENDERPIN_VAULT_PASSWORD` | Vaultの復号パスワード |
| `HACKCLUB_CDN_API_KEY` | Hack Club CDN v4のアップロード用APIキー（`sk_cdn_...`） |
| `ENDERPIN_BACKUP_AGE_RECIPIENT` | バックアップ暗号化用age公開鍵（`age1...`）。復号用秘密鍵は登録しない |

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
chmod 700 .local/enderpin
```

サーバー配布用ツールもEnderpin 0.1.5を使います。CIでソースからのRustビルドは行わず、検証済みの配信バイナリを成果物に保存します。手動配布でも対象コミットの成功したCheck workflowの成果物を使用してください。

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

バックアップは停止した`workspace`全体（ワールド・設定・MOD・ログ・ローカルの権限承認情報）をtar/gzipにし、age公開鍵で暗号化した状態でHack Club CDNの`POST /api/v4/upload`へ送ります。CDNの取得URLは公開されるため、平文を送信しません。APIキーと復号用秘密鍵はゲームのworkspaceに配置しません。

配布前にAPI認証とage公開鍵を検証し、停止後は暗号化・アップロード・応答のサイズ確認まで成功してから既存ファイルを更新します。失敗時は更新を中断して停止状態にし、作成済みの暗号化ファイルは残します。送信中もサーバーは停止するため、バックアップ容量と上り帯域によって停止時間が増えます。APIの容量超過（HTTP 402）も配布失敗として扱います。

各ホストに`/srv/enderpin/<名前>/backups/<名前>-<コミットSHA>-<UTC日時>.tar.gz.age`と、CDNの取得URL・upload ID・暗号化ファイルのSHA-256・サイズを記録した同名の`.json`を保存します。CDNへの再ダウンロードによる完全照合、古い世代の自動削除、定期実行は行いません。CDN側の保存容量を使い切らないよう、APIキーのアカウントで世代を管理してください。

初回は管理するMacなどでageの復号用秘密鍵を生成し、安全な別の場所にも保管してください。復号用秘密鍵を失うと復元できません。以下の秘密鍵はGit管理外の場所に作成します。

```sh
brew install age
umask 077
mkdir -p "$HOME/.config/spnya-mc"
age-keygen -o "$HOME/.config/spnya-mc/backup.agekey"
age-keygen -y "$HOME/.config/spnya-mc/backup.agekey" | gh secret set ENDERPIN_BACKUP_AGE_RECIPIENT -R aomona/spnya-mc
# APIキーは gh secret set HACKCLUB_CDN_API_KEY -R aomona/spnya-mc の非表示入力で登録
```

手動のAnsible配布では、非公開の`deploy/secrets.yml`にも`enderpin_backup_api_key`と`enderpin_backup_age_recipient`を設定してください。暗号化したVaultを使う場合の例は`deploy/ansible/vault.example.yml`にあります。復号用秘密鍵はAnsible、GitHub Secrets、CDNへ渡しません。

復元時は`.json`に記録されたURLから暗号化ファイルを取得し、SHA-256を照合してから管理用端末で復号します。取得URLや復号後の内容を公開ログへ貼らないでください。

```sh
age --decrypt --identity "$HOME/.config/spnya-mc/backup.agekey" \
  --output restored.tar.gz BACKUP.tar.gz.age
tar -tzf restored.tar.gz
```

内容を確認してから、停止した対象サーバーのworkspaceへ復元します。

VPSの公開ゲームポートはTCP 25565です。OCIのSecurity Listでも許可してください。AnsibleはVPSの既存iptablesにゲーム用の許可を追加して永続化し、バックエンド用のnftablesテーブルで自宅のTCP 25566への接続をVPSのVPNアドレスからに限定します。

配布テンプレートとテストは`aomona/enderpin`由来（GPL-3.0-only、`LICENSE`参照）です。
