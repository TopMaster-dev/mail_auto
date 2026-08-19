# 反響メール自動化システム

レントマガジン株式会社向けの、賃貸物件の反響メール自動対応システムです。

Gmail に届いたお問い合わせを5分ごとに取得し、NGワード・差別的表現をチェックしたうえで、
WordPress の物件データと照合して AI が返信文案を生成します。生成された文案は
Google スプレッドシートと管理画面に記録され、**担当者が内容を確認してから送信**します。
送信後は、お客様から返信があるまで2通目・3通目の追客メールを自動送信します。

---

## 処理の流れ

```
Gmail (IMAP, 5分ごと)
      │
      ├─ 反響フォーマット判定（自社フォーム / SUUMO）........ reflection
      ├─ 既存スレッドへの返信を検知 → 追客停止 ............... inquiry_processor
      │
      ▼
  受信本文のチェック ─ NGワード + 差別的表現(Claude) ......... content_checker
      │                                    └─ 検出 → NG検出 で停止
      ▼
  物件照合（URL → 物件名の順）............................... wp_client
      │  ├─ 空室あり  → 物件紹介文を生成
      │  ├─ 空室なし  → 代替物件を3件スコアリングして紹介 ..... property_scorer
      │  └─ 特定できず → 空室状況には触れず近い物件を提案
      ▼
  AI文案生成（物件紹介 / 代替紹介）.......................... draft_generator
      │
      ▼
  固定文と組み立て（署名・URL・日程ひな形はAIを通さない）..... assembler
      │
      ▼
  生成文のチェック（2回目）.................................. content_checker
      │
      ▼
  送信ゲート ................................................ send_gate
      ├─ 要確認 / NG検出        → 送信しない
      ├─ 確認あり（初期設定）   → 「自動送信可」で担当者待ち
      └─ 自動送信              → SMTP送信 → 2日後に追客を予約
                                          │
                                          ▼
                        追客スケジューラ（30分ごとに走査）..... followup_scheduler
                        2通目（2日後）→ 3通目（3日後）→ 追客完了
                        ※ 追客メールも送信前にNGチェックを行います
```

---

## 必要なもの

| 項目 | 内容 |
|------|------|
| Python | 3.11 以上（開発環境は 3.12） |
| Gmail | 2段階認証を有効化し、**アプリパスワード**を発行 |
| Claude API | API キー（`sk-ant-...`） |
| Google Sheets | サービスアカウントの JSON キーと、共有済みスプレッドシート |
| WordPress | `rentmagazine.jp` のアプリケーションパスワード（REST API 用） |

---

## セットアップ

```bash
# 1. 仮想環境
python -m venv venv
venv\Scripts\activate            # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt

# 2. 認証情報
copy .env.example .env           # Linux/macOS: cp .env.example .env
#    .env を編集し、サービスアカウントJSONを service_account.json として配置

# 3. スプレッドシートの初期化（タブと見出し行を作成、NGワードの初期値も投入）
python setup_sheets.py

# 4. 管理画面のパスワードハッシュを生成し、.env の ADMIN_PASSWORD_HASH に貼り付け
python -m admin.set_password "任意のパスワード"
```

### 環境変数（`.env`）

| 変数 | 用途 |
|------|------|
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | IMAP 受信・SMTP 送信 |
| `CLAUDE_API_KEY` | 文案生成・差別的表現チェック |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | サービスアカウントJSONのパス |
| `SPREADSHEET_ID` | スプレッドシートのID（URLの `/d/` と `/edit` の間） |
| `WP_BASE_URL` / `WP_APP_USER` / `WP_APP_PASSWORD` | WordPress REST API |
| `ADMIN_PASSWORD_HASH` | 管理画面のログインパスワード（bcrypt ハッシュ） |
| `FLASK_SECRET_KEY` | **必須。**未設定だと管理画面は起動しません（セッション偽造防止） |
| `ADMIN_ALLOWED_IPS` | 管理画面を許可するIP/CIDR。**空欄はすべてのIPを許可します** |

