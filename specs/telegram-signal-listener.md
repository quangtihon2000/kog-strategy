# Module: Telegram Signal Listener

> Module ingest signal từ các Telegram channel/group bằng user account (Telethon),
> parse thành signal chuẩn và đẩy vào Redis Streams cho downstream worker xử lý.

**Project:** `kog-strategy`
**Status:** Spec — chưa implement
**Last updated:** 2026-05-09

---

## 1. Mục tiêu & Phạm vi

### Goals
- Listen real-time message từ N signal channels qua **user account** (không phải bot).
- Parse signal entry sang format chuẩn `Signal` (Pydantic v2).
- Phân biệt **signal mới** vs **update** của signal cũ (TP hit, SL move, close, BE...).
- Validate signal trước khi vào queue (chặn signal rác/fat-finger).
- Push signal hợp lệ vào Redis Stream `signals:raw` cho downstream consumer.
- Observability đầy đủ để biết mỗi channel đang hoạt động ra sao.

### Non-goals
- ❌ Không tự execute order — việc của downstream service khác.
- ❌ Không backtest — module khác.
- ❌ Không OCR ảnh ở phase 1 (enqueue stream `signals:needs_ocr` xử lý sau).
- ❌ Không support Telegram Bot account (bot không join được channel public).

### Success criteria
| Metric | Target |
|---|---|
| p99 latency (admin post → Redis Stream) | < 1s |
| Parse rate trên signal đã biết format | > 90% |
| LLM cost (Tier 3) với 10 channels, 2000 msg/day | < $5/tháng |
| False negative (signal thật bị reject) | < 2% |
| Uptime | > 99% |

---

## 2. Tech Stack

Tuân thủ stack đã có của `kog-strategy`:

- **Python 3.11+**, package manager **uv**
- **Telethon** (MTProto user-client)
- **Redis 7+** (Streams + Hash cho stats)
- **PostgreSQL 15+** (lưu signal đã validated, history, channel config)
- **Pydantic v2 strict mode** (data models)
- **Celery** (downstream consumers — KHÔNG dùng trong listener service này)
- **structlog** (structured logging)
- **OpenTelemetry** (tracing — optional phase 2)
- **Docker** + **GitHub Actions** deploy lên self-hosted

LLM provider cho Tier 3: **Claude Haiku 4.5** (default) hoặc **Gemini 2.5 Flash** (fallback). Cấu hình qua env.

---

## 3. Kiến trúc

```
┌─────────────────────────────────────────────────────────────┐
│  telegram-listener (service riêng, container riêng)         │
│  ┌──────────────┐                                            │
│  │  Telethon    │  ←── persistent MTProto connection         │
│  │  Client      │      (1 connection cho TẤT CẢ channels)    │
│  └──────┬───────┘                                            │
│         │ NewMessage / MessageEdited events                  │
│         ▼                                                     │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Pipeline (4 tầng cascade reject)                     │   │
│  │                                                        │   │
│  │  Tier 0  → metadata filter (sender, media, reply)     │   │
│  │  Tier 1  → heuristic + anti-keyword                   │   │
│  │  Tier 2  → regex per-channel                          │   │
│  │  Tier 3  → LLM extractor (fallback)                   │   │
│  │  Tier 4  → validator (semantic gates)                 │   │
│  └──────┬───────────────────────────────────────────────┘   │
│         │                                                     │
└─────────┼─────────────────────────────────────────────────────┘
          ▼
   ┌─────────────────────┐
   │  Redis Streams      │
   │                     │
   │  signals:raw        │ ── signal mới hợp lệ
   │  signals:updates    │ ── update cho signal cũ
   │  signals:needs_ocr  │ ── ảnh không caption
   │  signals:rejected_sample │ ── audit (sample 0.5%)
   │  signals:unparsed   │ ── pass tier 1 nhưng tier 2+3 fail
   └─────────────────────┘
          │
          ▼
   Downstream: Celery workers (out of scope)
```

