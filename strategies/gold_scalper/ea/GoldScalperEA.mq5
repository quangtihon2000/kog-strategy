//+------------------------------------------------------------------+
//|  GoldScalperEA.mq5                                               |
//|  Scalp XAUUSD M5: Killzone breakout (A) + VWAP rejection (B).    |
//|  Risk 0.75%/lệnh, max 1 vị thế, 3 lệnh/ngày, dừng nếu -2% ngày.  |
//+------------------------------------------------------------------+
//
// TRIẾT LÝ MỤC TIÊU:
//   EA này nhắm trung bình 1-1.5%/ngày tính trên 20 ngày giao dịch,
//   KHÔNG phải mỗi ngày. Có ngày flat, có ngày âm trong giới hạn
//   daily loss. Việc cố ép profit mỗi ngày sẽ phá vỡ expectancy
//   của hệ thống.
//
//+------------------------------------------------------------------+
#property copyright "Gold Scalper EA"
#property version   "1.00"
#property description "XAUUSD M5: London/NY killzone breakout retest + VWAP mean-reversion."

#include <Trade\Trade.mqh>

//============================================================
// Inputs
//============================================================
input group "=== Risk Management ==="
input double InpRiskPercent          = 0.75;   // Risk per trade (% balance)
input double InpDailyLossLimitPct    = 2.0;    // Stop trading if daily PnL <= -X%
input int    InpMaxTradesPerDay      = 3;      // Max executed trades per broker day
input int    InpMaxConsecLosses      = 2;      // Stop trading after N losses in a row
input double InpDailyProfitTargetPct = 1.5;    // Daily profit target (% balance) — block new entries when hit
input bool   InpEnableGreenDayLock   = true;   // Close open trade if profit retraces below lock threshold
input double InpGreenDayLockRetracePct = 50.0; // % of target — close open trade to preserve green day
input bool   InpEnableLong           = true;   // Allow long entries (Setup A & B)
input bool   InpEnableShort          = true;   // Allow short entries (Setup A & B)

input group "=== Session Times (broker time, HH:MM) ==="
input string InpAsianStart           = "06:00"; // Asian session start
input string InpAsianEnd             = "13:00"; // Asian session end → range frozen
input string InpLondonKZStart        = "13:00"; // London killzone start
input string InpLondonKZEnd          = "16:00"; // London killzone end
input string InpNYKZStart            = "16:00"; // NY killzone start
input string InpNYKZEnd              = "19:00"; // NY killzone end
input string InpDailyCutoff          = "22:00"; // No new entries after this time
input string InpForceCloseTime       = "23:30"; // Force close all positions

input group "=== Setup A — Killzone Breakout ==="
input bool   InpEnableSetupA         = true;   // Enable breakout setup
input int    InpRetestMaxBars        = 12;     // Max M5 bars to wait for retest after break
input int    InpSL_BufferPoints      = 200;    // SL buffer beyond swing (points; 200=20pip)
input int    InpEMA_M15_Period       = 50;     // EMA filter period on M15
input double InpTP1_RR               = 1.0;    // TP1 RR (close 50%)
input double InpTP2_RR               = 2.5;    // TP2 RR (close remainder)
input double InpTP1_PartialPct       = 50.0;   // % volume closed at TP1

input group "=== Setup B — VWAP Rejection ==="
input bool   InpEnableSetupB         = true;   // Enable VWAP rejection setup
input int    InpVWAP_DeviationPips   = 60;     // Min deviation from VWAP to arm setup (pips)
input double InpVWAP_RR              = 1.5;    // RR for VWAP rejection trades

input group "=== Filters ==="
input int    InpMaxSpreadPoints      = 30;     // Skip entries when spread > X points
input string InpNewsTimes            = "";     // CSV of HH:MM (broker tz) e.g. "14:30,20:00"
input int    InpNewsBufferMinutes    = 15;     // Block ± minutes around each news time
input int    InpEMA_M15_Fast         = 20;     // EMA20 (chart-only when InpDrawLevels)
input bool   InpDrawLevels           = false;  // Draw VWAP/EMA/Asian range to chart
input bool   InpAlertOnRealAccount   = true;   // Alert once at OnInit on real account

