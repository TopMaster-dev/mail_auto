# Phase 1 開発ガイド（マイルストーン1 / 75,000円）

**納品スコープ**:  
メール受信 → 物件照合 → NGチェック → AI文案生成 → 送信ゲート までの**ドラフト生成パイプライン**  
（追客スケジューラ・管理画面はマイルストーン2）

---

## ディレクトリ構成

```
mail_automation/
├── src/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── inquiry_processor.py   # メインオーケストレーター
│   │   └── models.py              # データクラス定義
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── sheets_client.py       # Google Sheets 全読み書き
│   │   ├── gmail_client.py        # IMAP受信 + SMTP送信
│   │   └── wp_client.py           # WordPress REST API クライアント
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── draft_generator.py     # Claude API文案生成（3タスク）
│   │   └── content_checker.py     # NGワード + 差別表現チェック
│   ├── matching/
│   │   ├── __init__.py
│   │   └── property_scorer.py     # 代替物件スコアリング
│   └── email_builder/
│       ├── __init__.py
│       ├── assembler.py           # 固定文 + AI生成文の組立
│       └── send_gate.py           # 送信モード判定 + SMTP送信
├── config/
│   ├── settings.yaml              # 全設定（認証情報は.envから読む）
│   └── prompts/
│       ├── property_intro.txt     # 物件紹介文プロンプト
│       ├── alt_property_intro.txt # 代替物件紹介文プロンプト
│       └── visit_invitation.txt   # 来店誘導一言プロンプト
├── data/
│   └── scheduler.db               # APScheduler用（Phase2で使用）
├── logs/
│   └── .gitkeep
├── tests/
│   ├── test_content_checker.py
│   ├── test_property_scorer.py
│   └── test_draft_generator.py
├── .env                           # ← gitignore済み・実値を入れる
├── .env.example                   # ← コミット可
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 環境セットアップ（ConoHa VPS Ubuntu）

```bash
# Python 3.11 インストール
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip

# プロジェクトディレクトリ
mkdir -p /opt/mail_automation && cd /opt/mail_automation

# 仮想環境
python3.11 -m venv venv
source venv/bin/activate

# パッケージインストール（requirements.txt から）
pip install -r requirements.txt
```

**requirements.txt:**
```
anthropic>=0.25.0
gspread>=6.0.0
google-auth>=2.0.0
imap-tools>=1.5.0
flask>=3.0.0
flask-login>=0.6.0
apscheduler>=3.10.0
sqlalchemy>=2.0.0
pandas>=2.0.0
pyyaml>=6.0
python-dotenv>=1.0.0
requests>=2.31.0
bcrypt>=4.0.0
jpholiday>=0.1.5
```

---

## Feature 1: プロジェクト基盤 + 設定管理

### 実装内容

**`config/settings.yaml`** — 全設定の骨格（認証情報は.envから参照）:
```yaml
gmail:
  address: "${GMAIL_ADDRESS}"
  app_password: "${GMAIL_APP_PASSWORD}"
  imap_host: imap.gmail.com
  smtp_host: smtp.gmail.com
  smtp_port: 587
  poll_interval_seconds: 300   # 5分ごと

claude:
  api_key: "${CLAUDE_API_KEY}"
  model: claude-sonnet-4-6
  max_tokens: 800

sheets:
  service_account_json: "${GOOGLE_SERVICE_ACCOUNT_JSON}"
  spreadsheet_id: "${SPREADSHEET_ID}"
  sheets:
    inquiries: "問い合わせ一覧"
    properties: "物件一覧"
    ng_words: "NGワード"
    templates: "テンプレート"
    send_log: "送信履歴"
    followup_log: "追客履歴"
    review_log: "要確認履歴"
    config: "設定"

wordpress:
  base_url: "${WP_BASE_URL}"
  estate_post_type: estate
  per_page: 100
  app_user: "${WP_APP_USER}"
  app_password: "${WP_APP_PASSWORD}"