> `.env` と `service_account.json` は `.gitignore` 済みです。絶対にコミットしないでください。

---

## 実行方法

```bash
# 常時稼働（5分ごとのポーリング + 追客スケジューラ）
python main.py

# 1サイクルだけ実行して終了（動作確認用）
python main.py --once

# WordPress のフィールド名を確認する
python main.py --probe-wp

# 管理画面
python -m admin.app                          # 開発用 → http://localhost:5000
gunicorn admin.wsgi:app -b 127.0.0.1:5000    # 本番用
```

本番VPSへの配置（systemd / nginx / HTTPS）は **[deploy/DEPLOY.md](deploy/DEPLOY.md)** を参照してください。

---

## テスト

```bash
# ユニットテスト（130件）
python -m pytest tests/ -v

# 統合ドライラン（メールは送信しません）
python test_dryrun.py                # WordPress + スコアリング + NG + AI + 組立
python test_dryrun.py --wp-only      # WordPress と物件スコアリングのみ（APIキー不要）
python test_dryrun.py --check-only   # NGワードチェックのみ

# 管理画面の確認用に、テスト問い合わせを1件投入する
# （宛先はテスト用アドレスなので、送信しても実在の顧客には届きません）
python seed_test_inquiry.py your-address@example.com
```

機能ごとの検証手順は **`docs/TEST_PLAN.md`** にまとめています
（現在このファイルは `.gitignore` 対象のため、リポジトリには含まれていません）。

---

## ディレクトリ構成

```
mail_automation/
├── main.py                        # 常駐エントリポイント（ポーリングループ）
├── setup_sheets.py                # スプレッドシート初期化（1回だけ実行）
├── test_dryrun.py                 # 統合ドライラン
├── seed_test_inquiry.py           # 管理画面確認用のテストデータ投入
├── src/
│   ├── core/
│   │   ├── inquiry_processor.py   # 全体のオーケストレーター
│   │   ├── reflection.py          # 反響フォーマット判定・顧客情報抽出
│   │   └── models.py              # Inquiry / Property / CheckResult
│   ├── integrations/
│   │   ├── gmail_client.py        # IMAP受信 + SMTP送信
│   │   ├── sheets_client.py       # スプレッドシートの全読み書き
│   │   └── wp_client.py           # WordPress REST API（投稿タイプ estate）
│   ├── ai/
│   │   ├── draft_generator.py     # 文案生成（3タスク）
│   │   └── content_checker.py     # NGワード + 差別的表現チェック
│   ├── matching/
│   │   ├── property_scorer.py     # 代替物件スコアリング（8段階）
│   │   └── area.py                # エリア近接判定（遠方物件の抑止）
│   ├── email_builder/
│   │   ├── assembler.py           # 固定文 + AI生成文の組立
│   │   └── send_gate.py           # 送信可否の判定
│   ├── scheduling/
│   │   └── followup_scheduler.py  # 追客（2通目・3通目）
│   └── config_loader.py           # settings.yaml + .env の読み込み
├── admin/                         # Flask 管理画面
├── config/
│   ├── settings.yaml              # 全設定（認証情報は .env から参照）
│   └── prompts/                   # Claude プロンプトテンプレート
├── deploy/                        # systemd ユニット / nginx 設定 / 手順書
├── docs/                          # 操作マニュアル / テスト計画
├── tests/                         # ユニットテスト
└── logs/                          # mail_automation.log（10MB × 5世代）
```

---

## スプレッドシート構成

| シート | 用途 |
|--------|------|
| 問い合わせ一覧 | すべての問い合わせ・文案・ステータス（18列） |
| NGワード | **ワード / カテゴリ**。編集すると次のサイクルから反映されます |
| 設定 | 自動送信などの動作設定 |
| 送信履歴 | 送信したメールの記録（返信検知にも使用） |
| 追客履歴 | 追客メールの送信記録 |
| 要確認履歴 | NG検出・エラーの記録 |
| テンプレート | 文面テンプレート |

### ステータス

