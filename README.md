# KOG Strategy — Zone Signal System

A two-part trading automation system:

1. **`ZoneSignalEA.mq5`** — MetaTrader 5 Expert Advisor that monitors a JSON signal file and enters trades on M15 breakouts of a zone.
2. **`zone_signal_agent/`** — Python agent that reads zone signals from a Redis Stream and writes them atomically to per-account JSON files.

---

## Architecture

```
[Any producer]
      │
      │  XADD zone_signals  (Redis Stream)
      ▼
[Python agent — zone_signal_agent/main.py]
      │
      │  atomic rename  ({account}.tmp → {account}.json)
      │
      ├── 5100000.json
      ├── 5100001.json
      └── 5100002.json
            │
            ▼
       MT5 EA (ZoneSignalEA.mq5)
       — reads its own account's file
       — detects new signal by comparing timestamp
       — enters BUY/SELL on M15 bar close outside the zone
```

### File locking approach

The Python agent writes to a `.tmp` file then calls `os.replace(tmp, target)`.  
On both POSIX and NTFS this rename is **atomic at the OS level**, so the EA always reads either the complete old file or the complete new file — never a partial write. No separate lock file or status field is needed.

---

## JSON Signal Format

```json
{
  "timestamp":     1713259200,
  "symbol":        "XAUUSD",
  "redbox_upper":  2350.0,
  "redbox_lower":  2340.0,
  "targets_above": [2360.0, 2370.0],
  "targets_below": [2330.0, 2320.0]
}
```

| Field | Type | Description |
|---|---|---|
| `timestamp` | Unix seconds | Written by the Python agent at write time; EA detects a new signal when this value changes |
| `symbol` | string | Trading symbol (informational; EA trades on the chart symbol) |
| `redbox_upper` | float | Top of the zone |
| `redbox_lower` | float | Bottom of the zone |
| `targets_above` | float[] | Take-profit levels for BUY trades (one position per target) |
| `targets_below` | float[] | Take-profit levels for SELL trades (one position per target) |

---

## ZoneSignalEA.mq5

### How it works

| Event | Action |
|---|---|
| Every second (via `OnTick`) | Reads `{AccountLogin}.json`; if `timestamp` changed → loads the new signal |
| New M15 bar close **above** zone | Opens one BUY per `targets_above` entry; SL below `redbox_lower` |
| New M15 bar close **below** zone | Opens one SELL per `targets_below` entry; SL above `redbox_upper` |
| All positions closed (SL or TP) | Deactivates signal — no new trades until the file is updated |

### Input parameters

| Parameter | Default | Description |
|---|---|---|
| `InpLotPerTarget` | `0.01` | Lot size for each individual position |
| `InpSlBufferPts` | `50` | Extra points added to SL beyond the zone edge |
| `InpMagic` | `20240416` | Magic number to identify EA orders |
| `InpUseCommonDir` | `true` | Read from MT5 Common Files folder; set `false` for local MQL5/Files |

### Signal file location

The EA automatically determines its filename from the logged-in account number:

```
Account 5100000  →  reads  5100000.json
Account 5100001  →  reads  5100001.json
```

Place the JSON files in:
- **Common Files** (default, `InpUseCommonDir = true`):  
  `%AppData%\MetaQuotes\Terminal\Common\Files\`
- **Local Files** (`InpUseCommonDir = false`):  
  `<MT5 data folder>\MQL5\Files\`

### Signal lifecycle

```
Python writes file  →  EA reads new timestamp  →  EA enters trades
                                                         │
                                              SL or TP hit on all positions
                                                         │
                                              g_sig.valid = false
                                              (no more entries until new timestamp)
```

---

## Python Agent — `zone_signal_agent/`

### Project structure

```
zone_signal_agent/
├── .env.example        # environment variable reference
├── requirements.txt
├── config.py           # Settings dataclass loaded from .env
├── models.py           # ZoneSignal dataclass + validate() + to_json()
├── signal_writer.py    # Atomic per-account file writer
├── redis_consumer.py   # Redis Stream reader (extensible backend)
└── main.py             # Fan-out loop
```

### Setup

```bash
cd zone_signal_agent
pip install -r requirements.txt

