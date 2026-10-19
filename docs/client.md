# クライアント構成

2026年10月19日のPrismLauncher `26.3`インスタンスを基にした構成です。Minecraft 26.3 / Fabric Loader 0.19.5を使用します。

`client/enderpin.toml`のパッケージはModrinthのバージョンID、または特定バージョンの公開配布URLで固定しています。38個のMOD・シェーダー1個・リソースパック1個を、元のファイルのSHA-512で照合して選んでいます。JARやパック本体をGitへ入れず、Enderpinが配布元から取得し、`enderpin.lock`のハッシュとサイズを検証します。Sodium、Reese's Sodium Options、TweakerMore、Simple Voice Chatのベータ版も元の構成に合わせています。

IrisとZoomifyは同じファイルのModrinth CDN URLを指定しています。Iris 1.11.7のModrinth依存情報はSodium 0.9.2を要求しますが、実際のJARの`fabric.mod.json`は`0.9.x`を許可し、元の構成は0.9.3-beta.1です。Zoomify 2.17.0のModrinth依存情報には旧YACLプロジェクトも含まれていますが、JARが要求する`yet_another_config_lib_v3`は同梱構成のYACL 3.9.7で満たします。この2個をURL指定にすることで元のファイル構成を維持し、必要なSodium・YACL・Fabric API・Kotlinは明示的なパッケージとして固定しています。

## 起動と同期

WrapperはEnderpin 0.1.6に固定しています。Windowsで親ディレクトリのACL変更に失敗していた起動処理が修正され、通常のサンドボックスを有効にして起動できます。

`client/`で実行します。Windowsでは次のコマンドです。macOS / Linuxでは`.\enderpinw.ps1`を`./enderpinw`へ置き換えてください。

```powershell
.\enderpinw.ps1 sync --target client --locked
.\enderpinw.ps1 login
.\enderpinw.ps1 run
```

ゲームの保存先はこのリポジトリの`client/run/client/`です。PrismLauncherの元インスタンスを書き換えません。初回起動時はEnderpinの権限表示を確認してください。

## 共有する設定

`client/files/client/options.txt`と`client/files/client/config/`をゲームディレクトリへ同期します。日本語表示、キー操作、音量、描画距離16・シミュレーション距離12、最大120 FPS、各MODの表示・操作設定を含みます。

LithiumもPrism側の設定に合わせ、以前のクライアント独自の最適化無効化指定を外しています。サーバー側のLithium設定は従来どおりです。

Complementary Reimagined r5.9.3とLow on Fireも配置します。元の`options.txt`は`resourcePacks:["vanilla"]`、Irisは`shaderPack=`なので、追加パックは選択されていない状態です。導入と有効化は別で、元の選択状態を引き継ぎます。

Litematica / TweakerMoreの無効になっているカスタムschematicsディレクトリは、PC固有の絶対パスから`schematics`へ置き換えています。有効・無効の設定は変えていません。

次のデータは共有対象に含めません。

- ワールド、schematics本体、リプレイ、スクリーンショット、ログ、サーバー一覧・接続履歴
- Xaeroの地図・ウェイポイント、Distant HorizonsのLODデータ、Litematicaのサーバー別選択状態
- Sodiumの端末フィンガープリント、バックアップ、生成済み翻訳キャッシュ、プレイヤー別音量データ
- PrismLauncherのアカウント情報・ランチャー固有の設定

既存のEnderpin環境で共有設定を自分でも変更している場合、`sync`は競合を検出して停止します。残す設定を`files/client/`へ反映してから再同期してください。共有元へ戻す場合だけ、次を使用します。管理対象のローカル変更を破棄します。

```powershell
.\enderpinw.ps1 restore --target client
```

初めて共有管理に入る同名設定が`run/client/`にある場合は、バックアップして退避してから同期してください。

## MODの更新

バージョンを変更するときは`client/enderpin.toml`の固定IDを編集し、`sync --target client`で更新した`enderpin.lock`も一緒にコミットします。MOD設定は`client/files/client/`で編集します。ゲーム内で変更した内容を共有する場合も、この場所へコピーし直してください。
