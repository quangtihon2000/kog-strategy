# CLAUDE.md — GVFX Signal Strategy

## Overview

Grid DCA strategy: từ một "target price" với hướng (BUY/SELL), EA liên tục mở lệnh market miễn là (1) chưa chạm target và (2) trong phạm vi ±`step` points quanh giá hiện tại chưa có vị thế nào của EA. Tối đa 20 vị thế đồng thời, mỗi lệnh có TP cố định và hard SL 10000 points. Khi giá chạm target → signal inactive (positions đang mở vẫn chạy theo TP/SL, không vào thêm). EOD cut: gần cuối ngày (server time), nếu `dailyRealized + floating > 0` → đóng hết lệnh và pause re-entry đến qua ngày.

## Components

- **EA**: `ea/GvfxSignalEA.mq5` — Market grid DCA với spread guard + EOD cut
- **Agent**: `agent/` — Consume Redis Stream `gvfx_signals`, ghi `{account}_{symbol}.json`
- **Data**: `data/` — Runtime symlink → `MQL5/Files/GvfxSignalEA/`

## EA Logic

### Entry rules
- **Gating**: vào market mỗi tick khi tất cả các điều kiện sau thỏa:
  1. `g_signalActive` (chưa chạm target).
  2. `open_count < InpMaxPositions` (cap 20).
  3. `current_spread_pts ≤ InpMaxSpreadPts`.
  4. Price-zone gate (nếu signal có `low`/`high`): BUY chỉ vào khi `entryPrice > low`; SELL chỉ vào khi `entryPrice < high`.
  5. **Target proximity gate**: BUY chỉ vào khi `target − entry ≥ effStep*_Point`; SELL chỉ vào khi `entry − target ≥ effStep*_Point`. Tránh mở lệnh quá gần target — TP = effStep × 0.95 sẽ rơi qua target, signal deactivate trước khi position chạm TP → dangle đến khi SL/oscillation.
  6. `HasOpenWithinStep(entry_price, step*_Point) == false` — không có vị thế EA nào đang mở với `|open_price − entry_price| < step*_Point`.
- **Entry price**: BUY → `SymbolInfoDouble(_Symbol, SYMBOL_ASK)`; SELL → `SymbolInfoDouble(_Symbol, SYMBOL_BID)`.
- **Re-entry sau TP/SL**: khi 1 position TP/SL → slot trống. Nếu giá hiện tại không nằm trong ±`step` của bất kỳ vị thế nào còn lại → re-entry ngay tick kế tiếp (kể cả tại level cũ).
- **Spread guard**: chỉ vào lệnh khi `current_spread_pts ≤ InpMaxSpreadPts` (default 30 pts) — nếu vượt thì skip tick đó.

### Per-order risk
- Lot = `InpLotPerOrder` (default 0.01), normalize theo `SYMBOL_VOLUME_STEP`.
- TP = entry ± `effTp * _Point` (clamped via broker stops level), với `effTp` từ `EffectiveStepTpPts()` (ATR hoặc fallback).
- Hard SL = entry ∓ `InpMaxLossPtsPerOrder * _Point` (default 10000 pts, clamped).

### ATR-derived step/tp (`use_atr`)
- Khi `signal.use_atr = true` (default): EA derive `effStep` / `effTp` từ 2 handle ATR riêng (`InpAtrStepTf` default `PERIOD_M15`, `InpAtrTpTf` default `PERIOD_M5`):
  - `effStep = round(ATR_StepTf_pts * InpAtrStepMult)` — ATR trên `InpAtrStepTf` (default `PERIOD_M15`).
  - `effTp   = round(ATR_TpTf_pts   * InpAtrTpMult)`   — ATR trên `InpAtrTpTf`   (default `PERIOD_M5`).
  - Step clamp `[InpAtrMinPts, InpAtrMaxPts]` (default 500..5000 pts).
  - TP   clamp `[InpAtrMinPts, InpAtrTpMaxPts]` (default 500..1000 pts = 50..100 pips cho XAUUSD 2-digit) — TP ceiling thấp hơn step để giữ TP gần entry.
  - Cả 2 handle dùng shift=1 (bar đóng gần nhất) để tránh flap mỗi tick trong bar đang chạy.
