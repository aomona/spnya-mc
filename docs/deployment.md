# サーバーへの配布

[READMEへ戻る](../README.md) · [バックアップと復元](backup.md)

通常は設定を`main`へpushすると自動配布されます。手動配布も同じAnsible playbookを使います。更新するサーバーは停止するため、バックアップのアップロード時間も停止時間に含まれます。

## 自動配布の流れ

`main`へpushすると、GitHub Actionsが次の順で処理します。

1. 設定・テストを検証し、Enderpin 0.1.7の固定コミットへNetherNet向け修正を適用してLinux x86_64用にビルド。実行ファイルのSHA-256を成果物に記録。
2. 全サーバー（creativeを含む）とクライアント用プロファイルのlockを検証し、その実行の成果物を保存。
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

サーバー配布用ツールはEnderpin 0.1.7の固定コミット`d592f3ad32e1df1e74f7dee86b19dc17123436a6`からビルドします。[修正パッチ](../deploy/patches/enderpin-netlink-route.patch)は、通信許可時にLinuxの`NETLINK_ROUTE`を許可し、GeyserのICEインターフェース取得失敗（`getifaddrs failed, errno=97`）を解消します。ほかのNetlinkプロトコルや通信禁止設定は維持します。修正済みの公式リリースへ移行する際はパッチを外してください。手動配布でも対象コミットの成功したCheck workflowの成果物を使用してください。

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

VelocityにDiscord Integration Velocity 1.0.3-spnya.1、Fabricにv1.0.3のMinecraft 26.3専用backend MODを固定しています。Fabric APIは既存lockの0.163.0+26.3を使用し、クライアントへの導入は不要です。

プロキシ用JARは`servers/proxy/files/server/plugins/`に同梱しています。上流v1.0.3のソースを固定し、`patches/discord-integration-floodgate.patch`でFloodgateの認証済みUUIDと`.`付き名の保存に対応させています。Javaの`online-mode = true`と、全プレイヤーのDiscord登録は維持します。Floodgate/APIが利用できない場合、オフライン接続は拒否します。名前の接頭辞だけでは許可しません。

JDK 25を使用して、次のコマンドで認証の回帰テストと再ビルドを実行できます。CIは`--check`で同梱JARとのバイト一致も検証します。ソース・Floodgateのビルド・Gradle配布物のハッシュは固定しています。

```sh
python3 scripts/build-discord-plugin.py --check
# パッチ更新後、同梱JARを再生成する場合
python3 scripts/build-discord-plugin.py
```

1. Discord Developer PortalでBotを作り、**Message Content Intent**を有効にします。
2. `bot`・`applications.commands`スコープでDiscordサーバーへ招待し、対象テキストチャンネルで「チャンネルを見る」「メッセージを送信」「埋め込みリンク」を許可します。管理者権限は不要です。
3. Git管理外の`deploy/secrets.yml`に`enderpin_discord_token`と`enderpin_discord_channel_id`を設定します。チャンネルIDは引用符で囲んだ文字列にします。自動配布では、同じ値を既存の共有転送secretを保持したVaultへ追加し、GitHub Secret `ENDERPIN_VAULT`を更新します。
4. 通常のAnsible配布を実行します。BotトークンとチャンネルIDが空の場合はプロキシの更新前に停止します。

Ansibleが`plugins/discordintegrationvelocity/config.json`を非公開権限で生成し、Discord連携と`authentication.enabled`を有効にします。登録するまで全プレイヤーが接続時に認証を求められるため、先にBotの招待とチャンネル権限を確認してください。

初回接続時のコードを同じDiscordサーバーで`/認証 code:コード`に入力し、Minecraftへ再接続します。コードは10分・1回限りで、再起動すると失効します。管理者も同じ手順が必要です。

`/認証解除`は本人の登録を解除します。`/認証解除 user:@メンバー`は「サーバー管理」権限を持つ管理者向けで、対象のMinecraft接続も切断します。利用者のDiscord脱退やロール変更では登録が自動解除されません。再登録を防ぐ場合はDiscordの連携設定でコマンドの利用権限も制限してください。

登録情報はプロキシの`workspace/run/server/plugins/discordintegrationvelocity/discord-whitelist.json`に保存され、通常の配布では保持されます。停止後のworkspace全体バックアップにも含まれます。ファイルの破損時はログインを拒否するため、停止してバックアップと比較してから修復してください。Discord停止中も登録済みのプレイヤーは接続できますが、新規登録はできません。