business_hours:
  start: "10:00"
  end: "18:00"
  # 土日祝も営業のため曜日チェック不要

send_defaults:
  all_require_confirmation: true   # 初期設定：全件確認あり

company:
  name: "レントマガジン株式会社"
  staff_name: "担当者名をここに設定"
  tel: "0566-70-7117"
  fax: "0566-70-7228"
  email: "contact@rentmagazine.jp"
  web: "https://rentmagazine.jp"
  address: "〒472-0012 愛知県知立市八ツ田町1-8-3 YATSUDA APARTMENT 1F"
  instagram: "https://www.instagram.com/rent_magazine/"
  discount_url: "https://rentmagazine.jp/campaign1/"
  mypage_url: "https://rentmagazine.jp/mypage/"
  line_url: "https://liff.line.me/1660986243-R6pAo4W6?unique_key=DE9fwx&ts=1731193291"
  tel_reservation: "0566-70-8282"
```

**`src/core/models.py`** — 全データクラス:
```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class Inquiry:
    id: str                          # UUID
    received_at: datetime
    customer_name: str
    customer_email: str
    inquiry_property_name: str
    inquiry_property_url: str
    raw_body: str
    status: str = "未対応"
    is_vacant: Optional[bool] = None
    ai_draft: str = ""
    auto_send_eligible: bool = False
    ng_words_hit: list = field(default_factory=list)
    ng_category: str = ""
    discriminatory_flag: bool = False
    discriminatory_reason: str = ""
    followup_status: str = "追客中"
    next_followup_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    staff_memo: str = ""

@dataclass
class Property:
    wp_id: int
    name: str
    url: str
    rent: int                    # 円
    management_fee: int          # 円
    layout: str                  # 1K, 2LDK 等
    nearest_station: str
    train_line: str
    city: str                    # 市区町村
    walk_minutes: int
    category: list               # ["デザイナーズ", "ペット可"] 等
    equipment: list              # ["追い焚き", "宅配BOX"] 等
    is_vacant: bool
    is_commission_free: bool
    area_sqm: float
    building_type: str           # 単身/ファミリー

@dataclass  
class CheckResult:
    is_clean: bool
    ng_words: list               # [{"word": "クレーム", "category": "クレーム系"}]
    discriminatory: bool
    discriminatory_reason: str
    requires_review: bool
```

### Gitコミット（2件）

```
feat: project scaffold, config loader, and data models

- Directory structure, requirements.txt, .env.example
- settings.yaml with all config keys (credentials via env vars)
- Inquiry, Property, CheckResult dataclasses in models.py
- dotenv loader with validation for required keys
```

```
feat: Google Sheets client with all sheet read/write operations

- SheetsClient class with gspread service account auth
- write_inquiry(), update_inquiry_status(), read_ng_words()
- read_properties(), write_send_log(), write_followup_log()
- Batch write support to stay within 300 writes/min quota
- NFKC normalization for Japanese text consistency
```

---

## Feature 2: Gmail IMAP メール受信

### 実装内容

**`src/integrations/gmail_client.py`**:
```python
from imap_tools import MailBox, AND
from datetime import datetime
import unicodedata

class GmailClient:
    def __init__(self, address: str, app_password: str, imap_host: str):
        self._address = address
        self._password = app_password
        self._imap_host = imap_host

    def fetch_unread(self) -> list[dict]:
        """接続→未読取得→切断 のサイクル（永続接続しない）"""
        results = []
        with MailBox(self._imap_host).login(self._address, self._password) as mb:
            for msg in mb.fetch(AND(seen=False), mark_seen=True):
                results.append({
                    "uid": msg.uid,
                    "message_id": msg.headers.get("message-id", [""])[0],
                    "in_reply_to": msg.headers.get("in-reply-to", [""])[0],
                    "from_addr": msg.from_,
                    "from_name": msg.from_values.name or "",
                    "subject": msg.subject,
                    "body": unicodedata.normalize("NFKC", msg.text or msg.html or ""),
                    "date": msg.date or datetime.now(),
                })
        return results