### Service boundary
- **`telegram-listener`** chạy độc lập, container riêng, **KHÔNG nằm trong FastAPI process**.
- Lý do: Telethon giữ persistent MTProto connection, không nên restart cùng API service.
- Communication với phần còn lại của `kog-strategy` chỉ qua Redis Streams + PostgreSQL.

---

## 4. Data Flow

```
Telegram         Telethon Client          Pipeline          Redis Streams
   │                   │                     │                   │
   │  push msg         │                     │                   │
   ├──────────────────>│                     │                   │
   │                   │  on_event           │                   │
   │                   ├────────────────────>│                   │
   │                   │                     │  T0 reject ──────────> drop
   │                   │                     │  T1 reject ──────────> rejected_sample (0.5%)
   │                   │                     │  T2 parse OK         │
   │                   │                     ├─────────────────────>│ signals:raw
   │                   │                     │  T2 fail → T3 LLM    │
   │                   │                     ├─────────────────────>│ signals:raw OR signals:unparsed
   │                   │                     │  Update detected     │
   │                   │                     ├─────────────────────>│ signals:updates
```

---

## 5. Component Specs

### 5.1 Telethon Listener Service

#### Responsibilities
- Maintain persistent MTProto connection.
- Subscribe `events.NewMessage` + `events.MessageEdited` cho whitelist channels.
- Pass mỗi event qua pipeline.
- Handle reconnection, FloodWait, session persistence.

#### Configuration (env vars)
```
TELEGRAM_API_ID=<int>
TELEGRAM_API_HASH=<str>
TELEGRAM_SESSION_NAME=kog_signals
TELEGRAM_SESSION_DIR=/data/sessions   # mount persistent volume
SIGNAL_CHANNELS_CONFIG=/etc/kog/channels.yaml
REDIS_URL=redis://redis:6379/0
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<str>
LOG_LEVEL=INFO
STARTUP_REPLAY_WINDOW_MIN=1   # bỏ qua tin cũ hơn N phút khi start
```

#### Skeleton
```python
import asyncio
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events
from redis.asyncio import Redis
import structlog

log = structlog.get_logger()

STARTUP_TIME = datetime.now(timezone.utc)

client = TelegramClient(
    f"{SESSION_DIR}/{SESSION_NAME}",
    API_ID, API_HASH,
    connection_retries=None,   # retry vô hạn
    auto_reconnect=True,
)
redis = Redis.from_url(REDIS_URL, decode_responses=True)

@client.on(events.NewMessage(chats=SIGNAL_CHANNEL_IDS))
async def on_new_message(event):
    # Skip replay khi vừa start
    if event.message.date < STARTUP_TIME - timedelta(minutes=STARTUP_REPLAY_WINDOW_MIN):
        return
    await pipeline.process(event, is_edit=False)

@client.on(events.MessageEdited(chats=SIGNAL_CHANNEL_IDS))
async def on_edit(event):
    await pipeline.process(event, is_edit=True)

async def main():
    await client.start()
    await pipeline.startup(client, redis)   # load admins, configs
    log.info("listener_started", channels=len(SIGNAL_CHANNEL_IDS))
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
```

#### Reliability requirements
- Auto-reconnect khi mất kết nối (Telethon hỗ trợ sẵn với `connection_retries=None`).
- Health check endpoint (HTTP `/health`) trả 200 nếu `client.is_connected()`.
- Graceful shutdown: bắt SIGTERM → `client.disconnect()` → flush Redis pending.

---

### 5.2 Tier 0: Metadata Filter

Reject **trước cả khi đọc text** dựa trên Telegram metadata.

#### Rules (theo thứ tự)
1. **Service messages** (join, pin, edit title...): drop.
2. **Empty text + photo**: enqueue `signals:needs_ocr`, return.
3. **Empty text + no media**: drop.
4. **Group chat**: chỉ accept nếu `sender_id ∈ CHANNEL_ADMINS[chat_id]`.
   - Broadcast channel: bỏ qua check này.
5. **Forwarded message**: drop trừ khi `ALLOW_FORWARD[chat_id] == True`.
6. **Reply to another message** (`reply_to_msg_id != None`): xem là update/Q&A → enqueue `signals:updates`, không xử lý như signal mới.