### Distant Horizonsの事前生成

survivalにはクライアントと同じDistant Horizons 3.3.4（Minecraft 26.3用）を導入します。通常運用の生成負荷は`files/server/config/DistantHorizons.toml`で1スレッド・稼働率50%に抑えます。

Macで事前生成する場合は本番を停止してバックアップし、停止中のワールド全体を生成用workspaceの`run/server/world`へコピーします。転送が終わるまで本番を停止しておきます。同じMinecraft・Fabric・ワールド生成に関係するMODを使い、認証用MODや本番の秘密情報は生成環境に持ち込みません。今回の生成環境はMinecraft 26.3、Fabric 0.19.5、DH 3.3.4、Fabric API 0.163.0、Lithium・FerriteCore・Carpet・Jadeです。Jadeのゲームルールが保存されているため、生成環境にもJadeを含めます。

生成用サーバーは`127.0.0.1`にbindし、`pause-when-empty-seconds=-1`を設定します。DHは`common.worldGenerator.generatorPlan="CHUNKS_ONLY"`、`chunkGeneratorMode="INTERNAL_SERVER"`として、通常チャンクと遠景データを両方保存します。Macではメモリ8GiB、DHの6スレッド・稼働率100%で開始し、空き容量が3GiBを下回ったら中断します。起動直後はDHの生成キューが未作成のことがあるため、起動完了から数秒待って、次を生成用サーバーのコンソールで実行します。

```text
dh pregen start minecraft:overworld <spawnX> <spawnZ> 128
dh pregen status
```

中心にはコピーした`level.dat`のスポーン座標を指定します。半径の単位はチャンクなので2,048ブロックは`128`です。DHは四角い範囲を生成し、区画境界に合わせて端を丸めます。今回の実行ではスパイラルの端に不足が残らないよう`132`へ広げ、中心から±2,048ブロックの全チャンクを検査します。ネザーとエンドの事前生成は開始しません。中断は`dh pregen stop`で、再起動後の自動再開はありません。同じ範囲で再実行すると生成済みの遠景データを利用します。

`Pregen is complete`を確認したら生成用サーバーを正常終了し、通常チャンクとDHのSQLiteデータベースを検査します。ワールド全体を本番の別ディレクトリへ転送し、ハッシュを照合してから元ワールドを退避して入れ替えます。本番がコピー後に変更されていたら入れ替えを中止します。所有者を本番サービスのユーザーに戻し、同じDHを導入して起動を確認してください。遠景表示にはJava版クライアント側にもDHが必要です。

Enderpin 0.1.5は実行中の共有設定ファイルも権限承認のハッシュに含めます。Minecraftが`server.properties`の日時コメントを起動ごとに更新し、DHも初回起動時に設定を補完するため、手動で再起動するときはサービスを停止し、`permissions show --target server --json`で設定以外の権限が変わっていないことを確認して、表示されたfingerprintを`permissions approve <fingerprint> --target server --no-interactive`で承認してから起動します。コマンドは本番サービスのユーザーで、同じworkspace・cacheを指定して実行してください。権限確認やsandboxは無効化しません。

### 統合版とボイスチャット

Geyserはプロキシ上で`java.auth-type: floodgate`、`bedrock.transport: nethernet`を使用します。FloodgateのJavaアカウントへのリンクは無効にし、統合版はXboxのXUIDに基づく独立したUUIDとしてDiscordへ登録します。Discordアカウントとの1対1制限は共通なので、Java版で登録済みのDiscordアカウントを統合版へ切り替える場合は先に`/認証解除`が必要です。プレイヤーデータの統合・移行は行いません。

OCIの公開IPへのNATに対応するため、Geyserの案内先はVPSの`ansible_host`を配布時にIPv4へ解決して設定します。この接続先はVPS自身の公開IPを指す必要があります。DNSでIPを変更した場合は設定を再配布してください。

Floodgateの鍵は初回起動時にプロキシの`workspace/run/server/plugins/floodgate/`へ生成され、同じプロキシのGeyserが読み込みます。鍵をGitやクライアントへ配布しないでください。通常の配布では生成済み鍵を保持し、workspaceの暗号化バックアップにも含めます。バックエンドへのFloodgateデータ転送は無効です。

