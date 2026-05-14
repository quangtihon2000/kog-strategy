# 05 — Statistics service: per-account aggregations

## Context

`services/strategy-stats/` đã có aggregation per-account cho **Zone** (`zone_account.html`, `aggregate_by_account` trong `app/stats/zone.py`). Conde và Gvfx **chưa có** — overview chỉ chia theo channel (Conde) hoặc symbol (Gvfx).

Khi vận hành nhiều account cùng strategy, dashboard cần báo cáo P&L per-account cho cả 3 chiến lược (đặc biệt khi mỗi account có magic riêng → outcomes phân biệt được trên Redis stream).

## Tin tốt: schema đã sẵn sàng

Cả 3 outcome tables đã có column `account: BigInteger` + index `ix_*_outcomes_account_closed`:

- `services/strategy-stats/app/models.py:79` — `zone_outcomes.account` + index
- `services/strategy-stats/app/models.py:130` — `conde_outcomes.account` + index
- `services/strategy-stats/app/models.py:180` — `gvfx_outcomes.account` + index

EA outcome writer cũng đã emit field `account` vào Redis streams khi đóng position.

→ **Không cần migration schema.** Chỉ cần thêm 1 index composite cho hot query path (xem D bên dưới).

## A. `app/stats/conde.py` — aggregate_by_account

Bên cạnh aggregate per-channel hiện tại, thêm:

```python
from dataclasses import dataclass, field

@dataclass
class CondeAccountChannelBucket:
    account: int
    channel_id: int | None
    channel_name: str
    n_signals: int = 0
    n_positions: int = 0
    total_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    # reuse classify_outcome fields nếu cần thêm

@dataclass
class CondeAccountSummary:
    account: int
    n_positions: int = 0
    total_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    channels: dict[int | None, CondeAccountChannelBucket] = field(default_factory=dict)


async def aggregate_by_account(
    session: AsyncSession,
    since_epoch: int,
    account: int | None = None,
) -> dict[int, CondeAccountSummary]:
    """Aggregate outcomes by account (top) → channel (drilldown).

    - account=None → all accounts (overview card).
    - account=N → single account (detail page).
    """
    stmt = (
        select(CondeOutcome, CondeSignal)
        .join(CondeSignal, CondeOutcome.signal_id == CondeSignal.id, isouter=True)
        .where(CondeOutcome.closed_ts >= since_epoch)
    )
    if account is not None:
        stmt = stmt.where(CondeOutcome.account == account)

    result: dict[int, CondeAccountSummary] = {}
    rows = (await session.execute(stmt)).all()
    for outcome, signal in rows:
        acc = outcome.account
        if acc is None: continue
        summary = result.setdefault(acc, CondeAccountSummary(account=acc))

        ch_id   = signal.channel_id   if signal else None
        ch_name = signal.channel_name if signal else "(unknown)"
        bucket  = summary.channels.setdefault(
            ch_id, CondeAccountChannelBucket(account=acc, channel_id=ch_id, channel_name=ch_name)
        )

        outcome_class = classify_outcome(outcome)   # đã có sẵn
        bucket.n_positions += 1
        bucket.total_pnl   += outcome.pnl or 0.0
        summary.n_positions += 1
        summary.total_pnl   += outcome.pnl or 0.0
        if outcome_class == "win":       bucket.wins      += 1; summary.wins      += 1
        elif outcome_class == "loss":    bucket.losses    += 1; summary.losses    += 1
        elif outcome_class == "be":      bucket.breakeven += 1; summary.breakeven += 1

    return result
```

Reuse `classify_outcome` / `classify_signal` từ `strategies/conde_auto_entry/agent/stats.py` (đã có) thay vì viết lại.

## B. `app/stats/gvfx.py` — aggregate_by_account

Mirror cấu trúc Zone (`ZoneAccountSummary`) để template tái sử dụng được macro:

```python
@dataclass
class GvfxAccountSymbolBucket:
    account: int
    symbol: str
    mode_tag: str   # "grid" | "dca" | ...
    n_positions: int = 0
    total_pnl: float = 0.0

@dataclass
class GvfxAccountSummary:
    account: int
    n_positions: int = 0
    total_pnl: float = 0.0
    by_symbol: dict[tuple[str, str], GvfxAccountSymbolBucket] = field(default_factory=dict)


async def aggregate_by_account(
    session: AsyncSession,
    since_epoch: int,
    account: int | None = None,
) -> dict[int, GvfxAccountSummary]:
    ...
```