#### Admin loading
```python
async def load_channel_admins(client) -> dict[int, set[int]]:
    admins = {}
    for chan_id in SIGNAL_CHANNEL_IDS:
        try:
            entity = await client.get_entity(chan_id)
            if hasattr(entity, "broadcast") and entity.broadcast:
                admins[chan_id] = set()  # broadcast: không cần filter sender
            else:
                ps = await client.get_participants(
                    chan_id, filter=ChannelParticipantsAdmins
                )
                admins[chan_id] = {p.id for p in ps}
        except Exception as e:
            log.warning("load_admins_failed", chan_id=chan_id, error=str(e))
            admins[chan_id] = set()
    return admins
```
- Refresh **mỗi 6 giờ** qua background task (dùng `asyncio.create_task` với loop).

---

### 5.3 Tier 1: Heuristic Pre-filter

Reject 80%+ tin chat thường bằng keyword check rẻ.

#### Rules
```python
ANTI_HEAD = (
    "gm", "good morning", "chào sáng",
    "kết quả", "recap", "tuần qua", "hôm qua",
    "phân tích", "nhận định", "view tuần",
    "vip", "premium", "subscribe", "inbox", "liên hệ",
)

ANTI_ANYWHERE = (
    "tp1 hit", "tp2 hit", "tp3 hit", "sl hit",
    "closed", "đóng lệnh", "chốt lệnh",
    "move sl", "moved to", "breakeven", "be",
    "trailing", "cancel order", "hủy lệnh",
)

POSITIVE_SIDE = ("long", "short", "buy", "sell", "mua", "bán")
POSITIVE_PRICE_KW = ("entry", "sl", "tp", "stop", "target", "cắt lỗ", "mục tiêu")

def is_likely_signal(text: str) -> bool:
    t = text.lower()
    if any(kw in t[:40] for kw in ANTI_HEAD):
        return False
    if any(kw in t for kw in ANTI_ANYWHERE):
        return False
    if not (20 < len(text) < 1500):
        return False
    has_side = any(w in t for w in POSITIVE_SIDE)
    has_price_kw = any(w in t for w in POSITIVE_PRICE_KW)
    has_number = bool(re.search(r"\d{2,}", text))
    return has_side and has_price_kw and has_number
```

#### Output
- Pass → đi tiếp Tier 2.
- Fail → increment `stats:{chan_id}:rejected_t1`, sample 0.5% vào `signals:rejected_sample`.

---

### 5.4 Tier 2: Regex Parser (per-channel)

Mỗi channel 1 parser riêng, dispatch theo `chat_id`.

#### Strategy
- Field-by-field regex (KHÔNG viết 1 regex giant).
- Tolerant patterns: `\s*` thay vì `\s+`, optional separators `[:=\-]?`.
- Number normalization tách hàm riêng (`_norm_num`): handle `k`, `M` suffix, dấu phẩy ngàn.
- Try/except wrap để regex fail không crash service → return `None` → escalate Tier 3.

#### Example (Channel A — clean format)
```python
def parse_channel_a(text: str) -> ParsedSignalFields | None:
    side_m  = re.search(r"(LONG|SHORT)\s+(\w+)", text, re.I)
    entry_m = re.search(r"Entry[:=\-]?\s*([\d,.]+)", text, re.I)
    sl_m    = re.search(r"SL[:=\-]?\s*([\d,.]+)", text, re.I)
    tps     = re.findall(r"TP\d*[:=\-]?\s*([\d,.]+)", text, re.I)
    lev_m   = re.search(r"(?:Leverage|Lev|x)[:=\-]?\s*(\d+)", text, re.I)

    if not (side_m and entry_m and sl_m and tps):
        return None

    return ParsedSignalFields(
        symbol=side_m.group(2).upper(),
        side=side_m.group(1).upper(),
        entry=_norm_num(entry_m.group(1)),
        sl=_norm_num(sl_m.group(1)),
        tp=[_norm_num(x) for x in tps],
        leverage=int(lev_m.group(1)) if lev_m else None,
    )

def _norm_num(s: str) -> float:
    s = s.lower().replace(",", "").strip()
    if s.endswith("k"): return float(s[:-1]) * 1_000
    if s.endswith("m"): return float(s[:-1]) * 1_000_000
    return float(s)
```