Simple Voice ChatはプロキシとFabricの両方へ入れます。両JARの音声プロトコルは20で一致しています。FabricはVPNアドレスのUDP 24454、プロキシは公開UDP 24454で待ち受け、プロキシがバックエンドを自動検出します。`voice_host`は空にして、プレイヤーが使ったゲームアドレスを使用します。自宅の音声ポートはVPSのVPNアドレスからだけ許可します。通話を必須にはしないため、バニラ・統合版も参加できます。

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
| `enderpin_creative_address`の追加・削除 | プロキシ |
| `servers/creative/`・`deploy/box/` | なし（boxで手動更新） |

差分判定には、そのサーバーのmanifest・lock・共有ファイル、生成した設定・systemd unit、Enderpin実行ファイル、配布処理の実装を含めます。GitのコミットIDは含めないため、クライアントやREADMEだけを変更したpushではサーバーを更新しません。MOTD・メモリ・転送secretなどの変更は、反映先のサーバーを更新します。比較用の記録は本配置の変更前に無効化し、起動確認に成功した後で保存します。失敗後に元の設定へ戻した場合も、次回は再配布します。

更新対象の入力・生成済み設定・実行ファイルは、サーバーごとに1つの非公開アーカイブにまとめて転送し、同じアーカイブを停止前の検証と本配置に使います。ワールド・キャッシュは転送しません。SSHによるホスト・ファイアウォールの確認は毎回行い、APTの一覧更新は1時間キャッシュします。変更のあるサーバーの停止前検証、停止後のワールド全体バックアップ、権限確認、起動確認は維持しています。

初回は比較用の記録がないため両サーバーを更新します。以後、変更のないサーバーは転送・停止・バックアップ・再起動を省きます。Enderpin自体や共通の配布処理を変更した場合は両方が対象になります。比較用の記録は`/srv/enderpin/<名前>/.deployment-sha256`です。配置済みファイルの手動変更やサービス停止の自動修復はこの差分判定に含みません。強制的に同じ入力を再配布する場合は、対象サーバーのこの記録を管理者権限で削除してから実行します。

## creativeサーバー（別マシン）

`creative`はsurvivalと同じMOD・設定（`servers/creative/`）で、ゲームモードだけクリエイティブに固定したFabricサーバーです。自宅とは別のLinuxマシン（以下、box）で動かします。プレイヤーは`/server creative`で移動し、最初の接続先（`try`）はsurvivalのままです。

boxはsystemdがなく、外部への通信はHTTP CONNECTプロキシ経由だけなので、ActionsとAnsibleの配布対象にしません。代わりに次の構成にします。

- VPSに既存のUDPとは別のOpenVPN（`openvpn-server@enderpin-tcp`、既定TCP 443）を追加します。CA・tls-crypt鍵・gateway証明書は既存のものを共用し、サブネットとccdは別にします。
- boxは`proto tcp-client`と`http-proxy`でこのTCP VPNへ接続します。証明書名と固定アドレスは`enderpin_vpn_tcp_clients`だけで許可し（`ccd-exclusive`）、UDP側の証明書ではTCP側に、TCP側の証明書ではUDP側に接続できません。
- Velocityは`enderpin_creative_address`を設定したときだけ`creative`をバックエンドに追加します。未設定なら生成される設定は従来と同じです。
- boxのゲーム用ファイルは、[`deploy/box/creative.py`](../deploy/box/creative.py)がAnsibleと同じテンプレートから生成します。`server.properties`、FabricProxy-Lite、ボイスチャット、nftablesの内容はsurvivalと同じ形式です。

### inventoryに追加する変数

Git管理外の`deploy/inventory.yml`の`all.vars`に追加します。TCP VPNのサブネットは既存のVPN・boxのLANと重ならない/29などを選び、最初のアドレスをVPS側にします。以下は説明用アドレスです。

```yaml
enderpin_vpn_tcp:
  port: 443                      # VPSで公開するTCPポート。boxのプロキシが通すポートにする
  network: 198.51.100.0/29
  server_address: 198.51.100.1   # VPSのTCP VPNアドレス。boxのファイアウォールはここからだけ許可する
enderpin_vpn_tcp_clients:
  box: 198.51.100.2              # 証明書名と固定アドレス。Ansibleのホストではない
enderpin_creative_address: 198.51.100.2
```

