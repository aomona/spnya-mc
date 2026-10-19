# バックアップと復元

[READMEへ戻る](../README.md) · [配布ガイド](deployment.md)

バックアップは**更新対象のサーバーを停止した後、設定を書き換える前**に作成します。定期バックアップではありません。初回配布の前にCDN APIキーとage公開鍵を設定してください。

## バックアップの内容

バックアップは停止した`workspace`全体（ワールド・設定・MOD・ログ・ローカルの権限承認情報）をtar/gzipにし、age公開鍵で暗号化した状態でHack Club CDNの`POST /api/v4/upload`へ送ります。CDNの取得URLは公開されるため、平文を送信しません。APIキーと復号用秘密鍵はゲームのworkspaceに配置しません。

## 容量と停止時間

CDNの[Verified枠](https://cdn.hackclub.com/docs/quotas)は1ファイル100MB・合計50GBです。暗号化ファイルが90MiB（94,371,840バイト）を超える場合だけ、90MiB以下の`.part-000000`、`.part-000001`…に分けて送ります。暗号化後のファイルを1MiBずつ読み出して送信し、分割ファイルのローカルコピーは作りません。合計容量の上限は変わりません。

配布前にAPI認証とage公開鍵を検証し、停止後は暗号化・アップロード・応答のサイズ確認まで成功してから既存ファイルを更新します。失敗時は更新を中断して停止状態にし、作成済みの暗号化ファイルは残します。送信中もサーバーは停止するため、バックアップ容量と上り帯域によって停止時間が増えます。APIの容量超過（HTTP 402）も配布失敗として扱います。

## 保存先と送信結果

各ホストに`/srv/enderpin/<名前>/backups/<名前>-<コミットSHA>-<UTC日時>.tar.gz.age`と、CDNの取得URL・upload ID・暗号化ファイルのSHA-256・サイズを記録した同名の`.json`を保存します。分割時はJSONの`parts`配列に各部分の順番・URL・ID・SHA-256・サイズを記録し、`complete: true`で全体の送信完了を示します。各部分の送信後にJSONを更新するので、途中失敗時も`complete: false`と送信済みのIDが残り、不要な部分をCDNから削除できます。応答が失われた送信の成否はCDNのUploads画面で確認し、自動再送は行いません。CDNへの再ダウンロードによる完全照合、古い世代の自動削除、定期実行は行いません。CDN側の保存容量を使い切らないよう、APIキーのアカウントで世代を管理してください。

## 初回設定

初回は管理するMacなどでageの復号用秘密鍵を生成し、安全な別の場所にも保管してください。復号用秘密鍵を失うと復元できません。以下の秘密鍵はGit管理外の場所に作成します。

```sh
brew install age
umask 077
mkdir -p "$HOME/.config/spnya-mc"
age-keygen -o "$HOME/.config/spnya-mc/backup.agekey"
age-keygen -y "$HOME/.config/spnya-mc/backup.agekey" | gh secret set ENDERPIN_BACKUP_AGE_RECIPIENT -R aomona/spnya-mc
# APIキーは gh secret set HACKCLUB_CDN_API_KEY -R aomona/spnya-mc の非表示入力で登録
```

手動のAnsible配布では、非公開の`deploy/secrets.yml`にも`enderpin_backup_api_key`と`enderpin_backup_age_recipient`を設定してください。暗号化したVaultを使う場合の例は[`deploy/ansible/vault.example.yml`](../deploy/ansible/vault.example.yml)にあります。復号用秘密鍵はAnsible、GitHub Secrets、CDNへ渡しません。

## 復元する

復元時はサーバーから取得した`.json`に記録されたURLから暗号化ファイルを取得し、SHA-256を照合してから管理用端末で復号します。配信先はPython標準のUser-Agentを403で拒否するため、取得時はUser-Agentを指定します。取得URLや復号後の内容を公開ログへ貼らないでください。

### 分割されていない場合

次の手順で取得・照合して復号します。

```sh
curl --fail --location --proto '=https' --proto-redir '=https' \
  --user-agent 'spnya-mc-backup-verification/1.0' \
  --output BACKUP.tar.gz.age 'JSONに記録された取得URL'
shasum -a 256 BACKUP.tar.gz.age
# JSONのsha256と一致することを確認してから復号する
age --decrypt --identity "$HOME/.config/spnya-mc/backup.agekey" \
  --output restored.tar.gz BACKUP.tar.gz.age
tar -tzf restored.tar.gz
```

### 分割されている場合

分割されたバックアップは、同名のJSONを管理端末に`BACKUP.json`として保存し、次の手順で全部分を取得・照合・結合します。`jq`と`curl`が必要です。結合後は`downloaded-backup/BACKUP.tar.gz.age`を上記のageコマンドで復号します。

```sh
bash <<'SH'
set -euo pipefail
jq -e '.complete == true and (.parts | length > 1)' BACKUP.json > /dev/null
mkdir downloaded-backup
cp BACKUP.json downloaded-backup/
cd downloaded-backup
jq -r '.parts[].url' BACKUP.json | while IFS= read -r url; do
  curl --fail --location --proto '=https' --proto-redir '=https' \
    --user-agent 'spnya-mc-backup-verification/1.0' --remote-name "$url"
done
jq -r '.parts[] | "\(.sha256)  \(.archive)"' BACKUP.json | shasum -a 256 -c -
cat *.tar.gz.age.part-* > BACKUP.tar.gz.age
jq -r '"\(.sha256)  BACKUP.tar.gz.age"' BACKUP.json | shasum -a 256 -c -
SH
```

### サーバーへ戻す

内容を確認してから、停止した対象サーバーのworkspaceへ復元します。