#### Config-driven parser (preferred sau khi >5 channels)
Chuyển regex từ Python code sang YAML để hot-reload không cần redeploy:

```yaml
# /etc/kog/parsers.yaml
- channel_id: -1001234567890
  name: "Channel A Pro"
  patterns:
    side: '(LONG|SHORT)\s+(\w+)'
    entry: 'Entry[:=\-]?\s*([\d,.]+)'
    sl: 'SL[:=\-]?\s*([\d,.]+)'
    tp_all: 'TP\d*[:=\-]?\s*([\d,.]+)'
    leverage: 'Leverage[:=\-]?\s*(\d+)'
```
Generic parser đọc YAML, plug-and-play khi thêm channel hoặc admin đổi format.

#### Dispatcher
```python
PARSERS: dict[int, ChannelParser] = {}  # load từ YAML khi startup

def parse_tier2(channel_id: int, text: str) -> ParsedSignalFields | None:
    parser = PARSERS.get(channel_id)
    if not parser:
        return None
    try:
        return parser.parse(text)
    except (ValueError, AttributeError, IndexError) as e:
        log.debug("tier2_parse_error", chan=channel_id, error=str(e))
        return None
```

---

### 5.5 Tier 3: LLM Fallback Extractor

Chỉ fire khi Tier 1 pass nhưng Tier 2 return `None`.

#### Implementation
- Provider: Anthropic Claude Haiku 4.5 (default).
- **Structured output** với JSON schema (Pydantic).
- Cache theo `sha256(text)` trong Redis 24h (dedup tin lặp).
- Timeout 5s, max retries 2.
- Cost target: < $0.001/call.

#### Prompt template
```python
SCHEMA = {
    "type": "object",
    "properties": {
        "is_signal": {"type": "boolean"},
        "symbol": {"type": "string"},
        "side": {"type": "string", "enum": ["LONG", "SHORT"]},
        "entry": {"oneOf": [
            {"type": "number"},
            {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
        ]},
        "sl": {"type": "number"},
        "tp": {"type": "array", "items": {"type": "number"}, "minItems": 1},
        "leverage": {"type": ["integer", "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["is_signal"],
}

PROMPT = """You extract trading signals from messages. Return JSON only.

Rules:
- If message is NOT a new entry signal (chat, news, position update, recap), return {"is_signal": false}.
- If it IS a new entry signal, extract symbol, side, entry, sl, tp, leverage.
- entry can be a number OR a [low, high] zone.
- tp is always an array (1-5 items).
- confidence: 0.0-1.0, your certainty this is a real signal.

Message:
\"\"\"
{text}
\"\"\"
"""
```

#### Behavior
- `is_signal: false` → drop, increment `stats:{chan}:llm_not_signal`.
- `is_signal: true` + `confidence >= 0.7` → đi tiếp Tier 4.
- `is_signal: true` + `confidence < 0.7` → enqueue `signals:unparsed` cho human review.

---

### 5.6 Tier 4: Validator

Gate cuối, **quan trọng nhất**. Chặn signal rác / parse sai trước khi vào pipeline thật.