`enderpin_creative_address`は`enderpin_vpn_tcp_clients.box`と同じ値にします。TCP VPNの証明書名は既存の`enderpin_vpn_clients`と別の名前にしてください。

### Macで行う手順

1. パッチを適用し、`deploy/inventory.yml`へ上記の変数を追加します。
2. CA鍵のあるMacで`vpn.yml`を実行します。既存の証明書は再発行せず、`box`の鍵と証明書だけを`enderpin_pki_directory`に作り、VPSにTCPのOpenVPNを追加します。VPSのiptablesにTCPポートの許可を追加し、`netfilter-persistent save`で永続化します。既存のUDPのOpenVPNは、gatewayの鍵などが変わらない限り再起動しません。

   ```sh
   cd deploy/ansible
   ../../.venv/bin/ansible-playbook -i ../inventory.yml vpn.yml
   ```

3. **OCIのSecurity ListまたはNSGで、VPSのTCP 443（`enderpin_vpn_tcp.port`）のIngressを許可します。** AnsibleはOCI側のルールを変更しません。
4. `gh secret set ENDERPIN_INVENTORY -R aomona/spnya-mc < deploy/inventory.yml`でSecretを更新し、`main`へpushします。Velocityの設定が変わるため、プロキシだけが再起動します。boxが起動するまでは`/server creative`が接続エラーになるだけで、survivalには影響しません。
5. 次のファイル・値を、暗号化された経路でboxへ渡します。CA鍵（`ca.key`）、gatewayの鍵、自宅の鍵は渡しません。

| Mac側 | boxでの配置先（rootが所有、0600） |
| --- | --- |
| `enderpin_pki_directory/ca.crt` | `/etc/openvpn/client/enderpin-tcp/ca.crt` |
| `enderpin_pki_directory/box.crt` | `/etc/openvpn/client/enderpin-tcp/client.crt` |
| `enderpin_pki_directory/box.key` | `/etc/openvpn/client/enderpin-tcp/client.key` |
| `enderpin_pki_directory/tls-crypt.key` | `/etc/openvpn/client/enderpin-tcp/tls-crypt.key` |
| `deploy/secrets.yml`の`enderpin_forwarding_secret`の値 | `/etc/enderpin/forwarding.secret` |

共有転送secretは新しく生成せず、既存の値を使います。ディレクトリ`/etc/openvpn/client/enderpin-tcp`と`/etc/enderpin`は0700にします。

### boxで行う手順

boxでは`openvpn`・`nftables`・`bubblewrap`が必要です。APTもプロキシ経由になるため、HTTPSのミラーを指定します。リポジトリ・venv・Enderpinの取得も`https_proxy`を設定して行います。Enderpinの実行ファイルはCIと同じSHA-256で照合します。

```sh
export https_proxy=http://127.0.0.1:8791
sudo mkdir -p /etc/apt/enderpin-https
sed 's#http://deb.debian.org#https://deb.debian.org#' /etc/apt/sources.list.d/debian.sources \
  | sudo tee /etc/apt/enderpin-https/debian.sources > /dev/null
apt_options='-o Acquire::https::Proxy=http://127.0.0.1:8791 -o Dir::Etc::SourceParts=/etc/apt/enderpin-https'
sudo apt-get $apt_options update && sudo apt-get $apt_options install -y openvpn nftables bubblewrap
git clone https://github.com/aomona/spnya-mc.git && cd spnya-mc
python3 -m venv .venv && .venv/bin/pip install -r deploy/ansible/requirements.txt
mkdir -p .local
curl --fail --location -o .local/enderpin.tgz \
  https://github.com/aomona/enderpin/releases/download/v0.1.7/enderpin-linux-x64.tgz
echo 'f4871b1ddab68269bf4ada2bcdf2bfd2488f6176c7cd160d31fa671bbbc4ea97  .local/enderpin.tgz' | sha256sum -c -
tar -xzf .local/enderpin.tgz -C .local --strip-components=2 package/bin/enderpin
chmod 700 .local/enderpin   # prepareが実行ファイルのSHA-256も照合する
cp deploy/box/local.example.yml deploy/box/local.yml   # 実際のアドレスへ書き換える
.venv/bin/python deploy/box/creative.py check
```