input group "=== Misc ==="
input ulong  InpMagic                = 20260503; // Unique magic for this EA
input int    InpSlippagePts          = 20;       // Max deviation in points
input double InpFallbackLot          = 0.01;     // Used if risk math fails
input bool   InpVerboseLog           = true;     // Verbose debug logs

//============================================================
// Globals
//============================================================
CTrade   g_trade;
datetime g_lastTickCheck = 0;     // 1 Hz throttle for per-tick housekeeping
datetime g_lastM5BarTime = 0;     // last M5 bar processed
int      g_curDayKey     = 0;     // yyyymmdd

// Asian range state (per day)
double   g_asianHigh = 0.0;
double   g_asianLow  = 0.0;
bool     g_asianReady = false;

// Setup A breakout state (per day, per direction)
datetime g_breakoutBarTime = 0;   // bar time when break detected (for retest window)
int      g_breakoutDir     = 0;   // +1 long, -1 short, 0 none
double   g_swingExtreme    = 0.0; // swing low (long) or swing high (short) for SL calc
bool     g_setupA_takenToday = false;

// VWAP custom
double   g_vwapCumPV = 0.0;
double   g_vwapCumV  = 0.0;
double   g_vwapValue = 0.0;
int      g_vwapDayKey = 0;

// Indicator handles
int      g_emaSlowHandle = INVALID_HANDLE;
int      g_emaFastHandle = INVALID_HANDLE;

// Risk tracking
double   g_dayStartBalance = 0.0;
int      g_tradesToday     = 0;
int      g_consecLosses    = 0;
ulong    g_lastSeenDealId  = 0;   // for closed-trade detection
bool     g_realAccountAlerted = false;
bool     g_dailyProfitTargetHit = false;  // set when closed P/L >= target → block new entries
bool     g_greenDayLockTriggered = false; // set after green day lock fires → block re-entry same day

// Position management state per ticket
ulong    g_managedTicket = 0;
double   g_tp1Price      = 0.0;
double   g_tp2Price      = 0.0;
bool     g_tp1Hit        = false;
int      g_managedSetup  = 0;     // 1 = Setup A, 2 = Setup B

// Cached news times (parsed at OnInit)
int      g_newsMinutes[];
int      g_newsCount = 0;

//============================================================
// Helpers
//============================================================

// "HH:MM" → minutes-of-day; -1 if invalid
int ParseHHMM(const string s) {
   if (StringLen(s) < 4) return -1;
   string parts[];
   int n = StringSplit(s, ':', parts);
   if (n != 2) return -1;
   int h = (int)StringToInteger(parts[0]);
   int m = (int)StringToInteger(parts[1]);
   if (h < 0 || h > 23 || m < 0 || m > 59) return -1;
   return h * 60 + m;
}

int NowMinutesOfDay() {
   MqlDateTime tm; TimeToStruct(TimeCurrent(), tm);
   return tm.hour * 60 + tm.min;
}

int DateKey(const datetime t) {
   MqlDateTime tm; TimeToStruct(t, tm);
   return tm.year * 10000 + tm.mon * 100 + tm.day;
}

// 1 pip = 10 points cho XAUUSD (digits=2 hoặc 3)
double PipsToPrice(const int pips) {
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   return pips * 10.0 * point;
}

double PointsToPrice(const int points) {
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   return points * point;
}

// Bullish engulfing: nến idx (1=just closed) tăng, body >= body nến idx+1, ngược màu, close > open[idx+1]
bool IsBullishEngulfing(const int idx) {
   double o1 = iOpen(_Symbol, PERIOD_M5, idx);
   double c1 = iClose(_Symbol, PERIOD_M5, idx);
   double o2 = iOpen(_Symbol, PERIOD_M5, idx + 1);
   double c2 = iClose(_Symbol, PERIOD_M5, idx + 1);
   if (c1 <= o1) return false;             // current must be bullish
   if (c2 >= o2) return false;             // prior must be bearish
   double bodyCur = c1 - o1;
   double bodyPrev = o2 - c2;
   if (bodyCur < bodyPrev) return false;   // engulf prior body
   if (c1 <= o2) return false;             // close above prior open
   return true;
}

