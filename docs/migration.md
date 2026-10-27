# Enderpin v0.2.0 / format 4への移行

この変更は構成と配布処理の移行です。本番の停止・配布・実ゲームでの確認は別途行います。mainへのマージ後は従来のActionsが本番配布を開始するため、切り替え時間を決めてからマージしてください。

## 管理するファイル

| 旧配置 | 新配置 |
| --- | --- |
| `client/enderpin.toml` と `servers/*/enderpin.toml` | ルートの `enderpin.toml` |
| 各workspaceの `enderpin.lock` | ルートの `enderpin.lock`（対象ごとの固定結果） |
| `client/enderpinw*` とWrapper設定 | ルートの同名ファイル（v0.2.0固定） |
| `client/files/client/` | `client/files/` |
| 両サーバーの `files/server/config/` | `shared/backend/files/config/` |
| `servers/proxy/files/server/plugins/` | `proxy/files/plugins/` |

MODのバージョン・ファイルハッシュ、Java・Minecraft・ローダーのダウンロード情報は旧lockから保持しています。新しいmanifestに対するfingerprintをEnderpin v0.2.0の実装で再計算し、`sync --locked`で検証します。format番号だけの置換や最新版への再解決は行いません。

`groups.common`でclientと両バックエンドの共通MODを、`groups.backend`でFabricProxy-LiteとDiscordバックエンドを共有します。proxyのプラグインは`targets.proxy.packages`です。survivalとcreativeは`profiles.backend`を使い、共有設定は`file_groups = ["backend"]`で選びます。

## 既存のローカルクライアント

既存checkoutのGit管理外データは自動移動しません。旧 `client/run/client/` がある場所で、そのまま新しい `sync` を実行しないでください。

1. クライアントを終了し、旧checkoutの`client/`全体を別の場所へバックアップします。
2. **別ディレクトリへ新しくclone**し、ルートで`./enderpinw sync --target client --locked`を実行します。
3. 旧 `client/run/client/saves/`、スクリーンショット、リプレイ、サーバー一覧など必要な個人データを、新 `client/run/`の対応する場所へコピーします。生成済みのMOD・共有設定を旧ディレクトリで丸ごと上書きしないでください。
4. 旧環境で変更した設定は新`client/files/`との差分を確認して反映します。アカウントは新環境でログインし、実行計画を確認して権限を承認します。旧`.enderpin`の承認はコピーしません。
5. セーブデータなどを確認するまで旧checkoutとバックアップを保持します。

## 本番のサーバー・プロキシ

サービス名・ユーザー・ホスト・ポート・メモリは変えません。配布元は全サービス`workspace: .`となり、`instance.name`がEnderpinの対象名になります。各サービスの配置先は引き続き`/srv/enderpin/<名前>/workspace`です。

Ansibleの初回配布は次の順で行います。

1. 新しいバイナリと統合workspaceを一時領域へ転送し、対象だけ`sync --locked`します。旧配置・移行先・管理記録も停止前に検査します。
2. 対象サービスを正常終了し、旧workspace全体を暗号化して外部バックアップへ送信します。成功するまで移行しません。
3. `run/server/`を`<名前>/run/`へrenameします。ワールド・プレイヤーデータ・Discord登録・Floodgate鍵・EULAなど、ランタイム内のファイルを保持します。
4. 旧管理記録のパスを新配置へ変換し、新しい管理記録だけを作ります。旧記録は残し、権限承認は再利用しません。
5. 新しい入力を配置し、`restore`で管理ファイルを反映して`sync --locked --offline`で検証します。Ansibleが生成する設定には既存の秘密情報を使います。
6. 新しい対象の権限計画を確認・承認して起動します。TCP待受とsystemdの状態を確認してから配布成功を記録します。

途中で失敗するとサービスを停止したまま終了します。移行先に別のランタイムがある場合は統合せずエラーにします。原因を確認して再配布するか、停止した状態でバックアップを復元してください。復元には旧バイナリと旧unitの`--target server`も必要です。新しいワールドの進行を古いバックアップで自動的に巻き戻すことはしません。

既存のNetherNet用Netlink修正はv0.2.0にも必要なため、サーバー配布では引き続きパッチ付きバイナリを使います。通常のWrapperは公式v0.2.0を取得するため、本番プロキシ用バイナリの代替にしないでください。