#### Validation rules
```python
async def validate(s: ParsedSignalFields) -> ValidationResult:
    # 1. Direction logic
    if s.side == "LONG":
        entry_low = s.entry if isinstance(s.entry, float) else min(s.entry)
        if not (s.sl < entry_low < min(s.tp)):
            return ValidationResult(False, "long_levels_inverted")
    else:  # SHORT
        entry_high = s.entry if isinstance(s.entry, float) else max(s.entry)
        if not (max(s.tp) < entry_high < s.sl):
            return ValidationResult(False, "short_levels_inverted")

    # 2. Symbol whitelist (cache list từ exchange)
    if s.symbol not in await get_known_symbols():
        return ValidationResult(False, f"unknown_symbol_{s.symbol}")

    # 3. Entry ≤ 5% lệch market price (chống parse nhầm số)
    market = await get_market_price(s.symbol)   # cached 30s
    entry_mid = s.entry if isinstance(s.entry, float) else sum(s.entry) / 2
    if abs(entry_mid - market) / market > 0.05:
        return ValidationResult(False, f"entry_too_far_{(entry_mid-market)/market:.2%}")

    # 4. SL distance hợp lý (0.1% - 20%)
    sl_pct = abs(entry_mid - s.sl) / entry_mid
    if not (0.001 < sl_pct < 0.20):
        return ValidationResult(False, f"unrealistic_sl_{sl_pct:.2%}")

    # 5. R:R tối thiểu 0.5
    rr = abs(s.tp[0] - entry_mid) / abs(entry_mid - s.sl)
    if rr < 0.5:
        return ValidationResult(False, f"poor_rr_{rr:.2f}")

    # 6. Leverage hợp lý (nếu có)
    if s.leverage is not None and not (1 <= s.leverage <= 125):
        return ValidationResult(False, f"invalid_leverage_{s.leverage}")

    return ValidationResult(True, "ok")
```

#### Symbol & price source
- `get_known_symbols()`: đọc từ Postgres table `exchange_symbols`, refresh mỗi 1h từ Binance/HOSE API.
- `get_market_price(symbol)`: Redis cache 30s, miss thì fetch exchange.

---

### 5.7 Update Detector

Phân biệt **signal mới** vs **update của signal cũ** cùng-channel.

#### Detection logic (theo thứ tự)
1. **Reply to message**: nếu `msg.reply_to_msg_id` matches signal đã lưu → update của signal đó.
2. **Edit event**: `MessageEdited` với `message_id` đã thấy → update.
3. **Anti-keyword match** (`ANTI_ANYWHERE` ở Tier 1): xếp loại update.

#### Storage
```python
# Postgres table
class SignalMessage:
    id: UUID  # internal signal_id
    channel_id: int
    message_id: int   # Telegram message id
    received_at: datetime
    raw_text: str
    parsed_fields: dict   # JSONB
    status: str  # OPEN | TP1_HIT | TP2_HIT | SL_HIT | CLOSED | CANCELLED
    
    UNIQUE(channel_id, message_id)
```

```python
# Redis: lookup map cho update detection (TTL 7 ngày)
# Key: signal:msg_id:{channel_id}:{message_id}
# Value: signal_id (UUID string)
```

#### Update event format vào `signals:updates`
```json
{
  "signal_id": "uuid",
  "channel_id": -1001234567890,
  "update_type": "TP_HIT" | "SL_HIT" | "MOVE_SL" | "CLOSE" | "EDIT" | "OTHER",
  "raw_text": "...",
  "received_at": "2026-05-09T10:23:45Z"
}
```

Downstream worker (out of scope) đọc stream này để cập nhật state machine của signal.

---

## 6. Data Models (Pydantic v2)

```python
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Literal

class ParsedSignalFields(BaseModel):
    """Output của Tier 2/3, input của Tier 4."""
    model_config = ConfigDict(strict=True, frozen=True)
    
    symbol: str
    side: Literal["LONG", "SHORT"]
    entry: float | tuple[float, float]
    sl: float
    tp: list[float] = Field(min_length=1, max_length=5)
    leverage: int | None = None
    confidence: float = 1.0   # 1.0 cho regex, < 1.0 cho LLM

class Signal(BaseModel):
    """Signal đã validated, sẵn sàng push vào signals:raw."""
    model_config = ConfigDict(strict=True, frozen=True)
    
    signal_id: str   # UUID
    channel_id: int
    channel_name: str
    message_id: int
    received_at: datetime
    
    # Parsed fields
    symbol: str
    side: Literal["LONG", "SHORT"]
    entry: float | tuple[float, float]
    sl: float
    tp: list[float]
    leverage: int | None
    
    # Provenance
    raw_text: str
    parsed_by: Literal["regex", "llm"]
    confidence: float
    
class ValidationResult(BaseModel):
    ok: bool
    reason: str
```