bool IsBearishEngulfing(const int idx) {
   double o1 = iOpen(_Symbol, PERIOD_M5, idx);
   double c1 = iClose(_Symbol, PERIOD_M5, idx);
   double o2 = iOpen(_Symbol, PERIOD_M5, idx + 1);
   double c2 = iClose(_Symbol, PERIOD_M5, idx + 1);
   if (c1 >= o1) return false;
   if (c2 <= o2) return false;
   double bodyCur = o1 - c1;
   double bodyPrev = c2 - o2;
   if (bodyCur < bodyPrev) return false;
   if (c1 >= o2) return false;
   return true;
}

// Pin bar: body <= 30% range, lower wick (bull) hoặc upper wick (bear) >= 60% range
bool IsBullishPinBar(const int idx) {
   double o = iOpen(_Symbol,  PERIOD_M5, idx);
   double c = iClose(_Symbol, PERIOD_M5, idx);
   double h = iHigh(_Symbol,  PERIOD_M5, idx);
   double l = iLow(_Symbol,   PERIOD_M5, idx);
   double rng = h - l;
   if (rng <= 0) return false;
   double body = MathAbs(c - o);
   double lowerWick = MathMin(o, c) - l;
   if (body / rng > 0.30) return false;
   if (lowerWick / rng < 0.60) return false;
   return true;
}

bool IsBearishPinBar(const int idx) {
   double o = iOpen(_Symbol,  PERIOD_M5, idx);
   double c = iClose(_Symbol, PERIOD_M5, idx);
   double h = iHigh(_Symbol,  PERIOD_M5, idx);
   double l = iLow(_Symbol,   PERIOD_M5, idx);
   double rng = h - l;
   if (rng <= 0) return false;
   double body = MathAbs(c - o);
   double upperWick = h - MathMax(o, c);
   if (body / rng > 0.30) return false;
   if (upperWick / rng < 0.60) return false;
   return true;
}

bool IsBullishConfirm(const int idx) { return IsBullishEngulfing(idx) || IsBullishPinBar(idx); }
bool IsBearishConfirm(const int idx) { return IsBearishEngulfing(idx) || IsBearishPinBar(idx); }

void LogV(const string msg) {
   if (InpVerboseLog) Print("[GS] " + msg);
}

//============================================================
// News filter
//============================================================
void ParseNewsTimes() {
   ArrayResize(g_newsMinutes, 0);
   g_newsCount = 0;
   if (StringLen(InpNewsTimes) == 0) return;
   string items[];
   int n = StringSplit(InpNewsTimes, ',', items);
   for (int i = 0; i < n; i++) {
      string trimmed = items[i];
      StringTrimLeft(trimmed);
      StringTrimRight(trimmed);
      int m = ParseHHMM(trimmed);
      if (m < 0) continue;
      ArrayResize(g_newsMinutes, g_newsCount + 1);
      g_newsMinutes[g_newsCount++] = m;
   }
   PrintFormat("[GS] News filter loaded: %d entries (±%d min)", g_newsCount, InpNewsBufferMinutes);
}

bool IsInNewsWindow() {
   if (g_newsCount == 0) return false;
   int now = NowMinutesOfDay();
   for (int i = 0; i < g_newsCount; i++) {
      if (MathAbs(now - g_newsMinutes[i]) <= InpNewsBufferMinutes) return true;
   }
   return false;
}

//============================================================
// VWAP (custom, daily reset 00:00 broker)
//============================================================
void RebuildVWAPIfNewDay() {
   int dk = g_curDayKey;
   if (dk != g_vwapDayKey) {
      g_vwapDayKey = dk;
      g_vwapCumPV  = 0.0;
      g_vwapCumV   = 0.0;
      g_vwapValue  = 0.0;
   }
}

// Gọi mỗi khi có M5 bar mới đóng — accumulate bar idx=1
void UpdateVWAPOnNewBar() {
   RebuildVWAPIfNewDay();
   double h = iHigh(_Symbol,  PERIOD_M5, 1);
   double l = iLow(_Symbol,   PERIOD_M5, 1);
   double c = iClose(_Symbol, PERIOD_M5, 1);
   long   v = iTickVolume(_Symbol, PERIOD_M5, 1);
   double typical = (h + l + c) / 3.0;
   g_vwapCumPV += typical * (double)v;
   g_vwapCumV  += (double)v;
   if (g_vwapCumV > 0.0) g_vwapValue = g_vwapCumPV / g_vwapCumV;
}