```

**受信メールの解析 — `src/core/inquiry_processor.py`** にて:
- 物件名: 件名から `【物件名】` パターンまたはメール本文から抽出
- 顧客名: 差出人名フィールド
- 問い合わせ物件URL: 本文中のrentmagazine.jpリンク抽出
- 返信検知: `in_reply_to` が既存Inquiryの `message_id` に一致 → 追客停止フラグ

### Gitコミット（2件）

```
feat: Gmail IMAP poller with connect-fetch-disconnect cycle

- GmailClient.fetch_unread() with imap-tools
- NFKC normalization for all Japanese body text
- Exponential backoff on IMAP connection failure (max 3 retries)
- Reply detection via In-Reply-To header matching
```

```
feat: incoming email parser and inquiry deduplication

- Extract customer name, email, property name, property URL from body
- Generate UUID per inquiry, write initial row to Sheets
- Skip already-processed message IDs (dedup via Sheets lookup)
- Log each poll cycle (success/failure) to rotating file handler
```

---

## Feature 3: WordPress REST API — 物件データ取得

### WordPress API分析結果

スクリーンショットから確認した情報：
- **物件カスタム投稿タイプ**: `estate`
- **REST APIエンドポイント**: `https://rentmagazine.jp/wp-json/wp/v2/estate`
- **関連タクソノミー**（フィールドマッピング）:

| タクソノミースラッグ | 意味 | Propertyフィールド |
|---------------------|------|-------------------|
| `train` | 沿線 | `train_line` |
| `area` | エリア | `city` |
| `space` | 間取り | `layout` |
| `condition` | 条件（ペット可等） | `category` |
| `condition2` | 条件2 | `category`（追加） |
| `economical` | 経済条件（仲介手数料無料等） | `is_commission_free` |
| `building` | 建物種別 | `building_type` |
| `room_category` | 部屋カテゴリ | `category`（追加） |
| `cat2` | カテゴリ2 | `category`（追加） |

### 実装内容

**`src/integrations/wp_client.py`**:
```python
import requests
from requests.auth import HTTPBasicAuth

class WordPressClient:
    def __init__(self, base_url: str, app_user: str, app_password: str):
        self._base = base_url.rstrip("/")
        self._auth = HTTPBasicAuth(app_user, app_password)

    def get_all_estates(self) -> list[dict]:
        """全物件を100件ずつページ取得"""
        results, page = [], 1
        while True:
            r = requests.get(
                f"{self._base}/wp-json/wp/v2/estate",
                params={"per_page": 100, "page": page, "status": "publish"},
                auth=self._auth, timeout=30,
            )
            if r.status_code == 400:  # ページ終端
                break
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            results.extend(batch)
            page += 1
        return results

    def get_estate_by_id(self, wp_id: int) -> dict:
        r = requests.get(
            f"{self._base}/wp-json/wp/v2/estate/{wp_id}",
            auth=self._auth, timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def get_taxonomy_terms(self, taxonomy: str) -> dict[int, str]:
        """タームID → ターム名のマップを返す"""
        r = requests.get(
            f"{self._base}/wp-json/wp/v2/{taxonomy}",
            params={"per_page": 100}, auth=self._auth, timeout=15,
        )
        r.raise_for_status()
        return {t["id"]: t["name"] for t in r.json()}
```