`deploy/box/local.yml`はGit管理外です。`creative_address`と`proxy_address`には、inventoryの`enderpin_vpn_tcp_clients.box`と`enderpin_vpn_tcp.server_address`を設定します。

続けてrootで、VPN・ファイアウォール・workspaceの準備・起動の順に実行します。

```sh
sudo .venv/bin/python deploy/box/creative.py vpn-up     # openvpnをバックグラウンドで起動し、固定アドレスを確認
sudo .venv/bin/python deploy/box/creative.py firewall   # nftablesのinet enderpinテーブルを適用
sudo .venv/bin/python deploy/box/creative.py prepare    # 停止中にlockを検証し、配置して権限を承認
sudo .venv/bin/python deploy/box/creative.py start      # ep_creativeで起動し、VPNアドレスでの待ち受けを確認
```

`prepare`はAnsibleの`apply.yml`と同じ順で、専用アカウント`ep_creative`と`/srv/enderpin/creative/`を作り、lockのダウンロードを検証してから既存workspaceをバックアップし、設定を置き換えます。外部フォルダーの権限は拒否します。Enderpinはプロキシの環境変数を使わず、プライベートアドレスへのダウンロードも拒否するため、`prepare`は専用のnetwork namespace内でダウンロード先だけをCONNECTプロキシへ中継します。TLSの検証とlockのSHA-512照合はそのまま行います。

停止は`stop`、状態確認は`status`、VPNの停止は`vpn-down`です。boxにはsystemdがないため、再起動後は`vpn-up`・`firewall`・`start`を手動で実行します。`start`は、MinecraftやDistant Horizonsが書き換えた共有設定だけが変わっている場合に限り権限を再承認し、それ以外の変化があれば起動しません。コンソール出力は`/srv/enderpin/creative/console.log`、OpenVPNのログは`/var/log/enderpin-tcp-openvpn.log`です。

### creativeの更新とバックアップ

Actionsはboxを更新しません。`servers/creative/`・`deploy/box/`・共有転送secretを変更したら、boxで`git pull`の後に`stop`・`prepare`・`start`を実行します。survivalのMODを更新したときは、creativeも同じ版にそろえてください。

`prepare`のバックアップは`/srv/enderpin/creative/backups/`に置く暗号化していないローカルのtar/gzipだけで、CDNへは送りません。必要に応じて安全な場所へ退避してください。

### boxのネットワーク

boxのTCP 25566とUDP 24454は、`firewall`で`epvpn`上のVPSのTCP VPNアドレス（`proxy_address`）からだけ許可し、それ以外は破棄します。ゲームとボイスチャットはboxのVPNアドレスだけで待ち受けます。TCP VPNは`route-nopull`で経路を受け取らず、`client-to-client`も無効なので、boxから自宅のVPNアドレスへは到達できません。

TCPの上でVPNを通すため、UDPのVPNより遅延や揺らぎが大きくなります。ボイスチャットはVelocityがcreativeを自動検出し、boxのUDP 24454へ中継します。Discord連携は`syncedServers`にcreativeも自動で含まれ、backend MODもsurvivalと同じです。

## ネットワーク

VPNのMTUは1400、`mssfix`は1360に設定し、VPN越しのSSHで大きな鍵交換パケットが途切れる問題を避けています。

VPSの公開ゲームポートは次のとおりです。**OCIのSecurity ListまたはNSGでもそれぞれ許可してください。** Ansibleはホストの既存iptablesへ許可を追加して永続化しますが、OCI側のルールは変更しません。

| 用途 | VPSで公開するポート |
| --- | --- |
| Java版 | TCP 25565 |
| Simple Voice Chat | UDP 24454 |
| 統合版（NetherNet） | TCP・UDP 19132 |
| creative用のOpenVPN（任意、`vpn.yml`が許可） | TCP 443（`enderpin_vpn_tcp.port`） |

自宅のTCP 25566とUDP 24454は、バックエンド用nftablesテーブルでVPSのVPNアドレスからだけ許可します。自宅ルーターへの追加のポート開放は不要です。NetherNetの接続先を初めて追加するときは、統合版側でサーバーへの接続確認が表示される場合があります。26.40以前の旧接続方式用のポートは公開していません。