Drilldown key: `(symbol, mode_tag)` — gvfx có grid/dca tag từ signal.

## C. Routes & templates

### Conde

`services/strategy-stats/app/web/routers/conde.py` thêm:

```python
@router.get("/conde/account/{account}", response_class=HTMLResponse)
async def conde_account_detail(account: int, since: str = "7d", request: Request = ...):
    since_epoch = parse_since(since)
    async with session_scope() as s:
        summaries = await aggregate_by_account(s, since_epoch, account=account)
    summary = summaries.get(account)
    if summary is None:
        raise HTTPException(404)
    return templates.TemplateResponse("conde_account.html", {
        "request": request, "summary": summary, "since": since,
    })
```

Trang overview `/conde/` thêm card "Top accounts by n_positions" — query `aggregate_by_account(account=None)`, sort desc, top 10. Mỗi row deep-link `/conde/account/{account}`.

### Gvfx

Same pattern: `GET /gvfx/account/{account}` + card trên `/gvfx/`.

### Templates

**Mới**: `app/web/templates/conde_account.html`, `gvfx_account.html` — clone từ `zone_account.html`, đổi:

| Template | Header columns | Drilldown |
|---|---|---|
| `zone_account.html` (đã có) | Target above/below, n_signals, win/loss | per-redbox bucket |
| `conde_account.html` (mới) | Channel, direction, n_signals, n_positions, total_pnl | per-channel bucket |
| `gvfx_account.html` (mới) | Symbol, mode (grid/dca), n_positions, total_pnl | per-symbol bucket |

Macro chung trong `_macros.html` cho card "Account summary" + table P&L by drilldown → tái sử dụng giữa 3 template.

## D. Index migration

`services/strategy-stats/alembic/versions/0002_account_dimension.py`:

```python
def upgrade():
    op.create_index(
        "ix_conde_outcomes_account_signal",
        "conde_outcomes",
        ["account", "signal_ts"],
    )
    op.create_index(
        "ix_gvfx_outcomes_account_signal",
        "gvfx_outcomes",
        ["account", "signal_ts"],
    )

def downgrade():
    op.drop_index("ix_gvfx_outcomes_account_signal", table_name="gvfx_outcomes")
    op.drop_index("ix_conde_outcomes_account_signal", table_name="conde_outcomes")
```

Zone đã có index tương đương (`ix_zone_outcomes_account_closed`), không đụng.

Mục đích: tăng tốc query `WHERE account = ? AND closed_ts >= ?` (hot path của `aggregate_by_account` per-account).

## E. Triển khai độc lập

Stats service deploy qua workflow riêng `.github/workflows/strategy-stats-deploy.yml` (đã có), không liên quan `deploy.yml` của EA. Vì vậy có thể merge phần stats trước phần EA — không ảnh hưởng trading.

Order recommended:

1. Migration `0002_account_dimension.py` → review SQL → apply via alembic upgrade trên prod DB.
2. Code aggregations + routes + templates → unit test với fixture data.
3. Deploy stats service → smoke test `/conde/account/{acc}` với data outcomes thật.
4. (Sau đó mới đụng phần EA + CD pipeline.)

## F. Backward-compat data

Outcomes cũ trước khi có `account` column (nếu có) → `account IS NULL`. Aggregation skip rows này (đã filter `if acc is None: continue`). Không hiển thị trên dashboard per-account, vẫn hiển thị trên overview (không cần account dimension).

## G. Verification

```bash
# Sau khi deploy
curl https://stats.example.com/conde/account/5100000?since=7d | head -30
# → render bucket per-channel cho account 5100000 trong 7 ngày gần nhất

curl https://stats.example.com/gvfx/account/5200001?since=24h
# → render bucket per-(symbol, mode) cho account 5200001 trong 24h

# Overview phải có card "Top accounts"
curl https://stats.example.com/conde/ | grep "Top accounts"
```

Có thể compare totals với Zone hiện tại (đã verify đúng) để spot-check logic.