- Fallback về `signal.step` / `signal.tp` khi: handle invalid (init failed), `CopyBuffer()` chưa có data, hoặc giá trị NaN/≤0. Mode tag = `F`. Fallback áp dụng atomic: cả step và tp cùng fallback nếu một trong hai handle fail.
- Khi `signal.use_atr = false`: EA dùng thẳng `signal.step` / `signal.tp` — không cần ATR. Mode tag = `S`.
- 2 handle `iATR` được tạo 1 lần trong `OnInit`, release trong `OnDeinit` qua `IndicatorRelease`.
- ATR-related inputs: `InpAtrStepTf` (default `PERIOD_M15`), `InpAtrTpTf` (default `PERIOD_M5`), `InpAtrPeriod` (14), `InpAtrStepMult` (0.9), `InpAtrTpMult` (0.9), `InpAtrMinPts` (500 = 50 pips), `InpAtrMaxPts` (5000, step ceiling), `InpAtrTpMaxPts` (1000 = 100 pips, tp ceiling).

### Signal lifecycle
- New `timestamp` → `g_signalActive = true`, cache `g_currentSig`.
- Target reached → `g_signalActive = false`. Positions đang mở vẫn chạy theo TP/SL, không vào thêm.
  - BUY: `bid ≥ target` → inactive
  - SELL: `ask ≤ target` → inactive
- **Operator cancel**: signal JSON có 2 field `active` (default `true`) và `close_all` (default `false`). Operator gõ `/cancel_gvfx` → Telegram hiện 2 nút chọn scope → agent ghi đè file signal với `active=false` + `close_all` tương ứng (**giữ nguyên timestamp**). EA poll thấy `active=false` trên cùng ts, trên cạnh active→inactive:
  - `close_all=false` (default): chỉ set `g_signalActive=false` → ngừng vào lệnh mới; positions đang mở giữ TP/SL.
  - `close_all=true`: thêm `CloseAllAndCancel()` **đóng hết positions + cancel pendings** của magic+symbol.
  Cả 2 mode đều gọi `MarkSignalReached()` (kill survive qua redeploy). Không có resume — muốn trade lại thì publish signal mới (ts mới). Nếu `close_all=true` và EA bị restart giữa lúc cancel chưa kịp đóng, `OnInit` set cờ `g_pendingCancelSweep` → tick đầu sweep nốt lệnh sót.
- **Restart-safe deactivation**: khi target reached, EA persist ts vào GlobalVariable `GVFX_Reached_{magic}_{symbol}`. Cả `OnInit` (recover signal state) lẫn `OnTick` (new-signal detection) đều consult biến này — nếu `sig.timestamp == GVFX_Reached_*` thì giữ `g_signalActive = false` bất kể giá hiện tại đã rút khỏi target hay chưa. Tránh các kịch bản: (a) BUY target chạm rồi bid hồi lại dưới target, redeploy EA → tưởng signal còn active và mở lệnh mới trên signal đã chết; (b) deploy lên chart mới / fresh terminal khi positions/history rotate ra ngoài `InpHistoryLookbackDays` → `ScanMaxSeenTimestamp` trả 0, OnInit không match `probe.timestamp == g_lastSigTs`, OnTick treat file ts như signal mới và resurrect. Biến này tự overwrite khi signal mới reached, không cần cleanup.

### EOD cut
- **Trigger window**: từ `(today_session_close - InpEodCutLeadMins phút)` đến hết ngày. `today_session_close` lấy động qua `SymbolInfoSessionTrade(_Symbol, dow, i, ...)` — pick `max(to)` của tất cả phiên trong ngày của broker. Default lead = 5 phút. Set `InpEodCutLeadMins = -1` để disable.
- **Branch A — total > 0 (`dailyRealized + floating > 0`)**: `CloseAllAndCancel()` → đóng hết positions + cancel pendings, gọi `ArmEodSuppression()` → suppress entries đến qua ngày.
- **Branch B — total < 0**: `PartialEodTrimLosers()`. Sort các vị thế đang lỗ (`profit + swap < 0`) theo P&L tăng dần (lỗ nhiều nhất trước), close lần lượt; trước mỗi close kiểm tra `dailyRealized + Σcuts + next.pnl ≥ 0` — nếu cắt tiếp sẽ kéo realized âm thì `break`. Sau khi loop xong, gọi `ArmEodSuppression()` → suppress entries đến qua ngày (dù break sớm hay không).
- **Suppression**: `ArmEodSuppression()` set `g_eodCutDoneAnchor = g_dailyAnchor` và persist vào `GlobalVariable` `GVFX_EodCut_{magic}_{symbol}` (restart-safe). Day rollover → anchor cleared và GlobalVariable bị xóa.
- **Total = 0** hoặc `g_openCount == 0` → no-op (không suppress).
- Nếu broker không expose session (weekend/holiday) → `TodaySessionCloseTime()` return 0, EOD cut skip toàn bộ.