**`src/matching/property_scorer.py`** — 物件ローカルキャッシュとスコアリング:
```python
import pandas as pd

class PropertyScorer:
    """
    1,568件をDataFrameにロードし、問い合わせ物件の条件で
    8段階優先度スコアリングにより代替物件3〜5件を返す。
    """
    def find_alternatives(self, inquiry_property: Property, top_n: int = 3) -> list[Property]:
        df = self._df[self._df["is_vacant"] == True].copy()
        df["score"] = 0

        # 優先度1: 同最寄駅
        df.loc[df["nearest_station"] == inquiry_property.nearest_station, "score"] += 100
        # 優先度2: 同沿線
        df.loc[df["train_line"] == inquiry_property.train_line, "score"] += 60
        # 優先度3: 同市区町村
        df.loc[df["city"] == inquiry_property.city, "score"] += 40
        # 優先度4: 近隣市区町村（別途定義リストで）
        # 優先度5: 家賃±10,000円
        rent_diff = (df["rent"] - inquiry_property.rent).abs()
        df.loc[rent_diff <= 10000, "score"] += 30
        # 優先度6: 同間取り
        df.loc[df["layout"] == inquiry_property.layout, "score"] += 20
        # 優先度7: カテゴリ一致
        for cat in inquiry_property.category:
            df.loc[df["category"].apply(lambda c: cat in c), "score"] += 15
        # 優先度8: 設備一致数
        for eq in inquiry_property.equipment:
            df.loc[df["equipment"].apply(lambda e: eq in e), "score"] += 5

        top = df.nlargest(top_n, "score")
        return [self._row_to_property(r) for _, r in top.iterrows()]
```

### Gitコミット（3件）

```
feat: WordPress REST API client for estate post type

- WordPressClient with paginated GET for all 1,568 estate posts
- Taxonomy term cache (train/area/space/condition/economical etc.)
- HTTP Basic Auth via WP Application Password
- Retry with backoff on transient 5xx errors
```

```
feat: property data normalizer and vacancy lookup

- Map WP REST API response fields to Property dataclass
- Extract is_vacant from status/availability custom field
- Extract is_commission_free from economical taxonomy
- Normalize rent, layout, station, category, equipment fields
```

```
feat: alternative property scorer with 8-tier priority algorithm

- Load all properties into pandas DataFrame on startup (1,568 rows)
- Score by station > line > city > rent-range > layout > category > equipment
- Property-type weighting: single-unit vs family vs designer
- Return top 3–5 results, filter out vacant=False entries
```

---

## Feature 4: NGワードスキャナー + 差別的表現チェッカー

### 実装内容

**`src/ai/content_checker.py`**:
```python
import unicodedata
from anthropic import Anthropic

DISCRIMINATORY_PROMPT = """
あなたは不動産業務の法令遵守チェッカーです。
以下のメール文を読み、差別的・排除的表現が含まれているか判定してください。

判定基準：
- 属性（外国籍・生活保護・高齢者・障がい・ひとり親・LGBT等）を理由に
  「不可」「お断り」「入居できない」「紹介できない」と述べている場合 → discriminatory: true
- 上記属性に関して相談可能・案内可能と述べている場合 → discriminatory: false
- 単純に属性ワードが出るだけでは true にしない

対象テキスト:
{text}

以下のJSON形式のみで回答（説明不要）:
{{"discriminatory": true/false, "reason": "判定理由を20字以内で"}}
"""

class ContentChecker:
    def __init__(self, ng_words: list[dict], claude_client: Anthropic, model: str):
        self._ng_words = ng_words  # [{"word": "クレーム", "category": "クレーム系"}]
        self._client = claude_client
        self._model = model

    def check(self, text: str) -> "CheckResult":
        normalized = unicodedata.normalize("NFKC", text)
        hits = [
            w for w in self._ng_words
            if unicodedata.normalize("NFKC", w["word"]) in normalized
        ]
        disc_result = self._check_discriminatory(text)
        requires_review = bool(hits) or disc_result["discriminatory"]
        return CheckResult(
            is_clean=not requires_review,
            ng_words=hits,
            discriminatory=disc_result["discriminatory"],
            discriminatory_reason=disc_result["reason"],
            requires_review=requires_review,
        )

    def _check_discriminatory(self, text: str) -> dict:
        """Claude APIで文脈判定。失敗時はrequires_review=Trueにフォールバック"""
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=100,
                system="JSONのみで回答してください。",
                messages=[{"role": "user", "content": DISCRIMINATORY_PROMPT.format(text=text[:2000])}],
            )
            import json
            return json.loads(resp.content[0].text)
        except Exception:
            return {"discriminatory": True, "reason": "判定エラー（要確認）"}
```