---

## 7. Redis Schema

### Streams
| Stream | Purpose | Maxlen | TTL |
|---|---|---|---|
| `signals:raw` | Signal mới hợp lệ | 50,000 | – |
| `signals:updates` | Update của signal cũ | 50,000 | – |
| `signals:needs_ocr` | Ảnh không caption | 5,000 | – |
| `signals:unparsed` | Tier 1 pass, Tier 2+3 fail | 5,000 | – |
| `signals:rejected_sample` | Audit sample 0.5% | 1,000 | – |

Dùng `XADD ... MAXLEN ~ N` (approximate trim) để Redis không trim mỗi lần ghi.

### Keys
| Key | Type | Purpose | TTL |
|---|---|---|---|
| `stats:{chan_id}` | Hash | Counter (received, parsed_t2, parsed_t3, validated, rejected_*) | – |
| `signal:msg:{chan_id}:{msg_id}` | String | Lookup signal_id từ Telegram message_id | 7d |
| `llm_cache:{sha256}` | String | Cache LLM extraction result | 24h |
| `market_price:{symbol}` | String | Cached price | 30s |
| `known_symbols` | Set | Whitelist symbol | 1h |

---

## 8. Configuration: `channels.yaml`

```yaml
# Single source of truth cho channel config
channels:
  - id: -1001234567890
    name: "Channel A Pro"
    enabled: true
    type: "broadcast"   # broadcast | group
    allow_forward: false
    parser: "channel_a"   # tham chiếu PARSERS dict
    notes: "Format clean, regex chính"

  - id: -1001111111111
    name: "Channel B Free"
    enabled: true
    type: "group"
    allow_forward: false
    parser: "channel_b"
    admin_user_ids: [12345, 67890]   # override nếu API call get_participants fail
    notes: "Admin viết tay, regex linh hoạt + LLM fallback nhiều"

  - id: -1002222222222
    name: "Channel VN Stocks"
    enabled: false   # tạm tắt để debug
    type: "broadcast"
    parser: "channel_vn_stocks"

llm:
  provider: "anthropic"
  model: "claude-haiku-4-5-20251001"
  timeout_s: 5
  max_retries: 2
  confidence_threshold: 0.7

filtering:
  startup_replay_window_min: 1
  rejected_sample_rate: 0.005   # 0.5%
```

---

## 9. Observability

### Metrics (Prometheus format, qua endpoint `/metrics`)
- `tg_listener_messages_received_total{channel}` (counter)
- `tg_listener_messages_rejected_total{channel,tier,reason}` (counter)
- `tg_listener_messages_parsed_total{channel,parser}` (counter; parser = regex | llm)
- `tg_listener_validation_failed_total{channel,reason}` (counter)
- `tg_listener_signals_emitted_total{channel}` (counter)
- `tg_listener_pipeline_duration_seconds{tier}` (histogram)
- `tg_listener_llm_calls_total{result}` (counter; result = ok | error | timeout)
- `tg_listener_llm_cost_usd_total` (counter)
- `tg_listener_active_channels` (gauge)
- `tg_listener_telegram_connected` (gauge, 0 | 1)

### Structured logs (structlog → JSON)
Mỗi event log:
```json
{
  "ts": "2026-05-09T10:23:45.123Z",
  "level": "info",
  "event": "signal_emitted",
  "channel_id": -1001234567890,
  "channel_name": "Channel A Pro",
  "message_id": 5421,
  "signal_id": "uuid",
  "symbol": "BTCUSDT",
  "side": "LONG",
  "parsed_by": "regex",
  "duration_ms": 1.4
}
```

### Grafana dashboard panels (must-have)
1. Messages/min per channel (stacked).
2. Parse rate per channel (% parsed_t2 / received) — alert khi tụt > 30% trong 1h.
3. LLM cost cumulative.
4. Validation reject reasons (top 10 stacked).
5. p50/p95/p99 pipeline latency.
6. Active channels + connection status.