### Dedup (restart-safe)
- Position comment: `GVFX_T{timestamp}_{mode}` — e.g., `GVFX_T1777896356_A`. Mode tag distinguishes how step/tp được chọn tại thời điểm vào lệnh:
  - `S` — `signal.use_atr=false` → dùng thẳng `signal.step` / `signal.tp`.
  - `A` — `signal.use_atr=true` + ATR ready → step/tp derive từ ATR.
  - `F` — `signal.use_atr=true` nhưng ATR chưa sẵn (handle invalid hoặc buffer warming up) → fallback về `signal.step` / `signal.tp`.
- `ScanMaxSeenTimestamp()` scan POSITIONS + ORDERS + DEAL HISTORY (lookback `InpHistoryLookbackDays` ngày), parse ts từ comment, lấy max → `g_lastSigTs` recovered on restart. `StringToInteger()` dừng ở ký tự non-digit đầu tiên nên cả format mới (`GVFX_T{ts}_X`) lẫn legacy (`GVFX_T{ts}`) đều parse cùng kết quả.
- Không cần reconstruct grid anchor: `HasOpenWithinStep` đọc trực tiếp từ vị thế đang mở mỗi tick → state-less, restart-safe by construction.

## Agent Signal Format

```json
{
  "timestamp": 1777896356,
  "symbol": "XAUUSD",
  "target": 4860.0,
  "direction": "BUY",
  "step": 500,
  "tp": 500,
  "low": 0.0,
  "high": 0.0,
  "use_atr": true,
  "active": true,
  "close_all": false
}
```

- `step`, `tp`: integer, đơn vị **MT5 points** (1pt = `_Point`; với XAUUSD 2-digit → 500 pts = 5.00 price). Khi `use_atr=true` → đây là **fallback** dùng khi ATR buffer chưa sẵn sàng.
- `low`, `high`: float, đơn vị **price**. Optional price-zone gate (0 = disabled).
  - BUY: chỉ vào lệnh khi `entryPrice > low`.
  - SELL: chỉ vào lệnh khi `entryPrice < high`.
  - Nếu cả hai > 0 thì phải `low < high`.
- `use_atr`: bool, default `true`. Khi true → EA derive step/tp từ iATR; signal `step`/`tp` thành fallback. Xem section _ATR-derived step/tp_.
- `active`: bool, default `true`. `false` → operator đã hủy signal qua `/cancel_gvfx`; EA chặn entry mới. Agent set field này bằng cách rewrite file (giữ ts) khi nhận control message `action=deactivate` trên stream `gvfx_signals`.
- `close_all`: bool, default `false`. Chỉ có ý nghĩa khi `active=false`. `true` → EA còn đóng hết positions + cancel pendings; `false` → chỉ chặn entry, positions giữ nguyên.
- `timestamp` **NOT re-stamped** — producer-supplied, preserved end-to-end. Đây là dedup identity nhúng vào position comment.
- File path: `data/{account}_{symbol}.json` (e.g., `data/5100000_XAUUSD.json`).
- EA reads from `MQL5/Files/GvfxSignalEA/{account}_{symbol}.json`.
- Backward compat: nếu file không có field `low`/`high` (hoặc là `null`), EA mặc định 0 (disabled). Field `use_atr` missing/null → default `true`.

## Agent Config (`.env`)

```
MT5_SIGNAL_DIR=../data
MT5_ACCOUNTS=5100000
MT5_SYMBOLS=XAUUSD
REDIS_URL=redis://localhost:6379
REDIS_STREAM=gvfx_signals
REDIS_GROUP=gvfx_writer
REDIS_CONSUMER=gvfx-agent-1
LOG_LEVEL=INFO
```

## Important Notes

- Tất cả unit `step` / `tp` / `InpMaxLossPtsPerOrder` theo **MT5 points**, không phải pip / price.
- `ClampStop()` enforce broker's minimum stop distance (`SYMBOL_TRADE_STOPS_LEVEL`).
- Daily anchor = server time midnight (không dùng broker timezone hay local time) — vẫn được track dù cut logic đang disabled.
- Mỗi lệnh đặt cố định lot size từ `InpLotPerOrder`; không có lot scaling theo level grid.
- Re-entry rule là **state-less**: chỉ phụ thuộc vào set vị thế đang mở của magic+symbol — không cache "last entry price". Slot trống do TP/SL **được phép re-entry tại level cũ** ngay tick kế tiếp nếu ±step radius hiện tại rỗng.