cp .env.example .env
# edit .env — set MT5_SIGNAL_DIR and MT5_ACCOUNTS at minimum
```

### Configuration (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `MT5_SIGNAL_DIR` | yes | — | Absolute path to the directory where `{account}.json` files are written |
| `MT5_ACCOUNTS` | yes | — | Comma-separated MT5 account numbers, e.g. `5100000,5100001` |
| `REDIS_URL` | no | `redis://localhost:6379` | Redis connection URL |
| `REDIS_STREAM` | no | `zone_signals` | Redis Stream key to consume |
| `REDIS_GROUP` | no | `ea_writer` | Consumer group name |
| `REDIS_CONSUMER` | no | `agent-1` | Consumer name (change if running multiple instances) |
| `LOG_LEVEL` | no | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Running

```bash
# Ensure Redis is running
redis-server

# Start the agent
python main.py
```

---

## Producer — Pushing Signals to Redis

The producer sends a Redis Stream entry (`XADD`) with **flat string fields**:

| Field | Type | Example |
|---|---|---|
| `symbol` | string | `XAUUSD` |
| `redbox_upper` | string (float) | `2350.00` |
| `redbox_lower` | string (float) | `2340.00` |
| `targets_above` | comma-separated floats | `2360.0,2370.0` |
| `targets_below` | comma-separated floats | `2330.0,2320.0` |

> **Note:** `timestamp` is NOT included by the producer. The agent stamps it at write time so every file always reflects when it was last written.

### redis-cli example

```bash
redis-cli XADD zone_signals '*' \
  symbol        XAUUSD \
  redbox_upper  2350.00 \
  redbox_lower  2340.00 \
  targets_above "2360.0,2370.0" \
  targets_below "2330.0,2320.0"
```

### Python producer example

```python
import redis

r = redis.from_url("redis://localhost:6379")
r.xadd("zone_signals", {
    "symbol":        "XAUUSD",
    "redbox_upper":  "2350.00",
    "redbox_lower":  "2340.00",
    "targets_above": "2360.0,2370.0",
    "targets_below": "2330.0,2320.0",
})
```

---

## Verification

### 1. Test the Python agent end-to-end

```bash
# Start agent writing to a temp directory
MT5_SIGNAL_DIR=/tmp/test_sigs MT5_ACCOUNTS=5100000,5100001 python main.py
```

```bash
# In another terminal — push a test signal
redis-cli XADD zone_signals '*' \
  symbol XAUUSD redbox_upper 2350 redbox_lower 2340 \
  targets_above "2360,2370" targets_below "2330,2320"
```

Expected result: `/tmp/test_sigs/5100000.json` and `/tmp/test_sigs/5100001.json` both created with the correct zone data and a fresh `timestamp`.

```bash
# Push again — confirm timestamp updates and old file is replaced cleanly
redis-cli XADD zone_signals '*' \
  symbol XAUUSD redbox_upper 2352 redbox_lower 2342 \
  targets_above "2362,2372" targets_below "2332,2322"
```

### 2. Test the EA in MT5

1. Place the correct `{account}.json` in the MT5 Files directory.
2. Attach `ZoneSignalEA.mq5` to an **M15 XAUUSD** chart logged into the matching account.
3. Check the Experts log — you should see:
   ```
   [ZoneSignalEA] Signal file: 5100000.json
   [Signal] Applied — Zone 2340.00000 – 2350.00000 | Targets above: 2 | below: 2
   ```
4. When price closes above `redbox_upper` on an M15 bar:
   ```
   [Signal] Close ABOVE zone → opening BUY positions
   [BUY #1] entry=2351.00  sl=2339.50  tp=2360.00  Opened
   [BUY #2] entry=2351.00  sl=2339.50  tp=2370.00  Opened
   ```
5. Update the JSON file (new timestamp) — confirm the EA loads it within one second:
   ```
   [Signal] Applied — Zone ...
   ```

---

## Extending the Message Backend

To swap Redis Streams for a different queue (Kafka, RabbitMQ, SQS, simple BRPOP), only `redis_consumer.py` needs to change — specifically the internals of `consume_one()` and `ack()`. The rest of the system (`models.py`, `signal_writer.py`, `main.py`) is queue-agnostic.

**BRPOP drop-in** (simpler, no consumer groups):

```python
def consume_one(self, block_ms=5000):
    result = self._r.brpop(self._stream, timeout=block_ms // 1000)
    if result is None:
        return None
    _, raw = result          # raw is a JSON string from the producer
    import json
    return "brpop", json.loads(raw)

def ack(self, msg_id):
    pass   # BRPOP is fire-and-forget — no ACK needed
```