| ステータス | 意味 |
|------------|------|
| 未対応 | 受信直後 |
| AI返信文生成済み | 文案の生成が完了 |
| 自動送信可 | 文案完成・担当者の送信待ち |
| 要確認 | AI生成エラー・送信失敗など、人の確認が必要 |
| NG検出 | NGワードまたは差別的表現を検出（送信されません） |
| 送信済み | 返信メールを送信済み |
| 返信あり | お客様から返信あり（追客は自動停止） |

追客ステータスは `追客中` / `追客停止` / `追客完了` の3種類です。

---

## 主な設定（`config/settings.yaml`）

| 設定 | 初期値 | 内容 |
|------|--------|------|
| `gmail.poll_interval_seconds` | `300` | メール取得の間隔（秒） |
| `send.all_require_confirmation` | `true` | **安全スイッチ。** `true` の間は自動送信されません |
| `business_hours` | `10:00`–`18:00` | 土日祝も営業。時間外は「翌営業日にご返信」の一文が入ります |
| `followup.steps` | `2日` → `3日` | 追客の間隔 |
| `followup.max_followups` | `2` | 2通目・3通目まで（1通目は初回返信） |
| 反響判定 | フォーマット判定 | 自社フォーム / SUUMO の形式のみ取り込み（`src/core/reflection.py`） |
| `admin.allowed_ips` | `.env` 参照 | 管理画面のIP制限 |

---

## 安全設計

このシステムは、**誤ってお客様に不適切なメールを送らないこと**を最優先に設計されています。

- **初期設定では自動送信しません。** `send.all_require_confirmation: true` の間、
  すべての文案は「自動送信可」で止まり、担当者が管理画面で確認してから送信します。
- **チェックは2回**。受信したメール本文と、AI が生成した文案の両方を検査します。
- **追客メールも検査対象**です。2通目・3通目は毎回新しく生成されるため、
  送信前に同じNGチェックを通し、検出時は追客を停止します。
- **異常時は必ず止まる方向に倒します。** 差別的表現チェックのAPIエラー、
  文案生成の失敗、SMTP送信の失敗は、いずれも送信せず「要確認」になります。
- **固定文はAIを通しません。** 会社署名・物件URL・割引URL・日程ひな形は
  定数として管理し、AI は指定箇所の文章のみを生成します。
- **物件を特定できなかった場合、空室状況について断定しません。**
  「申し込みが入った」とも「空いている」とも書かず、近い物件を提案します。

自動送信を有効にする場合は、十分な試験運用のうえで
`send.all_require_confirmation` を `false` にし、「設定」シートの条件を有効化してください。

---

## ドキュメント

| ドキュメント | 対象読者 |
|--------------|----------|
| [docs/OPERATION_MANUAL.md](docs/OPERATION_MANUAL.md) | ご担当者さま向けの操作・運用マニュアル |
| [deploy/DEPLOY.md](deploy/DEPLOY.md) | VPSへの配置手順（systemd / nginx / HTTPS） |
| `docs/TEST_PLAN.md` ※ | 機能ごとの検証手順（要件対応表） |
| [PHASE1_DEV_GUIDE.md](PHASE1_DEV_GUIDE.md) | 開発者向けの設計メモ |

※ `docs/TEST_PLAN.md`・`PHASE1_DEMO_GUIDE.md`・`DEVELOPMENT_PLAN.md` は
現在 `.gitignore` 対象のため、リポジトリには含まれていません（手元にのみ存在します）。

---

## セキュリティ上の注意

- `.env`、`service_account.json` は **絶対にコミットしない**でください（`.gitignore` 済み）。
- 本番では `ADMIN_ALLOWED_IPS` を必ず設定してください。空欄のままだと
  管理画面はすべてのIPからのアクセスを受け付けます（起動時に警告が出ます）。
- 管理画面は nginx 経由の HTTPS で公開し、`settings.yaml` の
  `admin.behind_proxy` を `true` にしてください（Cookie に Secure 属性が付きます）。
- ポート 5000 は外部公開せず、`127.0.0.1` にバインドしてください。