//============================================================
// Session manager
//============================================================
bool IsBetween(const int now, const int startMin, const int endMin) {
   if (startMin <= endMin) return (now >= startMin && now < endMin);
   // wraps midnight
   return (now >= startMin || now < endMin);
}

bool IsInLondonKZ() {
   int now = NowMinutesOfDay();
   return IsBetween(now, ParseHHMM(InpLondonKZStart), ParseHHMM(InpLondonKZEnd));
}

bool IsInNYKZ() {
   int now = NowMinutesOfDay();
   return IsBetween(now, ParseHHMM(InpNYKZStart), ParseHHMM(InpNYKZEnd));
}

bool IsInKillzone() { return IsInLondonKZ() || IsInNYKZ(); }

bool IsAfterCutoff() {
   return NowMinutesOfDay() >= ParseHHMM(InpDailyCutoff);
}

bool IsForceCloseTime() {
   return NowMinutesOfDay() >= ParseHHMM(InpForceCloseTime);
}

// Build/refresh Asian range — chỉ build sau khi phiên Á đóng
void BuildAsianRangeIfReady() {
   if (g_asianReady) return;
   int asianEndMin = ParseHHMM(InpAsianEnd);
   if (NowMinutesOfDay() < asianEndMin) return;
   int asianStartMin = ParseHHMM(InpAsianStart);
   int barsLookback = (asianEndMin - asianStartMin) / 5;  // M5
   if (barsLookback <= 0) return;
   int hi = iHighest(_Symbol, PERIOD_M5, MODE_HIGH, barsLookback, 1);
   int lo = iLowest(_Symbol,  PERIOD_M5, MODE_LOW,  barsLookback, 1);
   if (hi < 0 || lo < 0) return;
   g_asianHigh = iHigh(_Symbol, PERIOD_M5, hi);
   g_asianLow  = iLow(_Symbol,  PERIOD_M5, lo);
   g_asianReady = true;
   PrintFormat("[GS] Asian range built: H=%.2f L=%.2f (range=%.2f)",
               g_asianHigh, g_asianLow, g_asianHigh - g_asianLow);
}

void ResetDailyState() {
   g_asianHigh = 0.0;
   g_asianLow  = 0.0;
   g_asianReady = false;
   g_breakoutBarTime = 0;
   g_breakoutDir = 0;
   g_swingExtreme = 0.0;
   g_setupA_takenToday = false;
   g_tradesToday = 0;
   g_consecLosses = 0;   // reset cùng thời điểm với daily loss limit
   g_dailyProfitTargetHit = false;
   g_greenDayLockTriggered = false;
   g_dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   PrintFormat("[GS] Daily reset. startBalance=%.2f", g_dayStartBalance);
}

//============================================================
// Risk manager
//============================================================
double CalcLot(const double slDistancePrice) {
   if (slDistancePrice <= 0) return InpFallbackLot;
   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if (tickVal <= 0 || tickSize <= 0) return InpFallbackLot;
   double riskMoney  = balance * InpRiskPercent / 100.0;
   double lossPerLot = (slDistancePrice / tickSize) * tickVal;
   if (lossPerLot <= 0) return InpFallbackLot;
   double lot = riskMoney / lossPerLot;
   double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if (step > 0) lot = MathFloor(lot / step) * step;
   if (lot < minLot) lot = minLot;
   if (lot > maxLot) lot = maxLot;
   return lot;
}

double DailyPnL() {
   return AccountInfoDouble(ACCOUNT_BALANCE) - g_dayStartBalance;
}

double DailyPnLPct() {
   if (g_dayStartBalance <= 0) return 0.0;
   return DailyPnL() / g_dayStartBalance * 100.0;
}

bool ShouldStopTrading() {
   if (DailyPnLPct() <= -InpDailyLossLimitPct) {
      LogV("Daily loss limit hit, stopping until next day");
      return true;
   }
   if (g_tradesToday >= InpMaxTradesPerDay) {
      LogV("Max trades/day reached");
      return true;
   }
   if (g_consecLosses >= InpMaxConsecLosses) {
      LogV("Consecutive loss kill-switch tripped");
      return true;
   }
   if (g_dailyProfitTargetHit) {
      LogV("Daily profit target reached — no new entries until next day");
      return true;
   }
   return false;
}

