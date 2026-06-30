# VPS デプロイ手順（ConoHa / Ubuntu）

反響メール自動化システムを本番VPS（Ubuntu 22.04 想定）へ配置し、24時間稼働
させる手順です。`poll`（受信・追客スケジューラ）と `admin`（管理画面）の
2つの systemd サービスを起動します。

---

## 0. 前提

- Ubuntu 22.04 LTS の VPS（sudo 権限）
- 独自ドメイン（管理画面をHTTPSで公開する場合）
- 以下のファイルを手元に用意：`.env`、`service_account.json`

---

## 1. システムユーザーとディレクトリ

```bash
sudo adduser --system --group --home /opt/mail_automation mailauto
sudo mkdir -p /opt/mail_automation
sudo chown mailauto:mailauto /opt/mail_automation
```

## 2. パッケージ

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx
sudo timedatectl set-timezone Asia/Tokyo     # 営業時間判定のためJST必須
```

## 3. アプリ配置

ソースを `/opt/mail_automation` に配置（git or scp）。例（scp）:

```bash
# ローカルからアップロード後、サーバ側で所有者を変更
sudo chown -R mailauto:mailauto /opt/mail_automation
```

## 4. Python 環境

```bash
cd /opt/mail_automation
sudo -u mailauto python3 -m venv venv
sudo -u mailauto venv/bin/pip install -U pip
sudo -u mailauto venv/bin/pip install -r requirements.txt
```

## 5. 認証情報の配置

```bash
# .env と service_account.json をプロジェクト直下へ
sudo -u mailauto cp /path/to/.env                 /opt/mail_automation/.env
sudo -u mailauto cp /path/to/service_account.json /opt/mail_automation/service_account.json
sudo chmod 600 /opt/mail_automation/.env /opt/mail_automation/service_account.json
```

`.env` 内で本番用に確認すべき項目：
- `CLAUDE_API_KEY`、`SPREADSHEET_ID`、`GMAIL_*`
- `ADMIN_PASSWORD_HASH`（`venv/bin/python -m admin.set_password "本番パスワード"` で生成）
- `FLASK_SECRET_KEY`（ランダムな長い文字列）
- `ADMIN_ALLOWED_IPS`（管理画面を許可するIP/CIDR。例 `203.0.113.0/24`）

`config/settings.yaml` で確認：
- `admin.behind_proxy: true`（nginx 経由で公開する場合）
- `followup.enabled: true`、`send.all_require_confirmation`（運用方針に応じて）

## 6. スプレッドシートの初期化

```bash
cd /opt/mail_automation
sudo -u mailauto venv/bin/python setup_sheets.py
```

## 7. systemd サービス登録

```bash
sudo cp deploy/mail-automation.service       /etc/systemd/system/
sudo cp deploy/mail-automation-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mail-automation.service
sudo systemctl enable --now mail-automation-admin.service
sudo systemctl status mail-automation.service
```

## 8. nginx + HTTPS（管理画面）

```bash
sudo cp deploy/nginx-admin.conf /etc/nginx/sites-available/mail-admin
# server_name を実ドメインに編集
sudo ln -s /etc/nginx/sites-available/mail-admin /etc/nginx/sites-enabled/
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d admin.example.com
sudo nginx -t && sudo systemctl reload nginx
```

> nginx 経由にしたら `settings.yaml` の `admin.behind_proxy: true` を設定して
> `sudo systemctl restart mail-automation-admin` してください。

## 9. ファイアウォール

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

管理画面ポート（5000）は外部公開しません（nginx 経由 127.0.0.1 のみ）。

---

## 運用コマンド

```bash
# 状態確認
sudo systemctl status mail-automation mail-automation-admin

# 再起動 / 停止
sudo systemctl restart mail-automation
sudo systemctl stop mail-automation-admin

# ログ（リアルタイム）
journalctl -u mail-automation -f
journalctl -u mail-automation-admin -f
# アプリログファイル
tail -f /opt/mail_automation/logs/mail_automation.log
```

## 動作確認チェックリスト

- [ ] `systemctl status` が両サービスとも active (running)
- [ ] `journalctl -u mail-automation` に「Poll cycle start/end」「Follow-up scheduler started」
- [ ] `https://admin.example.com` でログインできる
- [ ] テスト問い合わせ → 数分で管理画面に表示される
- [ ] 送信ボタンでメールが届く（テストアドレス宛）
- [ ] `data/scheduler.db` が生成されている（追客ジョブ永続化）

## 更新（再デプロイ）

```bash
cd /opt/mail_automation
# 新しいソースを配置後
sudo -u mailauto venv/bin/pip install -r requirements.txt
sudo systemctl restart mail-automation mail-automation-admin
```
