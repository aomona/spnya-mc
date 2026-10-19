# サーバーへの配布

[READMEへ戻る](../README.md) · [バックアップと復元](backup.md)

通常は設定を`main`へpushすると自動配布されます。手動配布も同じAnsible playbookを使います。更新するサーバーは停止するため、バックアップのアップロード時間も停止時間に含まれます。

## 自動配布の流れ

`main`へpushすると、GitHub Actionsが次の順で処理します。

1. 設定・テストを検証し、配信済みのEnderpin 0.1.5（Linux x86_64）を取得。アーカイブと実行ファイルのSHA-256を固定値と照合。
2. 両サーバーとクライアント用プロファイルのlockを検証し、その実行の成果物を保存。
3. 成功したpushのコミットと成果物を使い、GitHubホストのrunnerからAnsibleを実行。
4. 各サーバーの実効入力を前回成功時のSHA-256と比較し、変更のあるサーバーだけFabric、Velocityの順で更新。

PRの検証からは配布しません。配布処理は同時に走らず、実行中の更新を新しいpushで中断しません。更新に失敗したインスタンスは停止し、ワールドを自動で巻き戻しません。Actionsの`Deploy Minecraft`で失敗箇所を確認してください。

## 初回設定

### 1. 接続先とVPNを用意する

配布先はDebian系LinuxのVPSと自宅サーバーです。この手順はOpenVPNが構成済みであることを前提にしています。

[`deploy/inventory.example.yml`](../deploy/inventory.example.yml)から、Git管理外の`deploy/inventory.yml`を作り、接続先・SSHユーザー・VPNアドレスを設定します。ゲーム設定はplaybookディレクトリ内の`group_vars`と`host_vars`から読み込みます。

```sh
cp deploy/inventory.example.yml deploy/inventory.yml
```

VPSを踏み台に自宅のVPNアドレスへ接続する構成なので、自宅LANの外からも配布できます。既存のMac用ローカル設定では、VPSには1PasswordのSSH agent、自宅には`~/.ssh/enderpin_ed25519`を使用します。SSHのknown_hostsは事前に確認してください。

### 2. 手動配布用のツールを用意する

Macから手動配布する場合は、PythonのvenvとGitHub CLI（`gh`）を使います。以下はリポジトリのルートで実行します。`RUN_ID`は配布したいコミットの成功した「Check configuration and deployment artifact」のrun IDに置き換えてください。

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

### 3. 秘密情報とバックアップを設定する

既存環境では、設定済みの`deploy/secrets.yml`を引き継ぎます。**共有転送secretは毎回生成し直さず、同じ値を使ってください。** inventory・SSH鍵と一緒に非公開の場所へバックアップします。

以下は、新規環境で`deploy/secrets.yml`を初めて作るときだけ実行する例です。既存ファイルがある場合は上書きせず失敗します。

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