### Gitコミット（2件）

```
feat: NG word scanner with 6-category classification

- Load NG word list from Sheets on startup, refresh per poll cycle
- NFKC normalization to handle full/half-width character variants
- Return hit list with word and category for each match
- Check both incoming email body and AI-generated draft text
```

```
feat: discriminatory expression context checker via Claude API

- Claude API call with structured JSON output prompt
- Distinguishes "外国人不可" (flag) from "外国籍の方もご相談可能" (pass)
- JSON parse error falls back to requires_review=True (safe default)
- Full input/output logged for post-deployment audit
```

---

## Feature 5: Claude AI 返信文案生成

### 実装内容

プロンプトキャッシュ設計 — 会社情報は**キャッシュブロック**に置き、
毎回送信するプロンプト部分のAPI費用を30〜60%削減します。

**`src/ai/draft_generator.py`**:
```python
from anthropic import Anthropic
import json

SYSTEM_CACHED = """
あなたはレントマガジン株式会社の賃貸物件担当者です。
以下の制約を厳守してください：

絶対に生成しない内容（固定テキストとして別途差し込む）：
- 会社名・住所・電話番号・メールアドレス・署名
- 割引URL・物件URL・希望日時ひな形
- 空室状況の断定・審査可否・契約条件・入居可否・申込可否
- 初期費用の確定金額・「残り1室」等の在庫断定

生成する内容（自然な日本語で200字以内）：
指示された箇所のみ生成し、他は一切出力しない。
"""

class DraftGenerator:
    def __init__(self, client: Anthropic, model: str):
        self._client = client
        self._model = model

    def generate_property_intro(self, property: "Property") -> str:
        """物件紹介文 — 魅力・立地・設備・内覧したくなる一言"""
        prompt = f"""
以下の物件について、来店・内覧したくなる自然な紹介文を150〜200字で生成してください。

物件情報：
- 最寄駅：{property.train_line}「{property.nearest_station}」徒歩{property.walk_minutes}分
- 間取り：{property.layout}
- 家賃：{property.rent:,}円
- カテゴリ：{', '.join(property.category)}
- 設備：{', '.join(property.equipment[:6])}

制約：空室状況・審査・契約条件は一切触れない。
JSON形式: {{"intro": "生成した紹介文"}}
"""
        return self._call(prompt)["intro"]

    def generate_alt_property_intro(self, original: "Property", alt: "Property") -> str:
        """代替物件紹介文 — 「代わり」でなく「こちらも合いそう」のトーン"""
        prompt = f"""
お客様が問い合わせた物件は紹介できませんでした。
以下の代替物件を自然に紹介する文章（150字以内）を生成してください。

問い合わせ物件：{original.name}（{original.layout}・{original.nearest_station}）
代替物件：{alt.name}（{alt.layout}・{alt.nearest_station}・{alt.train_line}）
共通点として活用できる情報：{', '.join(set(original.category) & set(alt.category))}

制約：「代わりの物件です」という表現は使わない。
JSON形式: {{"intro": "生成した紹介文"}}
"""
        return self._call(prompt)["intro"]

    def generate_visit_invitation(self, inquiry_body: str) -> str:
        """来店誘導前の一言 — 問い合わせ内容に応じたパーソナライズ"""
        prompt = f"""
以下の問い合わせ内容を読み、来店・WEB面談への誘導前に置く自然な一言（40字以内）を生成してください。
例：初期費用重視→「費用面も一緒にご確認いただけます」

問い合わせ内容（抜粋）：{inquiry_body[:300]}

JSON形式: {{"sentence": "生成した一言"}}
"""
        return self._call(prompt)["sentence"]

    def _call(self, user_prompt: str) -> dict:
        """プロンプトキャッシュ付きAPIコール。JSON解析失敗は3回リトライ"""
        for attempt in range(3):
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=300,
                system=[{
                    "type": "text",
                    "text": SYSTEM_CACHED,
                    "cache_control": {"type": "ephemeral"},  # プロンプトキャッシュ
                }],
                messages=[{"role": "user", "content": user_prompt}],
            )
            try:
                return json.loads(resp.content[0].text)
            except json.JSONDecodeError:
                if attempt == 2:
                    raise
        return {}
```