// Daily profit target + green day lock — gọi mỗi tick (sau ManagePositions).
// Reason: dùng ACCOUNT_BALANCE (closed P/L) để arm flag, ACCOUNT_EQUITY (closed+floating)
// để check retrace. Lệnh đang mở tiếp tục được TP/SL/BE quản lý bình thường — chỉ block
// việc vào lệnh mới sau khi target hit.
void CheckDailyProfitTarget() {
   if (InpDailyProfitTargetPct <= 0 || g_dayStartBalance <= 0) return;
   double targetMoney = g_dayStartBalance * InpDailyProfitTargetPct / 100.0;

   // Arm: closed P/L (balance-only) đã chạm target
   if (!g_dailyProfitTargetHit) {
      double closedPnL = AccountInfoDouble(ACCOUNT_BALANCE) - g_dayStartBalance;
      if (closedPnL >= targetMoney) {
         g_dailyProfitTargetHit = true;
         PrintFormat("[GS] Daily profit target HIT closedPnL=%.2f target=%.2f — block new entries until 00:00",
                     closedPnL, targetMoney);
      }
   }

   // Green day lock: target đã hit + có lệnh đang mở + equity-based PnL retrace xuống lock threshold
   if (g_dailyProfitTargetHit
       && InpEnableGreenDayLock
       && !g_greenDayLockTriggered
       && CountMyPositions() > 0) {
      double lockMoney = targetMoney * InpGreenDayLockRetracePct / 100.0;
      double equityPnL = AccountInfoDouble(ACCOUNT_EQUITY) - g_dayStartBalance;
      if (equityPnL <= lockMoney) {
         PrintFormat("[GS] Green day lock TRIGGERED equityPnL=%.2f lockAt=%.2f — closing all to preserve green day",
                     equityPnL, lockMoney);
         ForceCloseAll();
         g_greenDayLockTriggered = true;
      }
   }
}

bool IsSpreadOk() {
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if (spread > InpMaxSpreadPoints) {
      LogV(StringFormat("Spread %d > max %d, skip", (int)spread, InpMaxSpreadPoints));
      return false;
   }
   return true;
}

