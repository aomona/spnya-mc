# クライアント構成

2026年10月19日のPrismLauncher `26.3`インスタンスを基にした構成です。Minecraft 26.3 / Fabric Loader 0.19.5を使用します。

`enderpin.toml`のパッケージはModrinthのバージョンID、または特定バージョンの公開配布URLで固定しています。38個のMOD・シェーダー1個・リソースパック1個を、元のファイルのSHA-512で照合して選んでいます。JARやパック本体をGitへ入れず、Enderpinが配布元から取得し、`enderpin.lock`のハッシュとサイズを検証します。Sodium、Reese's Sodium Options、TweakerMore、Simple Voice Chatのベータ版も元の構成に合わせています。

IrisとZoomifyは同じファイルのModrinth CDN URLを指定しています。Iris 1.11.7のModrinth依存情報はSodium 0.9.2を要求しますが、実際のJARの`fabric.mod.json`は`0.9.x`を許可し、元の構成は0.9.3-beta.1です。Zoomify 2.17.0のModrinth依存情報には旧YACLプロジェクトも含まれていますが、JARが要求する`yet_another_config_lib_v3`は同梱構成のYACL 3.9.7で満たします。この2個をURL指定にすることで元のファイル構成を維持し、必要なSodium・YACL・Fabric API・Kotlinは明示的なパッケージとして固定しています。

## 起動と同期

### mrpackを使う

[GitHub Releases](https://github.com/aomona/spnya-mc/releases)から`spnya-client.mrpack`をダウンロードし、Prism Launcherなどの対応ランチャーでインポートできます。Minecraft / Fabricのバージョンと共有設定も引き継ぎます。Enderpinの`jvm_args`はmrpackの対象外のため、ZGCを使う場合はランチャー側で`-XX:+UseZGC`を設定してください。

MOD・シェーダー・リソースパック本体は同梱せず、`enderpin.lock`に記録した元の公開配布URLからランチャーが取得します。生成時に全ファイルのサイズ・SHA-512を照合し、mrpackにSHA-1とSHA-512を記録します。同梱するのはGit管理中の`client/files/config/`と`options.txt`だけです。

`main`へのpush後、既存の検証CIが成功すると、そのコミットのパックとSHA-256チェックサムを`client-<コミットSHA>`のGitHub Releaseへ自動公開します。PRでは検証とActions artifactの生成だけを行います。同じコミットのReleaseが存在する場合は再公開しません。

手元で生成する場合はPython 3.11以降を使い、リポジトリのルートで実行します。配布元へのネットワーク接続が必要です。

```sh
python3 scripts/test_mrpack.py
python3 scripts/build-mrpack.py --version local --output .local/spnya-client.mrpack
```

### Enderpinを使う

WrapperはEnderpin 0.2.0に固定しています。0.1.7までのWindows向け修正（通常サンドボックス起動、マウスの画面内への閉じ込め、PowerShellで移動した作業フォルダーの引き継ぎ）に加え、`enderpin.toml`の`jvm_args`に対応しています。クライアントは`jvm_args = ["-XX:+UseZGC"]`でZGCを使って起動します。`jvm_args`は権限と同じく承認の対象のため、更新後の初回`run`では権限の再承認を求められます。format 3から更新する場合は[移行手順](migration.md)を先に実施してください。

リポジトリのルートで実行します。Windowsでは次のコマンドです。macOS / Linuxでは`.\enderpinw.ps1`を`./enderpinw`へ置き換えてください。

```powershell
.\enderpinw.ps1 sync --target client --locked
.\enderpinw.ps1 login
.\enderpinw.ps1 run
```

ゲームの保存先はこのリポジトリの`client/run/`です。PrismLauncherの元インスタンスを書き換えません。初回起動時はEnderpinの権限表示を確認してください。

## 共有する設定

`client/files/options.txt`と`client/files/config/`をゲームディレクトリへ同期します。日本語表示、キー操作、音量、描画距離16・シミュレーション距離12、最大120 FPS、各MODの表示・操作設定を含みます。

LithiumもPrism側の設定に合わせ、以前のクライアント独自の最適化無効化指定を外しています。サーバー側のLithium設定は従来どおりです。

Complementary Reimagined r5.9.3とLow on Fireも配置します。元の`options.txt`は`resourcePacks:["vanilla"]`、Irisは`shaderPack=`なので、追加パックは選択されていない状態です。導入と有効化は別で、元の選択状態を引き継ぎます。

Litematica / TweakerMoreの無効になっているカスタムschematicsディレクトリは、PC固有の絶対パスから`schematics`へ置き換えています。有効・無効の設定は変えていません。

次のデータは共有対象に含めません。

- ワールド、schematics本体、リプレイ、スクリーンショット、ログ、サーバー一覧・接続履歴
- Xaeroの地図・ウェイポイント、Distant HorizonsのLODデータ、Litematicaのサーバー別選択状態
- Sodiumの端末フィンガープリント、バックアップ、生成済み翻訳キャッシュ、プレイヤー別音量データ
- PrismLauncherのアカウント情報・ランチャー固有の設定

既存のEnderpin環境で共有設定を自分でも変更している場合、`sync`は競合を検出して停止します。残す設定を`client/files/`へ反映してから再同期してください。共有元へ戻す場合だけ、次を使用します。管理対象のローカル変更を破棄します。

```powershell
.\enderpinw.ps1 restore --target client
```

初めて共有管理に入る同名設定が`client/run/`にある場合は、バックアップして退避してから同期してください。

## MODの更新

バージョンを変更するときは`enderpin.toml`の固定IDを編集し、`sync --target client`で更新した`enderpin.lock`も一緒にコミットします。MOD設定は`client/files/`で編集します。ゲーム内で変更した内容を共有する場合も、この場所へコピーし直してください。