手動配布では同じファイルに`enderpin_backup_api_key`と`enderpin_backup_age_recipient`も必要です。[バックアップの初回設定](backup.md#初回設定)でage鍵を用意し、CDN APIキーとage公開鍵を設定してください。バックアップの設定が揃うまで配布は実行できません。

Discord連携には`enderpin_discord_token`と文字列の`enderpin_discord_channel_id`も必要です。設定方法は[Discord連携と参加認証](#discord連携と参加認証)を参照してください。

暗号化Vaultを使う場合の書式は[`deploy/ansible/vault.example.yml`](../deploy/ansible/vault.example.yml)を参照してください。復号用秘密鍵はAnsibleやGitHub Secretsへ渡しません。

### 4. GitHub ActionsのSecretsを登録する

自動配布には、以下をリポジトリのGitHub Secretsに登録します。接続先はコードと分離し、専用SSH鍵でVPSを経由して自宅に接続します。自宅ルーターのSSHポート開放は不要です。

| Secret | 内容 |
| --- | --- |
| `ENDERPIN_INVENTORY` | 実際の接続先・VPNアドレスを含むinventory |
| `ENDERPIN_SSH_KEY` | Actions専用のSSH秘密鍵 |
| `ENDERPIN_SSH_KNOWN_HOSTS` | 本人確認済みの両ホストのSSH公開ホスト鍵 |
| `ENDERPIN_VAULT` | Ansible Vaultで暗号化した共有転送secret・Discord Botトークン・チャンネルID |
| `ENDERPIN_VAULT_PASSWORD` | Vaultの復号パスワード |
| `HACKCLUB_CDN_API_KEY` | Hack Club CDN v4のアップロード用APIキー（`sk_cdn_...`） |
| `ENDERPIN_BACKUP_AGE_RECIPIENT` | バックアップ暗号化用age公開鍵（`age1...`）。復号用秘密鍵は登録しない |

1Passwordの個人用SSH鍵はGitHubへ渡しません。SSHはホスト鍵を必ず検証し、実際のIPは配布ログでもマスクします。追加・変更時は、秘密値をコマンド引数に直書きせず`gh secret set NAME < 非公開ファイル`などで登録してください。

## Discord連携と参加認証

VelocityにDiscord Integration Velocity v1.0.3、FabricにMinecraft 26.3専用のbackend MODを固定しています。Fabric APIは既存lockの0.163.0+26.3を使用し、クライアントへの導入は不要です。

1. Discord Developer PortalでBotを作り、**Message Content Intent**を有効にします。
2. `bot`・`applications.commands`スコープでDiscordサーバーへ招待し、対象テキストチャンネルで「チャンネルを見る」「メッセージを送信」「埋め込みリンク」を許可します。管理者権限は不要です。
3. Git管理外の`deploy/secrets.yml`に`enderpin_discord_token`と`enderpin_discord_channel_id`を設定します。チャンネルIDは引用符で囲んだ文字列にします。自動配布では、同じ値を既存の共有転送secretを保持したVaultへ追加し、GitHub Secret `ENDERPIN_VAULT`を更新します。
4. 通常のAnsible配布を実行します。BotトークンとチャンネルIDが空の場合はプロキシの更新前に停止します。

Ansibleが`plugins/discordintegrationvelocity/config.json`を非公開権限で生成し、Discord連携と`authentication.enabled`を有効にします。登録するまで全プレイヤーが接続時に認証を求められるため、先にBotの招待とチャンネル権限を確認してください。

初回接続時のコードを同じDiscordサーバーで`/認証 code:コード`に入力し、Minecraftへ再接続します。コードは10分・1回限りで、再起動すると失効します。管理者も同じ手順が必要です。

`/認証解除`は本人の登録を解除します。`/認証解除 user:@メンバー`は「サーバー管理」権限を持つ管理者向けで、対象のMinecraft接続も切断します。利用者のDiscord脱退やロール変更では登録が自動解除されません。再登録を防ぐ場合はDiscordの連携設定でコマンドの利用権限も制限してください。

登録情報はプロキシの`workspace/run/server/plugins/discordintegrationvelocity/discord-whitelist.json`に保存され、通常の配布では保持されます。停止後のworkspace全体バックアップにも含まれます。ファイルの破損時はログインを拒否するため、停止してバックアップと比較してから修復してください。Discord停止中も登録済みのプレイヤーは接続できますが、新規登録はできません。

## 手動で配布する

リポジトリのルートから実行します。配布したい設定をコミットし、そのコミットに対応する検証済み成果物を用意してから進めてください。Fabric、Velocityの順で更新し、各サービスを停止してバックアップした後、既存ワールドを保持したまま設定を反映します。

Minecraft EULAへの同意は[`group_vars/minecraft.yml`](../deploy/ansible/group_vars/minecraft.yml)で設定済みです。新規環境では同意内容を確認してください。

```sh
revision=$(git rev-parse HEAD)
cd deploy/ansible
../../.venv/bin/ansible-playbook -i ../inventory.yml deploy.yml \
  -e @../secrets.yml -e "enderpin_revision=$revision"
```

サービス名は`enderpin-survival`と`enderpin-proxy`です。実行ファイル・キャッシュ・ワールドは各ホストの`/srv/enderpin/<名前>/`、更新前のバックアップは同ディレクトリの`backups/`に保存します。復元時は停止してから内容を確認し、ワールドを自動で巻き戻さないでください。自宅には`systemd-timesyncd`も導入し、認証・署名付きチャットに必要な時刻同期を維持します。

## どの変更で再起動するか

| 変更内容 | 更新対象 |
| --- | --- |
| README・クライアントだけ | なし |
| サーバーのmanifest・lock・共有ファイル | 変更したサーバー |
| メモリ・MOTDなどの生成設定 | 反映先のサーバー |
| 共有転送secret | 両サーバー |
| Enderpin実行ファイル・共通の配布処理 | 両サーバー |

差分判定には、そのサーバーのmanifest・lock・共有ファイル、生成した設定・systemd unit、Enderpin実行ファイル、配布処理の実装を含めます。GitのコミットIDは含めないため、クライアントやREADMEだけを変更したpushではサーバーを更新しません。MOTD・メモリ・転送secretなどの変更は、反映先のサーバーを更新します。比較用の記録は本配置の変更前に無効化し、起動確認に成功した後で保存します。失敗後に元の設定へ戻した場合も、次回は再配布します。

更新対象の入力・生成済み設定・実行ファイルは、サーバーごとに1つの非公開アーカイブにまとめて転送し、同じアーカイブを停止前の検証と本配置に使います。ワールド・キャッシュは転送しません。SSHによるホスト・ファイアウォールの確認は毎回行い、APTの一覧更新は1時間キャッシュします。変更のあるサーバーの停止前検証、停止後のワールド全体バックアップ、権限確認、起動確認は維持しています。

初回は比較用の記録がないため両サーバーを更新します。以後、変更のないサーバーは転送・停止・バックアップ・再起動を省きます。Enderpin自体や共通の配布処理を変更した場合は両方が対象になります。比較用の記録は`/srv/enderpin/<名前>/.deployment-sha256`です。配置済みファイルの手動変更やサービス停止の自動修復はこの差分判定に含みません。強制的に同じ入力を再配布する場合は、対象サーバーのこの記録を管理者権限で削除してから実行します。

## ネットワーク

VPNのMTUは1400、`mssfix`は1360に設定し、VPN越しのSSHで大きな鍵交換パケットが途切れる問題を避けています。

VPSの公開ゲームポートはTCP 25565です。OCIのSecurity Listでも許可してください。AnsibleはVPSの既存iptablesにゲーム用の許可を追加して永続化し、バックエンド用のnftablesテーブルで自宅のTCP 25566への接続をVPSのVPNアドレスからに限定します。