### Gitコミット（2件）

```
feat: Claude API client with prompt caching for system context

- Anthropic SDK with cache_control: ephemeral on system prompt
- Retry loop (3 attempts) with stricter JSON instruction on failure
- Third failure raises exception → caller sets status to 要確認
- Token usage and cache hit/miss logged per call
```

```
feat: AI draft generation for property intro, alt intro, visit invitation

- generate_property_intro(): features/location/equipment/teaser line
- generate_alt_property_intro(): similarity-focused, not "replacement" tone
- generate_visit_invitation(): inquiry-context-aware personalized sentence
- All outputs validated: no vacancy/screening/contract assertions
```

---

## Feature 6: メール組立 + 送信ゲート + SMTP送信

### 実装内容

**`src/email_builder/assembler.py`** — 固定文 + AI生成文のマージ:

メールは以下のブロック構造で組み立てます。  
**AIは`{{AI_BLOCK_*}}`部分のみ生成し、他は全て固定テキストです。**

```
[冒頭] {顧客名}様 / お礼文 / 会社名
[物件情報ブロック]
  - 空室あり: 物件名・物件URL・{{AI_BLOCK_PROPERTY_INTRO}}
  - 空室なし: 申込済み案内・代替物件URL×3件・{{AI_BLOCK_ALT_INTRO}}×3件
[{{AI_BLOCK_VISIT_INVITATION}}]  ← 来店誘導一言
[共通文（来店誘導・割引・日程ひな形）]  ← 固定
[署名]  ← 固定
```

**`src/email_builder/send_gate.py`** — 送信前チェックと分岐:
```python
from datetime import datetime, time

class SendGate:
    def evaluate(self, inquiry: "Inquiry", check: "CheckResult",
                 auto_send_conditions: dict) -> str:
        """
        戻り値: "auto_send" | "requires_confirmation" | "blocked"
        """
        # NG検出 → 問答無用でブロック
        if not check.is_clean:
            return "blocked"
        # 全体設定が確認ありの場合
        if not self._condition_allows_auto(inquiry, auto_send_conditions):
            return "requires_confirmation"
        return "auto_send"

    def is_business_hours(self) -> bool:
        now = datetime.now().time()
        return time(10, 0) <= now <= time(18, 0)
```

### Gitコミット（2件）

```
feat: email assembler merging fixed and AI-generated sections

- Section-by-section assembly: header, property block, CTA, signature
- Vacancy branch: available path vs unavailable+alternative path
- Fixed sections (company info, URLs, schedule template) never touch Claude
- Business hours flag switches scheduling vs next-business-day copy
```

```
feat: send gate evaluation and SMTP delivery with full logging

- SendGate.evaluate(): blocked / requires_confirmation / auto_send
- Default: all_require_confirmation=true blocks auto_send globally
- smtplib STARTTLS send with retry on transient SMTP errors
- Write send record to Sheets: timestamp, recipient, message_id, status
```

---

## Feature 7: メインオーケストレーター

### 実装内容