### Alerts
- 🔴 `telegram_disconnected_5m`: client mất kết nối > 5 phút.
- 🟡 `parse_rate_dropped`: parse rate channel < 50% trong 2h.
- 🟡 `llm_cost_spike`: LLM cost > $1/giờ.
- 🔴 `pipeline_p99_high`: p99 latency > 5s trong 10 phút.

---

## 10. Audit Loop (chống filter drift)

Sample 0.5% tin reject ở mỗi tier vào `signals:rejected_sample`:

```python
import random

async def maybe_sample(event, text: str, tier: str, reason: str):
    if random.random() < 0.005:
        await redis.xadd(
            "signals:rejected_sample",
            {
                "channel_id": str(event.chat_id),
                "message_id": str(event.message.id),
                "text": text[:500],
                "tier": tier,
                "reason": reason,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
            maxlen=1000,
            approximate=True,
        )
```

### Weekly review process
1. `XRANGE signals:rejected_sample - +` → lấy 50 tin random.
2. Review thủ công (human-in-loop):
   - Có signal thật bị reject? → relax filter / sửa anti-keyword / thêm parser.
   - Pure noise? → filter ok.
3. Update `channels.yaml` hoặc `ANTI_*` lists.
4. Hot-reload config (không cần restart).

---

## 11. Operational Concerns

### Session management
- Session file `*.session` = full quyền account → treat như password.
- Lưu trên persistent volume mount `/data/sessions`.
- **KHÔNG commit vào git, KHÔNG copy ra log.**
- Backup encrypted (age/sops).
- Mất session = phải login lại bằng OTP → ngừng service vài phút.

### Deployment
- Single-replica (không scale horizontally — Telethon session không share được).
- Liveness probe: HTTP `GET /health` → 200 nếu `client.is_connected()`.
- Readiness probe: cùng endpoint, thêm check Redis ping.
- Restart policy: `unless-stopped`.
- Resource: 0.5 vCPU, 256MB RAM đủ với <50 channels.

### Rate limits
- **Receive**: passive listening không bị limit. Subscribe nhiều channels OK.
- **API calls** (`get_participants`, `get_entity`...): cẩn thận, có thể bị FloodWait. Cache aggressively, retry với exponential backoff.

### Failure modes
| Failure | Detection | Recovery |
|---|---|---|
| Telegram disconnect | `client.is_connected() == False` | auto-reconnect (Telethon built-in) |
| Redis down | `redis.ping()` fail | local in-memory buffer 1000 events, replay khi reconnect |
| LLM provider down | timeout | enqueue `signals:unparsed`, không block pipeline |
| Session corrupt | `SessionPasswordNeededError` | alert, manual re-login |
| Channel admin đổi format | parse rate drop | alert + audit review |

---

## 12. Testing Strategy

### Unit tests
- `tests/parsers/` — mỗi channel parser có 1 file test với 20-50 fixture messages thật (anonymized).
- `tests/test_validator.py` — coverage hết các validation rule.
- `tests/test_tier1_heuristic.py` — corpus mixed signal + chat.

### Integration tests
- `tests/integration/test_pipeline.py` — mock Telethon event → assert Redis Stream output.
- Dùng `fakeredis` cho Redis layer.
- Mock LLM với fixed responses.

### Replay test (regression suite)
Lưu 500-1000 tin lịch sử (anonymized) vào `tests/fixtures/historical_messages.jsonl`. Chạy qua pipeline mới → so kết quả với baseline. Bất kỳ thay đổi nào (regex, anti-keyword, validator) phải pass replay test.

```bash
uv run pytest tests/regression/test_replay.py -v
```

### Load test (phase 2)
- Simulate 100 msg/s qua mock event injector.
- Assert p99 latency < 1s, no OOM, no event loss.

---

## 13. Edge Cases & Gotchas