//============================================================
// Position helpers
//============================================================
int CountMyPositions() {
   int c = 0;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong tk = PositionGetTicket(i);
      if (tk == 0) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if ((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      c++;
   }
   return c;
}

void ForceCloseAll() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong tk = PositionGetTicket(i);
      if (tk == 0) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if ((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      g_trade.PositionClose(tk);
      PrintFormat("[GS] Force close ticket=%I64u", tk);
   }
}

//============================================================
// Trade executor — OrderSend with retries
//============================================================
bool OpenMarketTrade(const int direction, const double slPrice, const double tp2Price,
                     const double tp1Price, const int setupId) {
   double askBid = (direction > 0)
                   ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                   : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double slDist = MathAbs(askBid - slPrice);
   double lot = CalcLot(slDist);
   if (lot <= 0) {
      LogV("CalcLot returned 0, abort");
      return false;
   }

   for (int attempt = 0; attempt < 3; attempt++) {
      bool ok;
      if (direction > 0)
         ok = g_trade.Buy(lot, _Symbol, 0.0, slPrice, tp2Price, "GS");
      else
         ok = g_trade.Sell(lot, _Symbol, 0.0, slPrice, tp2Price, "GS");

      uint retcode = g_trade.ResultRetcode();
      if (ok && (retcode == TRADE_RETCODE_DONE || retcode == TRADE_RETCODE_PLACED)) {
         ulong ticket = g_trade.ResultOrder();
         g_managedTicket = ticket;
         g_tp1Price = tp1Price;
         g_tp2Price = tp2Price;
         g_tp1Hit   = false;
         g_managedSetup = setupId;
         g_tradesToday++;
         PrintFormat("[GS] Setup%s OPEN dir=%d lot=%.2f sl=%.2f tp1=%.2f tp2=%.2f ticket=%I64u",
                     (setupId == 1 ? "A" : "B"), direction, lot, slPrice, tp1Price, tp2Price, ticket);
         return true;
      }
      // Retry only on requote/changed price/off quotes
      if (retcode != TRADE_RETCODE_REQUOTE
          && retcode != TRADE_RETCODE_PRICE_CHANGED
          && retcode != TRADE_RETCODE_PRICE_OFF) {
         PrintFormat("[GS] OrderSend failed retcode=%d, abort", retcode);
         return false;
      }
      PrintFormat("[GS] OrderSend retry %d retcode=%d", attempt + 1, retcode);
      Sleep(200);
   }
   PrintFormat("[GS] OrderSend exhausted retries");
   return false;
}

// Manage open positions: TP1 partial + breakeven
void ManagePositions() {
   if (g_managedTicket == 0) return;
   if (!PositionSelectByTicket(g_managedTicket)) {
      // Position closed; check deal history to update consec losses
      OnManagedTradeClosed();
      g_managedTicket = 0;
      return;
   }
   if (g_tp1Hit) return;

   long type = PositionGetInteger(POSITION_TYPE);
   double curPrice = (type == POSITION_TYPE_BUY)
                     ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                     : SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   bool reachedTP1 = (type == POSITION_TYPE_BUY)
                     ? (curPrice >= g_tp1Price)
                     : (curPrice <= g_tp1Price);
   if (!reachedTP1) return;

   // Setup A: partial close + move SL to BE
   // Setup B: RR 1:1.5 single target → no partial, but treat TP1 == TP2 (no-op partial)
   if (g_managedSetup == 1) {
      double vol = PositionGetDouble(POSITION_VOLUME);
      double partialVol = vol * (InpTP1_PartialPct / 100.0);
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
      if (step > 0) partialVol = MathFloor(partialVol / step) * step;
      if (partialVol < minLot) partialVol = minLot;
      if (partialVol >= vol) partialVol = vol - step;
      if (partialVol >= minLot) {
         g_trade.PositionClosePartial(g_managedTicket, partialVol);
         PrintFormat("[GS] TP1 partial close vol=%.2f ticket=%I64u", partialVol, g_managedTicket);
      }
      // Move SL to BE
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double tp = PositionGetDouble(POSITION_TP);
      g_trade.PositionModify(g_managedTicket, openPrice, tp);
      PrintFormat("[GS] BE moved sl=%.2f ticket=%I64u", openPrice, g_managedTicket);
   }
   g_tp1Hit = true;
}

// Trade closed → look at last deal PnL to update consec losses
void OnManagedTradeClosed() {
   HistorySelect(TimeCurrent() - 86400, TimeCurrent() + 60);
   int total = HistoryDealsTotal();
   double lastPnL = 0.0;
   bool found = false;
   for (int i = total - 1; i >= 0; i--) {
      ulong dealTicket = HistoryDealGetTicket(i);
      if (dealTicket == 0) continue;
      if ((ulong)HistoryDealGetInteger(dealTicket, DEAL_MAGIC) != InpMagic) continue;
      if (HistoryDealGetString(dealTicket, DEAL_SYMBOL) != _Symbol) continue;
      long entry = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);
      if (entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) continue;
      lastPnL = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
              + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
              + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
      found = true;
      break;
   }
   if (!found) return;
   if (lastPnL < 0) g_consecLosses++;
   else if (lastPnL > 0) g_consecLosses = 0;
   PrintFormat("[GS] Trade closed pnl=%.2f consecLosses=%d", lastPnL, g_consecLosses);
}