**`src/core/inquiry_processor.py`** — 全モジュールの接続:
```python
class InquiryProcessor:
    """
    ポーリングサイクルごとに呼ばれるメイン処理。
    未読メール1件ずつを以下の順序で処理する。
    """
    def process_one(self, raw_email: dict):
        # 1. 問い合わせレコード作成
        inquiry = self._parse_inquiry(raw_email)
        self._sheets.write_inquiry(inquiry)

        # 2. 返信検知チェック（既存追客の停止判定）
        if self._is_reply(raw_email):
            self._stop_followup(raw_email["in_reply_to"])
            return

        # 3. 受信メール本文のNGチェック
        check = self._checker.check(inquiry.raw_body)
        if not check.is_clean:
            self._sheets.update_status(inquiry.id, "NG検出", check)
            return

        # 4. 物件照合・空室判定
        inquiry.is_vacant, property_data = self._lookup_property(inquiry)

        # 5. AI文案生成
        draft = self._generate_draft(inquiry, property_data)

        # 6. AI生成文のNGチェック（2回目）
        draft_check = self._checker.check(draft)
        if not draft_check.is_clean:
            self._sheets.update_status(inquiry.id, "NG検出", draft_check)
            return

        inquiry.ai_draft = draft
        self._sheets.update_status(inquiry.id, "AI返信文生成済み")

        # 7. 送信ゲート評価
        gate = self._send_gate.evaluate(inquiry, draft_check, self._config.auto_send)
        if gate == "blocked":
            self._sheets.update_status(inquiry.id, "要確認")
        elif gate == "requires_confirmation":
            self._sheets.update_status(inquiry.id, "自動送信可")
            # 管理者確認待ち（Phase2管理画面から送信）
        elif gate == "auto_send":
            self._send_and_schedule(inquiry)
```

### Gitコミット（2件）

```
feat: main inquiry processor wiring all modules end-to-end

- InquiryProcessor.process_one() orchestrating all pipeline steps
- Reply detection stops follow-up sequence (in_reply_to matching)
- Dual NG check: incoming email body AND AI-generated draft
- Status transitions: 未対応→AI生成済み→要確認/自動送信可/送信済み
```

```
feat: polling entry point and Windows/Linux service runner

- poll_loop(): fetch unread, process each, sleep interval
- dotenv loader with validation for all required env keys
- Rotating log handler: mail_automation.log (10MB × 5 files)
- Graceful shutdown on SIGTERM for VPS process management
```

---

## Phase 1 完成時の動作確認チェックリスト

テスト用メールを以下のパターンで送信し、全件Sheetsに正しく記録されることを確認：

| # | テストシナリオ | 期待ステータス |
|---|--------------|--------------|
| 1 | 通常問い合わせ（空室あり） | `AI返信文生成済み` |
| 2 | 通常問い合わせ（空室なし） | `AI返信文生成済み`（代替物件付き） |
| 3 | 「クレーム」含むメール | `NG検出` |
| 4 | 「弁護士」含むメール | `NG検出` |
| 5 | 「外国人不可」含むメール | `NG検出`（差別表現） |
| 6 | 「外国籍の方もご相談」含むメール | `AI返信文生成済み`（通過） |
| 7 | AI生成文に「クレーム」が含まれた場合 | `NG検出` |
| 8 | 業務時間外（18時以降）送信 | `AI返信文生成済み`（翌営業日文面） |
| 9 | 自動送信ONの条件 | `送信済み`、Sheets送信履歴に記録 |
| 10 | 既存スレッドへの返信メール | 追客停止、`返信あり`に更新 |

---

## マイルストーン2（Phase 2）スコープ（参考）

Phase 1納品・動作確認後に着手：
- **Feature 8**: APScheduler追客スケジューラ（2日後・3日後）
- **Feature 9**: 返信検知による自動停止 + 手動停止API
- **Feature 10**: Flask管理画面（問い合わせ一覧・NGワードCRUD・テンプレート管理・送信操作）
- **Feature 11**: VPS本番デプロイ（systemdサービス化）
- **Feature 12**: 統合テスト・操作マニュアル・フロー図・納品

---

## セキュリティ注意事項

| 項目 | 対処 |
|------|------|
| Gmailアプリパスワード | `.env` にのみ記載。絶対にコミットしない |
| Claude APIキー | `.env` にのみ記載 |
| WP Application Password | `.env` にのみ記載 |
| Google Service Account JSON | `.gitignore` に追加済み |
| `.env` ファイル | `.gitignore` に追加済み |
| 管理画面 | IP制限（ADMIN_ALLOWED_IPS）で社内IPのみ許可 |
