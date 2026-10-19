# Spnya Minecraft

Minecraft **26.3** のサーバー設定と、任意で使えるクライアントのプロファイルです。EnderpinでバージョンとMODを固定し、Ansibleでサーバーへ配布します。

```text
プレイヤー → 公開ゲームアドレス:25565/TCP → Velocity（VPS）
                                             ↓ OpenVPN
                                          Fabric（自宅）
```

Velocityでオンライン認証を行い、FabricはVPN内だけで接続を受け付けます。自宅ルーターのゲーム用・SSH用ポート開放は不要です。

| やりたいこと | 読む場所 |
| --- | --- |
| サーバーに参加する | [参加方法](#参加方法) |
| 軽量クライアントを起動する | [軽量クライアントの起動](#軽量クライアントの起動任意) |
| MOD・メモリ・MOTDなどを変える | [設定の変更](#設定の変更) |
| サーバーへ配布する・Actionsを設定する | [配布ガイド](docs/deployment.md) |
| バックアップを設定する・復元する | [バックアップと復元](docs/backup.md) |

## 参加方法

**Minecraft 26.3のバニラクライアントで参加できます。** MODをクライアントに入れる必要はありません。接続先は管理者が案内する公開ゲームアドレスです。

初回は接続画面に表示された認証コードを、案内されたDiscordサーバーで `/認証 code:表示されたコード` に入力し、Minecraftへ再接続してください。コードは10分間有効で、1回だけ使用できます。DiscordとMinecraftのアカウントは1対1で登録します。

描画なども軽くしたい場合は、次のクライアントを任意で使えます。

## 軽量クライアントの起動（任意）

PrismLauncherの`26.3`インスタンスに合わせた、38個のMODとシェーダー・リソースパック各1個のプロファイルを`client/`に用意しています。日本語表示・キー設定・描画距離16チャンクなどのゲーム設定と各MOD設定も同期します。[構成と設定の詳細](docs/client.md)を確認してください。同梱のwrapperがEnderpin 0.1.5を取得してSHA-256を検証するので、Enderpinの事前インストールは不要です。

リポジトリを取得済みの場合は、`client/`への移動から実行してください。

```sh
git clone https://github.com/aomona/spnya-mc.git
cd spnya-mc/client
./enderpinw sync --target client --locked
# 初回のみMicrosoftアカウントへログイン
./enderpinw login
./enderpinw run
```

WindowsのPowerShellでは、`client/`内で次のように実行します。

```powershell
.\enderpinw.ps1 sync --target client --locked
.\enderpinw.ps1 login
.\enderpinw.ps1 run
```

起動時に表示される権限を確認して承認してください。ワールド・設定・実行時データは`client/run/client/`に保存され、Git管理外です。各コマンドはカレントディレクトリの`enderpin.toml`を読むため、`client/`内では`-C`の指定は不要です。

## サーバー構成

| 項目 | 設定 |
| --- | --- |
| Minecraft | 26.3 |
| VPS（`proxy`） | Velocity 4.2.0 build 30 / メモリ256MiB / 公開TCP 25565 |
| 自宅（`survival`） | Fabric Loader 0.19.5 / メモリ2048MiB / VPN内TCP 25566 |
| 転送認証 | FabricProxy-Lite v2.12.0 / Velocity modern forwarding |
| Discord連携・参加認証 | Discord Integration Velocity v1.0.3 / Fabric backend v1.0.3（26.3用） |
| 最大人数 | 10人 |
| サーバーの軽量化MOD | Lithium 0.26.2、FerriteCore 9.0.0 |
| サーバーの技術支援MOD | Carpet 26.3 |
| クライアントの軽量化MOD | Sodium 0.9.3-beta.1、Lithium 0.26.2、FerriteCore 9.0.0など（[全構成](docs/client.md)） |

Lithiumはゲーム処理のCPU負荷、FerriteCoreはメモリ使用量、Sodiumはクライアントの描画負荷を軽減します。

Carpetはsurvivalサーバーに導入し、ルールは初期設定のままにしています。

バニラの挙動を優先し、サーバーは`files/`内の`lithium.properties`で`minimal_nonvanilla`、`experimental`、`ai.poi.fast_portals`、`world.explosions.block_raycast.skip_air`を無効化しています。サーバーのポータル探索時のチャンク読み込みや、爆発で生成されたブロックの破壊もバニラの処理を使います。クライアントのLithium設定はPrismLauncher側と同じ初期設定です。

MOD更新時はLithiumの[該当バージョンの最適化一覧](https://github.com/CaffeineMC/lithium/blob/mc26.3-0.26.2/lithium-fabric-mixin-config.md)で挙動差のある項目を再確認してください。実際のワールドでの性能や装置の動作は別途確認が必要です。

## 設定の変更

| 変更したいもの | 編集するファイル |
| --- | --- |
| Velocityのバージョン | [`servers/proxy/enderpin.toml`](servers/proxy/enderpin.toml) |
| Fabric・サーバーMOD | [`servers/survival/enderpin.toml`](servers/survival/enderpin.toml) |
| クライアントMOD | [`client/enderpin.toml`](client/enderpin.toml) |
| クライアントのゲーム・MOD設定 | `client/files/client/options.txt`、`client/files/client/config/` |
| Fabricのメモリ・MOTD・人数・描画距離 | [`deploy/ansible/host_vars/home.yml`](deploy/ansible/host_vars/home.yml) |
| Velocityのメモリ・MOTD | [`deploy/ansible/host_vars/edge.yml`](deploy/ansible/host_vars/edge.yml) |
| 共通の配置先・接続するサーバー名 | [`deploy/ansible/group_vars/minecraft.yml`](deploy/ansible/group_vars/minecraft.yml) |
| IP・SSHユーザー・接続方法 | `deploy/inventory.yml`（Git管理外） |
| 追加のサーバー共有設定 | `servers/<名前>/files/server/` |

`enderpin.toml`でバージョンやMODを変更したら、対象の`enderpin.lock`も更新して一緒にコミットします。以下はリポジトリのルートで実行する例です。

```sh
# 変更した対象だけ実行する
./client/enderpinw -C servers/survival sync --target server
./client/enderpinw -C servers/proxy sync --target server
./client/enderpinw -C client sync --target client
```

`server.properties`、`velocity.toml`、転送認証設定、Discord連携の`config.json`はAnsibleが生成します。`files/server/`に同じファイルを重ねて置かないでください。Botの設定と参加認証の管理は[配布ガイド](docs/deployment.md#discord連携と参加認証)を参照してください。

サーバー設定を`main`へpushすると、検証に成功したコミットが自動で配布されます。**READMEやクライアントだけの変更では、サーバーを停止・再起動しません。** 配布対象の判定や手動実行は[配布ガイド](docs/deployment.md)を参照してください。

実際のIP、ワールド、プレイヤーデータ、実行時ファイル、SSH秘密鍵、転送用secretはGitへ入れません。公開するinventoryの例とテストには説明用アドレスだけを使用し、以前のコミット履歴は保持しています。

## ライセンス

配布テンプレートとテストは`aomona/enderpin`由来です。GPL-3.0-only（[`LICENSE`](LICENSE)）。