//============================================================
// Setup A — Killzone breakout retest
//============================================================
// Trả về true nếu đã vào lệnh. Direction: +1 long, -1 short.
bool TrySetupA() {
   if (!InpEnableSetupA) return false;
   if (g_setupA_takenToday) return false;
   if (!g_asianReady) return false;
   if (!IsInKillzone()) return false;

   // Đọc EMA50 M15
   double emaBuf[];
   if (CopyBuffer(g_emaSlowHandle, 0, 0, 2, emaBuf) < 2) return false;
   double ema50 = emaBuf[1];

   double closePrev = iClose(_Symbol, PERIOD_M5, 1);

   // Phase 1: phát hiện break (close M5 vượt qua range Á)
   if (g_breakoutDir == 0) {
      if (closePrev > g_asianHigh && InpEnableLong) {
         // Long break candidate — cần EMA50 + VWAP đồng thuận
         if (closePrev > ema50 && closePrev > g_vwapValue) {
            g_breakoutDir = +1;
            g_breakoutBarTime = iTime(_Symbol, PERIOD_M5, 1);
            // Swing low = low của N nến gần nhất (lấy 5 nến trước break)
            int swingIdx = iLowest(_Symbol, PERIOD_M5, MODE_LOW, 5, 1);
            g_swingExtreme = (swingIdx >= 0) ? iLow(_Symbol, PERIOD_M5, swingIdx) : g_asianLow;
            PrintFormat("[GS] SetupA LONG break detected close=%.2f asianH=%.2f swingLow=%.2f",
                        closePrev, g_asianHigh, g_swingExtreme);
         }
      } else if (closePrev < g_asianLow && InpEnableShort) {
         if (closePrev < ema50 && closePrev < g_vwapValue) {
            g_breakoutDir = -1;
            g_breakoutBarTime = iTime(_Symbol, PERIOD_M5, 1);
            int swingIdx = iHighest(_Symbol, PERIOD_M5, MODE_HIGH, 5, 1);
            g_swingExtreme = (swingIdx >= 0) ? iHigh(_Symbol, PERIOD_M5, swingIdx) : g_asianHigh;
            PrintFormat("[GS] SetupA SHORT break detected close=%.2f asianL=%.2f swingHigh=%.2f",
                        closePrev, g_asianLow, g_swingExtreme);
         }
      }
      return false;
   }

   // Phase 2: chờ retest + nến confirm trong cửa sổ N nến
   datetime now = iTime(_Symbol, PERIOD_M5, 0);
   long elapsedBars = (now - g_breakoutBarTime) / (5 * 60);
   if (elapsedBars > InpRetestMaxBars) {
      LogV("SetupA retest window expired, reset");
      g_breakoutDir = 0;
      g_breakoutBarTime = 0;
      return false;
   }

   if (g_breakoutDir > 0) {
      // Retest: low nến idx=1 chạm/qua g_asianHigh từ trên xuống + nến confirm bullish
      double lowPrev = iLow(_Symbol, PERIOD_M5, 1);
      if (lowPrev <= g_asianHigh && IsBullishConfirm(1) && closePrev > g_asianHigh) {
         double sl = g_swingExtreme - PointsToPrice(InpSL_BufferPoints);
         double entry = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double slDist = entry - sl;
         if (slDist <= 0) { LogV("SetupA invalid SL dist"); g_breakoutDir = 0; return false; }
         double tp1 = entry + slDist * InpTP1_RR;
         double tp2 = entry + slDist * InpTP2_RR;
         if (OpenMarketTrade(+1, sl, tp2, tp1, 1)) {
            g_setupA_takenToday = true;
            g_breakoutDir = 0;
            return true;
         }
      }
   } else {
      double highPrev = iHigh(_Symbol, PERIOD_M5, 1);
      if (highPrev >= g_asianLow && IsBearishConfirm(1) && closePrev < g_asianLow) {
         double sl = g_swingExtreme + PointsToPrice(InpSL_BufferPoints);
         double entry = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double slDist = sl - entry;
         if (slDist <= 0) { LogV("SetupA invalid SL dist"); g_breakoutDir = 0; return false; }
         double tp1 = entry - slDist * InpTP1_RR;
         double tp2 = entry - slDist * InpTP2_RR;
         if (OpenMarketTrade(-1, sl, tp2, tp1, 1)) {
            g_setupA_takenToday = true;
            g_breakoutDir = 0;
            return true;
         }
      }
   }
   return false;
}

//============================================================
// Setup B — VWAP rejection (mean reversion). RR cố định 1:1.5
//============================================================
bool TrySetupB() {
   if (!InpEnableSetupB) return false;
   if (g_vwapValue <= 0) return false;

   double closePrev = iClose(_Symbol, PERIOD_M5, 1);
   double highPrev  = iHigh(_Symbol,  PERIOD_M5, 1);
   double lowPrev   = iLow(_Symbol,   PERIOD_M5, 1);
   double devThresh = PipsToPrice(InpVWAP_DeviationPips);

   // Long Setup B: giá lệch xa dưới VWAP, retest VWAP từ dưới + nến bullish confirm
   if (InpEnableLong && lowPrev < (g_vwapValue - devThresh) && IsBullishConfirm(1) && closePrev > lowPrev) {
      double entry = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = lowPrev - PipsToPrice(20);
      double slDist = entry - sl;
      if (slDist <= 0) return false;
      double tp = entry + slDist * InpVWAP_RR;
      if (OpenMarketTrade(+1, sl, tp, tp, 2)) return true;
   }

   // Short Setup B: lệch xa trên VWAP
   if (InpEnableShort && highPrev > (g_vwapValue + devThresh) && IsBearishConfirm(1) && closePrev < highPrev) {
      double entry = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl = highPrev + PipsToPrice(20);
      double slDist = sl - entry;
      if (slDist <= 0) return false;
      double tp = entry - slDist * InpVWAP_RR;
      if (OpenMarketTrade(-1, sl, tp, tp, 2)) return true;
   }
   return false;
}