### Đã biết
1. **Startup replay**: lần đầu start, Telethon flush missed updates → filter theo `STARTUP_TIME` (đã handle ở 5.1).
2. **Edited messages**: signal hay được edit để thêm TP. Bắt `MessageEdited`, dedupe theo `(channel_id, message_id)`.
3. **Channel với caption + ảnh**: caption thường có signal đầy đủ, parse như text bình thường.
4. **Pure image signal**: enqueue `signals:needs_ocr`, không xử lý phase 1.
5. **Multi-language mix**: regex Vietnamese keywords + English keywords cùng lúc (`mua`, `bán`, `cắt lỗ` + `entry`, `sl`, `tp`).
6. **Number format**: `70k`, `1.5M`, `67,500`, `67.500` (EU style — RARE, Quang's case không gặp), `67500` — hàm `_norm_num` phải handle hết.
7. **Zone entry**: `entry 67400-67700` → tuple, downstream phải handle.
8. **Multiple TPs**: list, không phải single value.
9. **Channel đổi format đột ngột**: parse rate tụt → alert → review audit sample → update parser.
10. **Bot post trong channel**: một số channel dùng bot post template — accept (`sender.bot == True` OK).
11. **Forwarded signals từ channel khác**: tùy `allow_forward` config.
12. **FloodWait khi gọi `get_participants`**: cache 6h, retry với backoff.

### Chưa biết / phase 2
- Channel post bằng image only + signal viết trên ảnh → cần OCR pipeline riêng.
- Channel dùng inline keyboard (button) thay vì text → ít gặp với signal channel.
- Voice message signal → ignore.

---

## 14. Implementation Phases

### Phase 1 (MVP) — 1-2 tuần
- [ ] Setup Telethon listener service + Docker container.
- [ ] Implement Tier 0 + Tier 1.
- [ ] Implement Tier 2 cho 2-3 channels đầu (hard-coded parsers).
- [ ] Implement Tier 4 validator.
- [ ] Redis Streams output (`signals:raw`, `signals:updates`).
- [ ] Basic Prometheus metrics.
- [ ] Unit tests cho parsers.

### Phase 2 — 1 tuần
- [ ] Tier 3 LLM fallback (Claude Haiku).
- [ ] Config-driven parsers (YAML).
- [ ] Update detector full (reply matching + state).
- [ ] Audit sampling + weekly review tool.
- [ ] Grafana dashboard.

### Phase 3 — nice to have
- [ ] OCR pipeline cho image-only signals.
- [ ] Hot-reload `channels.yaml` không cần restart.
- [ ] Multi-region failover (2 listener instances với session khác nhau, 1 active 1 standby).
- [ ] Web UI để review `signals:rejected_sample` và `signals:unparsed`.

---

## 15. Open Questions

- [ ] Có cần persist raw text của TẤT CẢ message (kể cả reject) vào Postgres để debug lâu dài không? (Storage cost vs debugging value)
- [ ] Tier 3 LLM: dùng Claude Haiku hay tự host model nhỏ (Qwen 7B)? Trade-off cost vs latency vs privacy.
- [ ] Channel dùng custom emoji premium làm phân cách field — regex có cần handle Unicode emoji range không?
- [ ] Cần webhook notify Quang khi có signal entry không (qua bot Telegram khác)?
- [ ] Multi-account support (2 user account để failover) — phase nào?

---

## 16. References & Conventions

Tuân thủ convention chung của `kog-strategy`:
- **Architecture**: Router → Service → Repository (FastAPI side); listener service này chỉ có Service + Repository.
- **Async-first**: tất cả I/O dùng `async def`.
- **Pydantic v2 strict mode**: `ConfigDict(strict=True)` cho mọi model.
- **Redis cache key naming**: `{domain}:{entity}:{id}` (xem CLAUDE.md root).
- **Logging**: structlog JSON output, KHÔNG dùng `print` hoặc `logging.info` trực tiếp.
- **Error handling**: domain exceptions tách riêng module `exceptions.py`, không raise generic `Exception`.

### Useful Telethon docs
- Events: https://docs.telethon.dev/en/stable/quick-references/events-reference.html
- Sessions: https://docs.telethon.dev/en/stable/concepts/sessions.html
- FloodWait handling: https://docs.telethon.dev/en/stable/concepts/errors.html

---

**End of spec.**