//============================================================
// Lifecycle
//============================================================
int OnInit() {
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints((ulong)InpSlippagePts);

   g_emaSlowHandle = iMA(_Symbol, PERIOD_M15, InpEMA_M15_Period, 0, MODE_EMA, PRICE_CLOSE);
   g_emaFastHandle = iMA(_Symbol, PERIOD_M15, InpEMA_M15_Fast,   0, MODE_EMA, PRICE_CLOSE);
   if (g_emaSlowHandle == INVALID_HANDLE || g_emaFastHandle == INVALID_HANDLE) {
      Print("[GS] Failed to create EMA handles");
      return INIT_FAILED;
   }

   ParseNewsTimes();
   g_dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   g_curDayKey = DateKey(TimeCurrent());
   g_vwapDayKey = g_curDayKey;

   if (InpAlertOnRealAccount && !g_realAccountAlerted) {
      ENUM_ACCOUNT_TRADE_MODE mode = (ENUM_ACCOUNT_TRADE_MODE)AccountInfoInteger(ACCOUNT_TRADE_MODE);
      if (mode == ACCOUNT_TRADE_MODE_REAL) {
         Alert("[GS] WARNING: GoldScalper attached to REAL account ", AccountInfoInteger(ACCOUNT_LOGIN));
         g_realAccountAlerted = true;
      }
   }

   PrintFormat("[GS] Init magic=%I64u risk=%.2f%% maxTrades/day=%d dailyLoss=%.2f%% target=%.2f%% greenLock=%s@%.0f%% spread<=%d",
               InpMagic, InpRiskPercent, InpMaxTradesPerDay, InpDailyLossLimitPct,
               InpDailyProfitTargetPct, (InpEnableGreenDayLock ? "on" : "off"),
               InpGreenDayLockRetracePct, InpMaxSpreadPoints);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) {
   if (g_emaSlowHandle != INVALID_HANDLE) IndicatorRelease(g_emaSlowHandle);
   if (g_emaFastHandle != INVALID_HANDLE) IndicatorRelease(g_emaFastHandle);
   PrintFormat("[GS] Removed. Reason: %d", reason);
}

void OnTick() {
   datetime now = TimeCurrent();
   if (now == g_lastTickCheck) return;
   g_lastTickCheck = now;

   // Day rollover
   int dk = DateKey(now);
   if (dk != g_curDayKey) {
      g_curDayKey = dk;
      ResetDailyState();
   }

   // Per-tick housekeeping
   ManagePositions();
   CheckDailyProfitTarget();

   // Force close window
   if (IsForceCloseTime()) {
      if (CountMyPositions() > 0) ForceCloseAll();
      return;
   }

   // News window — chỉ block entries, không đóng lệnh đang chạy
   bool blocked = IsInNewsWindow();

   // M5 bar mới đóng?
   datetime curM5BarTime = iTime(_Symbol, PERIOD_M5, 0);
   if (curM5BarTime == g_lastM5BarTime) return;
   g_lastM5BarTime = curM5BarTime;

   // Per-bar updates
   UpdateVWAPOnNewBar();
   BuildAsianRangeIfReady();

   if (blocked) { LogV("News window — skip entries"); return; }
   if (IsAfterCutoff()) return;
   if (ShouldStopTrading()) return;
   if (!IsSpreadOk()) return;
   if (CountMyPositions() > 0) return;   // max 1 vị thế đồng thời

   // Setup A trước, B chỉ chạy nếu A không trigger
   if (TrySetupA()) return;
   TrySetupB();
}
