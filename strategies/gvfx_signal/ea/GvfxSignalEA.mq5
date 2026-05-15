//+------------------------------------------------------------------+
//|  GvfxSignalEA.mq5                                                |
//|  Grid DCA from a target-price signal with daily P&L cut          |
//+------------------------------------------------------------------+
#property copyright   "GvfxSignal EA"
#property version     "1.01"
#property description "Reads {account}_{symbol}.json, grid-DCA toward adverse side until target reached"

#include <Trade\Trade.mqh>

//--- Input Parameters
input ulong  InpMagic               = 770001;        // Magic number
input string InpSignalSubdir        = "GvfxSignalEA";// Subdir under MQL5/Files
const bool   InpUseCommonDir        = false;         // Pinned: outcomes path must match publisher. const (not input) so chart-template cache cannot revive it.
input double InpLotPerOrder         = 0.01;          // Lot per grid level
input int    InpMaxPositions        = 20;            // Max OPEN positions on this magic+symbol
input int    InpMaxLossPtsPerOrder  = 10000;         // Hard SL distance per order (points)
input int    InpEodCutLeadMins      = 5;             // Cut N minutes before broker's last session close (-1 disables)
input int    InpMaxSpreadPts        = 30;            // Max spread (pts) to allow entry; 0 disables
input int    InpHistoryLookbackDays = 7;             // History window for restart-safe dedup
input ENUM_TIMEFRAMES InpAtrTimeframe = PERIOD_M15;  // ATR timeframe (used when signal.use_atr=true)
input int    InpAtrPeriod           = 14;            // ATR period
input double InpAtrStepMult         = 1.0;           // step = ATR * mult (when use_atr)
input double InpAtrTpMult           = 0.95;          // tp   = ATR * mult (when use_atr)
input int    InpAtrMinPts           = 500;           // ATR-derived step/tp floor (points)
input int    InpAtrMaxPts           = 5000;          // ATR-derived step/tp ceiling (points)

//+------------------------------------------------------------------+
//| Signal data structure                                            |
//+------------------------------------------------------------------+
struct GvfxSig {
   ulong    timestamp;
   string   symbol;
   string   direction;   // "BUY" or "SELL"
   double   target;
   int      step;        // points (fallback when use_atr and ATR unavailable)
   int      tp;          // points (fallback when use_atr and ATR unavailable)
   double   low;         // BUY entry floor (price). 0 = disabled
   double   high;        // SELL entry ceiling (price). 0 = disabled
   bool     use_atr;     // true → EA derives step/tp from ATR (signal step/tp = fallback)
   bool     valid;
};

//--- Globals
CTrade   g_trade;
string   g_signalFile;
datetime g_lastTickCheck = 0;
ulong    g_lastSigTs     = 0;
bool     g_signalActive  = false;
int      g_openCount     = 0;
double   g_floating      = 0;
datetime g_dailyAnchor   = 0;
double   g_dailyRealized = 0;
datetime g_eodCutDoneAnchor = 0;  // == g_dailyAnchor while today's EOD cut is in effect
GvfxSig  g_currentSig;
int      g_atrHandle    = INVALID_HANDLE;

// === Shadow globals: init from inputs in OnInit, overlaid by per-account JSON config ===
ulong             g_cfg_Magic;
string            g_cfg_SignalSubdir;
double            g_cfg_LotPerOrder;
int               g_cfg_MaxPositions;
int               g_cfg_MaxLossPtsPerOrder;
int               g_cfg_EodCutLeadMins;
int               g_cfg_MaxSpreadPts;
int               g_cfg_HistoryLookbackDays;
ENUM_TIMEFRAMES   g_cfg_AtrTimeframe;
int               g_cfg_AtrPeriod;
double            g_cfg_AtrStepMult;
double            g_cfg_AtrTpMult;
int               g_cfg_AtrMinPts;
int               g_cfg_AtrMaxPts;
bool              g_cfg_Enabled = true;

//+------------------------------------------------------------------+
int OnInit() {
   InitShadowsFromInputs();
   LoadAccountConfig();

   g_trade.SetExpertMagicNumber(g_cfg_Magic);
   ZeroMemory(g_currentSig);

   g_atrHandle = iATR(_Symbol, g_cfg_AtrTimeframe, g_cfg_AtrPeriod);
   if (g_atrHandle == INVALID_HANDLE)
      PrintFormat("[GVFX] iATR(%s, tf=%d, period=%d) failed — fallback to signal step/tp",
                  _Symbol, (int)g_cfg_AtrTimeframe, g_cfg_AtrPeriod);

   g_signalFile = g_cfg_SignalSubdir + "\\"
                + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))
                + "_" + _Symbol + ".json";

   g_lastSigTs = ScanMaxSeenTimestamp();

   //--- If signal file already executed and target not yet reached → keep active.
   //    `SignalAlreadyReached` reads the persistent reached-ts marker so a
   //    deactivated signal stays dead across restarts even if price has since
   //    retreated to the unfavorable side of target.
   GvfxSig probe;
   if (LoadSignal(g_signalFile, probe) && probe.timestamp == g_lastSigTs && g_lastSigTs > 0) {
      g_currentSig    = probe;
      g_signalActive  = !TargetReached(probe) && !SignalAlreadyReached(probe.timestamp);
   }

   RefreshDailyAnchor();

   string varName = EodAnchorVarName();
   if (GlobalVariableCheck(varName)) {
      datetime saved = (datetime)GlobalVariableGet(varName);
      if (saved >= g_dailyAnchor) g_eodCutDoneAnchor = saved;
      else                        GlobalVariableDel(varName);
   }

   EnsureOutcomesDir();

   PrintFormat("[GVFX] Initialized. Signal=%s lastSigTs=%s active=%s dailyRealized=%.2f eodCutDone=%s",
               g_signalFile, IntegerToString(g_lastSigTs),
               g_signalActive ? "true" : "false",
               g_dailyRealized,
               (g_eodCutDoneAnchor == g_dailyAnchor && g_eodCutDoneAnchor > 0) ? "true" : "false");
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   if (g_atrHandle != INVALID_HANDLE) {
      IndicatorRelease(g_atrHandle);
      g_atrHandle = INVALID_HANDLE;
   }
   PrintFormat("[GVFX] Removed. Reason: %d", reason);
}

//+------------------------------------------------------------------+
//| Create GvfxSignalEA\outcomes\ if missing                         |
//+------------------------------------------------------------------+
void EnsureOutcomesDir() {
   string path = g_cfg_SignalSubdir + "\\outcomes";
   if (InpUseCommonDir) {
      if (FolderCreate(path, FILE_COMMON)) return;
   }
   FolderCreate(path);
}

//+------------------------------------------------------------------+
//| Parse mode tag (A|F|S) from "GVFX_T{ts}_{mode}".                 |
//| Returns "" if comment lacks the suffix (legacy positions).        |
//+------------------------------------------------------------------+
string ParseModeFromComment(const string comment) {
   if (StringFind(comment, "GVFX_T") != 0) return "";
   int us = StringFind(comment, "_", 6);
   if (us < 0) return "";
   string tail = StringSubstr(comment, us + 1);
   if (tail == "A" || tail == "F" || tail == "S") return tail;
   return "";
}

//+------------------------------------------------------------------+
//| Capture position close → write outcomes\{position_id}.json        |
//| Fires once per closing deal; idempotent (overwrite by position_id)|
//+------------------------------------------------------------------+
void OnTradeTransaction(
   const MqlTradeTransaction &trans,
   const MqlTradeRequest     &req,
   const MqlTradeResult      &res
) {
   if (trans.type != TRADE_TRANSACTION_DEAL_ADD) return;

   ulong deal_ticket = trans.deal;
   if (deal_ticket == 0) return;
   if (!HistoryDealSelect(deal_ticket)) return;

   if ((ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY) != DEAL_ENTRY_OUT) return;
   if (HistoryDealGetString(deal_ticket, DEAL_SYMBOL) != _Symbol)                          return;
   // NOTE: do NOT filter on closing deal's DEAL_MAGIC — manual close via UI sets it to 0.

   long position_id = HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
   if (position_id == 0) return;

   string out_comment = HistoryDealGetString(deal_ticket, DEAL_COMMENT);
   ulong  signal_ts   = ParseTsFromComment(out_comment);
   string mode_tag    = ParseModeFromComment(out_comment);
   string in_comment  = "";
   double entry_price = 0.0;
   long   opened_at   = 0;
   string direction   = "";
   long   in_magic    = -1;

   if (HistorySelectByPosition(position_id)) {
      int n = HistoryDealsTotal();
      for (int i = 0; i < n; i++) {
         ulong d = HistoryDealGetTicket(i);
         if (d == 0) continue;
         if ((ENUM_DEAL_ENTRY)HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_IN) continue;
         in_magic    = HistoryDealGetInteger(d, DEAL_MAGIC);
         entry_price = HistoryDealGetDouble(d, DEAL_PRICE);
         opened_at   = (long)HistoryDealGetInteger(d, DEAL_TIME);
         long t      = HistoryDealGetInteger(d, DEAL_TYPE);
         direction   = (t == DEAL_TYPE_BUY) ? "BUY" : "SELL";
         in_comment  = HistoryDealGetString(d, DEAL_COMMENT);
         if (signal_ts == 0) signal_ts = ParseTsFromComment(in_comment);
         if (mode_tag  == "") mode_tag  = ParseModeFromComment(in_comment);
         break;
      }
   }
   if (in_magic != (long)g_cfg_Magic) return;
   if (signal_ts == 0)                return;

   double exit_price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
   double profit     = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
   double swap       = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
   double commission = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
   double volume     = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
   long   closed_at  = (long)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
   long   reason_int = HistoryDealGetInteger(deal_ticket, DEAL_REASON);
   long   account    = (long)AccountInfoInteger(ACCOUNT_LOGIN);

   string close_reason;
   if      (reason_int == DEAL_REASON_TP)     close_reason = "TP";
   else if (reason_int == DEAL_REASON_SL)     close_reason = "SL";
   else if (reason_int == DEAL_REASON_EXPERT) close_reason = IsEodWindow() ? "EOD" : "EXPERT";
   else                                       close_reason = "OTHER";

   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   string comment_field = (in_comment != "") ? in_comment : out_comment;

   string json = "{";
   json += "\"position_id\":"     + IntegerToString(position_id)        + ",";
   json += "\"deal_out_ticket\":" + IntegerToString((long)deal_ticket)  + ",";
   json += "\"signal_ts\":"       + IntegerToString((long)signal_ts)    + ",";
   json += "\"comment\":\""       + comment_field                       + "\",";
   json += "\"mode_tag\":\""      + mode_tag                            + "\",";
   json += "\"account\":"         + IntegerToString(account)            + ",";
   json += "\"symbol\":\""        + _Symbol                             + "\",";
   json += "\"direction\":\""     + direction                           + "\",";
   json += "\"magic\":"           + IntegerToString((long)g_cfg_Magic)  + ",";
   json += "\"volume\":"          + DoubleToString(volume, 2)           + ",";
   json += "\"entry_price\":"     + DoubleToString(entry_price, digits) + ",";
   json += "\"exit_price\":"      + DoubleToString(exit_price, digits)  + ",";
   json += "\"profit\":"          + DoubleToString(profit, 2)           + ",";
   json += "\"swap\":"            + DoubleToString(swap, 2)             + ",";
   json += "\"commission\":"      + DoubleToString(commission, 2)       + ",";
   json += "\"opened_at\":"       + IntegerToString(opened_at)          + ",";
   json += "\"closed_at\":"       + IntegerToString(closed_at)          + ",";
   json += "\"close_reason\":\""  + close_reason                        + "\"";
   json += "}";

   string path  = g_cfg_SignalSubdir + "\\outcomes\\" + IntegerToString(position_id) + ".json";
   int    flags = FILE_WRITE | FILE_TXT | FILE_ANSI;
   if (InpUseCommonDir) flags |= FILE_COMMON;

   int h = FileOpen(path, flags);
   if (h == INVALID_HANDLE) {
      flags ^= FILE_COMMON;
      h = FileOpen(path, flags);
      if (h == INVALID_HANDLE) {
         PrintFormat("[Outcome] Cannot write '%s' (err %d)", path, GetLastError());
         return;
      }
   }
   FileWriteString(h, json);
   FileClose(h);
   PrintFormat("[Outcome] saved pos=%s reason=%s mode=%s ts=%s",
               IntegerToString(position_id), close_reason, mode_tag,
               IntegerToString((long)signal_ts));
}

//+------------------------------------------------------------------+
//| Effective step/tp (points) for the current signal + mode tag:    |
//|   "S" — sig.use_atr=false (signal step/tp used directly)         |
//|   "A" — sig.use_atr=true  and ATR-derived values applied         |
//|   "F" — sig.use_atr=true  but ATR unavailable → fallback to      |
//|         sig.step/sig.tp (handle invalid or buffer warming up)    |
//| Mode is embedded in the per-position comment for post-hoc audit. |
//+------------------------------------------------------------------+
void EffectiveStepTpPts(const GvfxSig &sig, int &stepPts, int &tpPts, string &mode) {
   if (!sig.use_atr) {
      stepPts = sig.step;
      tpPts   = sig.tp;
      mode    = "S";
      return;
   }
   double buf[];
   if (g_atrHandle == INVALID_HANDLE
       || CopyBuffer(g_atrHandle, 0, 0, 1, buf) <= 0
       || buf[0] <= 0) {
      stepPts = sig.step;
      tpPts   = sig.tp;
      mode    = "F";
      return;
   }
   double atrPrice = buf[0];
   int    pt       = (_Point > 0) ? (int)MathRound(atrPrice / _Point) : 0;
   int    stepRaw  = (int)MathRound(pt * g_cfg_AtrStepMult);
   int    tpRaw    = (int)MathRound(pt * g_cfg_AtrTpMult);
   int    lo       = MathMax(1, g_cfg_AtrMinPts);
   int    hi       = MathMax(lo, g_cfg_AtrMaxPts);
   stepPts = MathMax(lo, MathMin(hi, stepRaw));
   tpPts   = MathMax(lo, MathMin(hi, tpRaw));
   mode    = "A";
}

//+------------------------------------------------------------------+
void OnTick() {
   datetime now = TimeCurrent();  // broker-local, throttle only — không dùng cho dedup/timestamp
   if (now == g_lastTickCheck) return;
   g_lastTickCheck = now;

   RefreshDailyAnchor();

   RefreshOpenStats(g_openCount, g_floating);

   //--- EOD cut window (only fires within InpEodCutLeadMins of session close):
   //      total > 0 → close everything; suppress re-entry until next day.
   //      total < 0 → trim losers (most-negative first) while projected daily
   //                  realized stays ≥ 0; suppress re-entry until next day.
   if (g_eodCutDoneAnchor != g_dailyAnchor && IsEodWindow() && g_openCount > 0) {
      double total = g_dailyRealized + g_floating;
      if (total > 0) {
         PrintFormat("[GVFX EOD CUT] realized=%.2f floating=%.2f total=%.2f → close all, pause until next day",
                     g_dailyRealized, g_floating, total);
         CloseAllAndCancel();
         ArmEodSuppression();
         return;
      }
      if (total < 0) {
         PrintFormat("[GVFX EOD trim] realized=%.2f floating=%.2f total=%.2f → trim losers, pause until next day",
                     g_dailyRealized, g_floating, total);
         PartialEodTrimLosers();
         ArmEodSuppression();
         return;
      }
   }

   //--- Account disabled via JSON config: skip new entries but track signal ts
   if (!g_cfg_Enabled) return;

   //--- Load current signal
   GvfxSig sig;
   if (!LoadSignal(g_signalFile, sig)) return;

   //--- New signal detection. Consult the persisted reached-ts marker so a
   //    previously-deactivated signal can't resurrect when it looks "new" to
   //    in-memory state — fresh chart, terminal redeploy after history rolled
   //    out of the lookback window, or any path where ScanMaxSeenTimestamp
   //    returns 0 (or diverges from the file ts) while the GlobalVariable
   //    GVFX_Reached_* still records the kill.
   if (sig.timestamp != g_lastSigTs) {
      g_currentSig   = sig;
      g_lastSigTs    = sig.timestamp;
      g_signalActive = !SignalAlreadyReached(sig.timestamp);
      int    effStep, effTp;
      string effMode;
      EffectiveStepTpPts(g_currentSig, effStep, effTp, effMode);
      PrintFormat("[GVFX] New signal ts=%s dir=%s target=%.5f step=%d tp=%d low=%.5f high=%.5f atr=%s effStep=%d effTp=%d mode=%s active=%s",
                  IntegerToString(sig.timestamp), sig.direction,
                  sig.target, sig.step, sig.tp, sig.low, sig.high,
                  sig.use_atr ? "true" : "false", effStep, effTp, effMode,
                  g_signalActive ? "true" : "false");
   }

   //--- Target reached → deactivate signal (don't enter more). Persist the
   //    reached-ts so a redeploy doesn't resurrect the signal when price
   //    retreats to the unfavorable side of target.
   if (g_signalActive && TargetReached(g_currentSig)) {
      g_signalActive = false;
      MarkSignalReached(g_currentSig.timestamp);
      Print("[GVFX] Target reached — signal deactivated; existing positions continue");
   }

   //--- Entry attempt: re-enter freely as long as no existing position is within
   //    ±step radius of the current price (price-based grid spacing, not last-entry).
   if (!g_signalActive) return;
   if (g_eodCutDoneAnchor == g_dailyAnchor && g_eodCutDoneAnchor > 0) return;
   if (g_openCount >= g_cfg_MaxPositions) return;
   if (!IsSpreadOK("Entry")) return;

   bool   isBuy      = (g_currentSig.direction == "BUY");
   double entryPrice = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                             : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   int    effStepPts, effTpPts;
   string mode;
   EffectiveStepTpPts(g_currentSig, effStepPts, effTpPts, mode);
   double stepP      = effStepPts * _Point;

   //--- High/low price-zone gate (optional per signal):
   //      BUY  → only enter when price > low  (floor)
   //      SELL → only enter when price < high (ceiling)
   if ( isBuy && g_currentSig.low  > 0 && entryPrice <= g_currentSig.low)  return;
   if (!isBuy && g_currentSig.high > 0 && entryPrice >= g_currentSig.high) return;

   //--- Target proximity guard: skip when entry is within one grid step of
   //    target. Otherwise the position's TP (≈ effStep * 0.95) lands past
   //    target — when target is hit, signal deactivates but the position
   //    keeps a TP it may never reach, dangling until SL or oscillation.
   double stepDistP = effStepPts * _Point;
   double targetGap = isBuy ? (g_currentSig.target - entryPrice)
                            : (entryPrice - g_currentSig.target);
   if (targetGap < stepDistP) return;

   if (HasOpenWithinStep(entryPrice, stepP)) return;

   OpenMarket(isBuy, entryPrice, effTpPts, mode);
}

//+------------------------------------------------------------------+
//| Open one market position with hard SL + TP per signal             |
//+------------------------------------------------------------------+
bool OpenMarket(const bool isBuy, const double triggerRef, const int tpPts, const string mode) {
   double lot = NormalizeLot(g_cfg_LotPerOrder);
   if (lot <= 0) {
      Print("[GVFX] lot normalized to 0 — skip");
      return false;
   }

   double entry = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                        : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   ENUM_POSITION_TYPE dir = isBuy ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;

   double slRaw = isBuy ? entry - g_cfg_MaxLossPtsPerOrder * _Point
                        : entry + g_cfg_MaxLossPtsPerOrder * _Point;
   double tpRaw = isBuy ? entry + tpPts * _Point
                        : entry - tpPts * _Point;

   double sl = ClampStop(dir, slRaw, true);
   double tp = ClampStop(dir, tpRaw, false);

   //--- Comment carries dedup ts + execution-mode suffix (A/F/S) for post-hoc audit.
   //--- ParseTsFromComment() reads the ts via StringToInteger which stops at the
   //--- first non-digit, so the trailing "_X" is naturally ignored on restart scan.
   string comment = StringFormat("GVFX_T%s_%s", IntegerToString(g_currentSig.timestamp), mode);

   bool ok = isBuy ? g_trade.Buy (lot, _Symbol, 0.0, sl, tp, comment)
                   : g_trade.Sell(lot, _Symbol, 0.0, sl, tp, comment);

   PrintFormat("[GVFX %s] mode=%s level=%d lot=%.2f entry=%.5f sl=%.5f tp=%.5f trig=%.5f  %s",
               isBuy ? "BUY" : "SELL", mode, g_openCount + 1, lot, entry, sl, tp, triggerRef,
               ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());

   if (ok) g_openCount++;
   return ok;
}

//+------------------------------------------------------------------+
//| Spread gate                                                      |
//+------------------------------------------------------------------+
bool IsSpreadOK(const string tag) {
   if (g_cfg_MaxSpreadPts <= 0) return true;
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if (spread > g_cfg_MaxSpreadPts) {
      static datetime lastWarn = 0;
      if (TimeCurrent() - lastWarn >= 30) {  // broker-local, throttle only — warn rate-limiter
         PrintFormat("[%s SKIP] Spread %d pts > max %d pts", tag, (int)spread, (int)g_cfg_MaxSpreadPts);
         lastWarn = TimeCurrent();  // broker-local, throttle only — warn rate-limiter anchor
      }
      return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Has price reached the target?                                    |
//+------------------------------------------------------------------+
bool TargetReached(const GvfxSig &sig) {
   if (sig.direction == "BUY")
      return SymbolInfoDouble(_Symbol, SYMBOL_BID) >= sig.target;
   return SymbolInfoDouble(_Symbol, SYMBOL_ASK) <= sig.target;
}

//+------------------------------------------------------------------+
//| Refresh midnight server-time anchor and recompute realized P&L   |
//+------------------------------------------------------------------+
void RefreshDailyAnchor() {
   datetime srv = TimeTradeServer();  // server-local — EOD anchor phải theo broker TZ, không phải UTC
   MqlDateTime mdt; TimeToStruct(srv, mdt);
   mdt.hour = 0; mdt.min = 0; mdt.sec = 0;
   datetime midnight = StructToTime(mdt);
   if (midnight != g_dailyAnchor) {
      g_dailyAnchor = midnight;
      if (g_eodCutDoneAnchor != 0 && g_eodCutDoneAnchor < g_dailyAnchor) {
         g_eodCutDoneAnchor = 0;
         GlobalVariableDel(EodAnchorVarName());
         Print("[GVFX] New day — EOD cut suppression cleared");
      }
   }
   g_dailyRealized = ComputeRealizedSince(g_dailyAnchor);
}

//+------------------------------------------------------------------+
//| Today's last broker trading-session close as absolute datetime.  |
//| 0 if the broker reports no session today (e.g. weekend).         |
//+------------------------------------------------------------------+
datetime TodaySessionCloseTime() {
   datetime srv = TimeTradeServer();  // server-local — session windows (DOW + to/from) là broker TZ
   MqlDateTime mdt; TimeToStruct(srv, mdt);
   mdt.hour = 0; mdt.min = 0; mdt.sec = 0;
   datetime midnight = StructToTime(mdt);
   ENUM_DAY_OF_WEEK dow = (ENUM_DAY_OF_WEEK)mdt.day_of_week;

   datetime from, to, maxTo = 0;
   for (uint i = 0; SymbolInfoSessionTrade(_Symbol, dow, i, from, to); i++)
      if (to > maxTo) maxTo = to;

   if (maxTo == 0) return 0;
   //--- 'to' is anchored on 1970-01-01; map to seconds-of-day, treating 24:00 as 86400.
   long sec = (long)maxTo % 86400;
   if (sec == 0 && maxTo > 0) sec = 86400;
   return midnight + (datetime)sec;
}

//+------------------------------------------------------------------+
//| Server time has reached "lead" minutes before today's session    |
//| close — open the EOD cut evaluation window.                      |
//+------------------------------------------------------------------+
bool IsEodWindow() {
   if (g_cfg_EodCutLeadMins < 0) return false;
   datetime closeAt = TodaySessionCloseTime();
   if (closeAt == 0) return false;
   datetime triggerAt = closeAt - (datetime)(g_cfg_EodCutLeadMins * 60);
   return TimeTradeServer() >= triggerAt;  // server-local — so sánh với triggerAt cũng từ server TZ
}

//+------------------------------------------------------------------+
//| Per-instance global var name for EOD-cut anchor persistence      |
//+------------------------------------------------------------------+
string EodAnchorVarName() {
   return "GVFX_EodCut_" + IntegerToString((long)g_cfg_Magic) + "_" + _Symbol;
}

//+------------------------------------------------------------------+
//| Mark today as EOD-cut-done so entries are suppressed until the   |
//| next server-time day. Persisted via GlobalVariable for restart   |
//| safety. Used by both the full-close and partial-trim branches.   |
//+------------------------------------------------------------------+
void ArmEodSuppression() {
   g_eodCutDoneAnchor = g_dailyAnchor;
   GlobalVariableSet(EodAnchorVarName(), (double)g_eodCutDoneAnchor);
}

//+------------------------------------------------------------------+
//| Per-instance global var name for "target was reached" marker.    |
//| Stores the timestamp of the most recent signal that hit target;  |
//| OnInit compares against probe.timestamp to keep the signal dead  |
//| across restarts even if price has since retreated.               |
//+------------------------------------------------------------------+
string ReachedTsVarName() {
   return "GVFX_Reached_" + IntegerToString((long)g_cfg_Magic) + "_" + _Symbol;
}

bool SignalAlreadyReached(const ulong ts) {
   if (ts == 0) return false;
   string n = ReachedTsVarName();
   if (!GlobalVariableCheck(n)) return false;
   return (ulong)GlobalVariableGet(n) == ts;
}

void MarkSignalReached(const ulong ts) {
   if (ts == 0) return;
   GlobalVariableSet(ReachedTsVarName(), (double)ts);
}

//+------------------------------------------------------------------+
//| Sum profit + swap + commission of closed deals on this magic+sym |
//+------------------------------------------------------------------+
double ComputeRealizedSince(const datetime fromTime) {
   double total = 0;
   if (!HistorySelect(fromTime, TimeCurrent() + 60)) return 0;  // broker-local OK — upper bound chỉ cần > now
   int n = HistoryDealsTotal();
   for (int i = 0; i < n; i++) {
      ulong d = HistoryDealGetTicket(i);
      if (d == 0) continue;
      if (HistoryDealGetInteger(d, DEAL_MAGIC)  != (long)g_cfg_Magic) continue;
      if (HistoryDealGetString(d, DEAL_SYMBOL)  != _Symbol)        continue;
      total += HistoryDealGetDouble(d, DEAL_PROFIT)
             + HistoryDealGetDouble(d, DEAL_SWAP)
             + HistoryDealGetDouble(d, DEAL_COMMISSION);
   }
   return total;
}

//+------------------------------------------------------------------+
//| Tally open positions on this magic+symbol; report worst-adverse  |
//+------------------------------------------------------------------+
void RefreshOpenStats(int &openCount, double &floating) {
   openCount = 0;
   floating  = 0;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong t = PositionGetTicket(i);
      if (!PositionSelectByTicket(t))                              continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)g_cfg_Magic)  continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)            continue;
      openCount++;
      floating += PositionGetDouble(POSITION_PROFIT)
                + PositionGetDouble(POSITION_SWAP);
   }
}

//+------------------------------------------------------------------+
//| True if any open position on this magic+symbol has open price    |
//| within ±stepPrice of the candidate price.                        |
//+------------------------------------------------------------------+
bool HasOpenWithinStep(const double price, const double stepPrice) {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong t = PositionGetTicket(i);
      if (!PositionSelectByTicket(t))                               continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)g_cfg_Magic)  continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)            continue;
      double e = PositionGetDouble(POSITION_PRICE_OPEN);
      if (MathAbs(price - e) < stepPrice) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Close all positions + cancel pendings of this magic+symbol       |
//+------------------------------------------------------------------+
void CloseAllAndCancel() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong t = PositionGetTicket(i);
      if (!PositionSelectByTicket(t))                               continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)g_cfg_Magic)  continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)            continue;
      bool ok = g_trade.PositionClose(t);
      PrintFormat("[GVFX cut] Close #%d  %s", t,
                  ok ? "OK" : "FAILED: " + g_trade.ResultRetcodeDescription());
   }
   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong t = OrderGetTicket(i);
      if (t == 0)                                                   continue;
      if (OrderGetInteger(ORDER_MAGIC)  != (long)g_cfg_Magic)       continue;
      if (OrderGetString(ORDER_SYMBOL)  != _Symbol)                 continue;
      bool ok = g_trade.OrderDelete(t);
      PrintFormat("[GVFX cut] Cancel pending #%d  %s", t,
                  ok ? "OK" : "FAILED: " + g_trade.ResultRetcodeDescription());
   }
   //--- Signal stays active so EA re-arms next tick (subject to ±step gating)
}

//+------------------------------------------------------------------+
//| Trim biggest losers (most-negative first) while the projected    |
//| daily realized P&L stays ≥ 0. Stops before a cut would push it   |
//| into the red. Idempotent: safe to re-run each tick.              |
//+------------------------------------------------------------------+
void PartialEodTrimLosers() {
   ulong  losers_t[];
   double losers_p[];
   ArrayResize(losers_t, 0);
   ArrayResize(losers_p, 0);

   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong t = PositionGetTicket(i);
      if (!PositionSelectByTicket(t))                               continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)g_cfg_Magic)  continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)            continue;
      double pnl = PositionGetDouble(POSITION_PROFIT)
                 + PositionGetDouble(POSITION_SWAP);
      if (pnl >= 0) continue;
      int n = ArraySize(losers_t);
      ArrayResize(losers_t, n + 1);
      ArrayResize(losers_p, n + 1);
      losers_t[n] = t;
      losers_p[n] = pnl;
   }

   int n = ArraySize(losers_t);
   if (n == 0) return;

   //--- Insertion sort by P&L ascending (most negative first)
   for (int i = 1; i < n; i++) {
      double key_p = losers_p[i];
      ulong  key_t = losers_t[i];
      int    j     = i - 1;
      while (j >= 0 && losers_p[j] > key_p) {
         losers_p[j + 1] = losers_p[j];
         losers_t[j + 1] = losers_t[j];
         j--;
      }
      losers_p[j + 1] = key_p;
      losers_t[j + 1] = key_t;
   }

   double running = g_dailyRealized;
   int    closed  = 0;
   for (int i = 0; i < n; i++) {
      double candidate = running + losers_p[i];
      if (candidate < 0) break;
      bool ok = g_trade.PositionClose(losers_t[i]);
      if (ok) {
         running = candidate;
         closed++;
         PrintFormat("[GVFX EOD trim] Close #%d pnl=%.2f → projected realized=%.2f",
                     losers_t[i], losers_p[i], running);
      } else {
         PrintFormat("[GVFX EOD trim] FAIL close #%d: %s",
                     losers_t[i], g_trade.ResultRetcodeDescription());
      }
   }
   if (closed > 0)
      PrintFormat("[GVFX EOD trim] cut %d/%d losers; projected daily realized=%.2f",
                  closed, n, running);
}

//+------------------------------------------------------------------+
//| Normalize lot to broker step/min/max                             |
//+------------------------------------------------------------------+
double NormalizeLot(const double raw) {
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double mn   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double mx   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   if (step <= 0) step = 0.01;
   double v = MathFloor(raw / step) * step;
   if (v < mn) v = mn;
   if (v > mx) v = mx;
   return NormalizeDouble(v, 2);
}

//+------------------------------------------------------------------+
//| Clamp SL/TP to broker's minimum stop distance                    |
//+------------------------------------------------------------------+
double ClampStop(const ENUM_POSITION_TYPE dir, const double rawPrice, const bool isSL) {
   double price    = rawPrice;
   long   stopsLvl = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double minDist  = stopsLvl * _Point;

   if (minDist > 0) {
      double ref = (dir == POSITION_TYPE_BUY)
                   ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                   : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double orig = price;
      if (dir == POSITION_TYPE_BUY) {
         if ( isSL && price > ref - minDist) price = ref - minDist;
         if (!isSL && price < ref + minDist) price = ref + minDist;
      } else {
         if ( isSL && price < ref + minDist) price = ref + minDist;
         if (!isSL && price > ref - minDist) price = ref - minDist;
      }
      if (MathAbs(price - orig) > _Point / 2)
         PrintFormat("[Clamp] %s %s %.5f -> %.5f (broker min dist %.0f pts)",
                     dir == POSITION_TYPE_BUY ? "BUY" : "SELL",
                     isSL ? "SL" : "TP", orig, price, (double)stopsLvl);
   }
   return NormalizeDouble(price, _Digits);
}

//+------------------------------------------------------------------+
//| Max ts seen in any GVFX_T{ts}[_X] comment — restart-safe dedup   |
//+------------------------------------------------------------------+
ulong ScanMaxSeenTimestamp() {
   ulong maxTs = 0;

   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong t = PositionGetTicket(i);
      if (!PositionSelectByTicket(t))                               continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)            continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)g_cfg_Magic)  continue;
      ulong ts = ParseTsFromComment(PositionGetString(POSITION_COMMENT));
      if (ts > maxTs) maxTs = ts;
   }

   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong t = OrderGetTicket(i);
      if (t == 0)                                                   continue;
      if (OrderGetString(ORDER_SYMBOL)  != _Symbol)                 continue;
      if (OrderGetInteger(ORDER_MAGIC)  != (long)g_cfg_Magic)       continue;
      ulong ts = ParseTsFromComment(OrderGetString(ORDER_COMMENT));
      if (ts > maxTs) maxTs = ts;
   }

   datetime from = TimeCurrent() - (datetime)(g_cfg_HistoryLookbackDays * 86400);  // broker-local OK — window bounds chỉ dùng cho HistorySelect
   if (HistorySelect(from, TimeCurrent() + 60)) {  // broker-local OK — upper bound chỉ cần > now
      int deals = HistoryDealsTotal();
      for (int i = deals - 1; i >= 0; i--) {
         ulong d = HistoryDealGetTicket(i);
         if (d == 0)                                                          continue;
         if (HistoryDealGetString(d, DEAL_SYMBOL)  != _Symbol)                continue;
         if (HistoryDealGetInteger(d, DEAL_MAGIC)  != (long)g_cfg_Magic)       continue;
         ulong ts = ParseTsFromComment(HistoryDealGetString(d, DEAL_COMMENT));
         if (ts > maxTs) maxTs = ts;
      }
      int orders = HistoryOrdersTotal();
      for (int i = orders - 1; i >= 0; i--) {
         ulong o = HistoryOrderGetTicket(i);
         if (o == 0)                                                          continue;
         if (HistoryOrderGetString(o, ORDER_SYMBOL)  != _Symbol)              continue;
         if (HistoryOrderGetInteger(o, ORDER_MAGIC)  != (long)g_cfg_Magic)     continue;
         ulong ts = ParseTsFromComment(HistoryOrderGetString(o, ORDER_COMMENT));
         if (ts > maxTs) maxTs = ts;
      }
   }
   return maxTs;
}

//+------------------------------------------------------------------+
//| Extract ts from "GVFX_T{ts}" or "GVFX_T{ts}_{A|F|S}". 0 if not   |
//| our format. StringToInteger stops at the first non-digit so the  |
//| trailing "_X" mode suffix is ignored — older positions written    |
//| before the suffix existed still parse identically.                |
//+------------------------------------------------------------------+
ulong ParseTsFromComment(const string comment) {
   if (StringFind(comment, "GVFX_T") != 0) return 0;
   return (ulong)StringToInteger(StringSubstr(comment, 6));
}

//+------------------------------------------------------------------+
//| Master loader: read file → validate → populate struct            |
//+------------------------------------------------------------------+
bool LoadSignal(const string filename, GvfxSig &sig) {
   ZeroMemory(sig);
   sig.valid = false;

   string json = ReadFileToString(filename);
   if (json == "") return false;

   string ts_str   = JsonGetString(json, "timestamp");
   string sym_str  = JsonGetString(json, "symbol");
   string dir_str  = JsonGetString(json, "direction");
   string tgt_str  = JsonGetString(json, "target");
   string step_str = JsonGetString(json, "step");
   string tp_str   = JsonGetString(json, "tp");
   string low_str  = JsonGetString(json, "low");
   string high_str = JsonGetString(json, "high");
   string atr_str  = JsonGetString(json, "use_atr");

   if (ts_str   == "") { Print("[Validation] Missing: timestamp"); return false; }
   if (sym_str  == "") { Print("[Validation] Missing: symbol");    return false; }
   if (dir_str  == "") { Print("[Validation] Missing: direction"); return false; }
   if (tgt_str  == "") { Print("[Validation] Missing: target");    return false; }
   if (step_str == "") { Print("[Validation] Missing: step");      return false; }
   if (tp_str   == "") { Print("[Validation] Missing: tp");        return false; }

   if (sym_str != _Symbol) {
      PrintFormat("[Validation] Symbol mismatch: file=%s chart=%s", sym_str, _Symbol);
      return false;
   }

   StringToUpper(dir_str);
   if (dir_str != "BUY" && dir_str != "SELL") {
      PrintFormat("[Validation] Invalid direction: %s", dir_str);
      return false;
   }

   double target = StringToDouble(tgt_str);
   int    step   = (int)StringToInteger(step_str);
   int    tp     = (int)StringToInteger(tp_str);
   if (target <= 0) { Print("[Validation] target <= 0"); return false; }
   if (step   <= 0) { Print("[Validation] step <= 0");   return false; }
   if (tp     <= 0) { Print("[Validation] tp <= 0");     return false; }

   //--- low/high are optional. Missing field or "null" → 0 (disabled).
   double low  = (low_str  == "" || low_str  == "null") ? 0.0 : StringToDouble(low_str);
   double high = (high_str == "" || high_str == "null") ? 0.0 : StringToDouble(high_str);
   if (low  < 0) { Print("[Validation] low < 0");  return false; }
   if (high < 0) { Print("[Validation] high < 0"); return false; }
   if (low > 0 && high > 0 && low >= high) {
      PrintFormat("[Validation] low (%.5f) must be < high (%.5f)", low, high);
      return false;
   }

   ulong ts = (ulong)StringToInteger(ts_str);
   if (ts == 0) { Print("[Validation] timestamp == 0"); return false; }

   sig.timestamp = ts;
   sig.symbol    = sym_str;
   sig.direction = dir_str;
   sig.target    = target;
   sig.step      = step;
   sig.tp        = tp;
   sig.low       = low;
   sig.high      = high;

   //--- use_atr is optional. Missing field or "null" → true (default).
   //--- Accepts "true"/"false"/"1"/"0" (case-insensitive).
   bool useAtr = true;
   if (atr_str != "" && atr_str != "null") {
      string a = atr_str;
      StringToLower(a);
      useAtr = !(a == "false" || a == "0" || a == "no");
   }
   sig.use_atr   = useAtr;
   sig.valid     = true;
   return true;
}

//+------------------------------------------------------------------+
//| JSON typed getters (complement the existing JsonGetString)       |
//+------------------------------------------------------------------+
bool JsonGetBool(const string json, const string key, bool defval) {
   string v = JsonGetString(json, key);
   if (v == "") return defval;
   if (v == "true" || v == "1") return true;
   if (v == "false" || v == "0") return false;
   return defval;
}

double JsonGetDouble(const string json, const string key, double defval) {
   string v = JsonGetString(json, key);
   if (v == "") return defval;
   return StringToDouble(v);
}

long JsonGetLong(const string json, const string key, long defval) {
   string v = JsonGetString(json, key);
   if (v == "") return defval;
   return StringToInteger(v);
}

//+------------------------------------------------------------------+
//| Copy every input into its shadow global (called first in OnInit) |
//+------------------------------------------------------------------+
void InitShadowsFromInputs() {
   g_cfg_Magic               = InpMagic;
   g_cfg_SignalSubdir        = InpSignalSubdir;
   g_cfg_LotPerOrder         = InpLotPerOrder;
   g_cfg_MaxPositions        = InpMaxPositions;
   g_cfg_MaxLossPtsPerOrder  = InpMaxLossPtsPerOrder;
   g_cfg_EodCutLeadMins      = InpEodCutLeadMins;
   g_cfg_MaxSpreadPts        = InpMaxSpreadPts;
   g_cfg_HistoryLookbackDays = InpHistoryLookbackDays;
   g_cfg_AtrTimeframe        = InpAtrTimeframe;
   g_cfg_AtrPeriod           = InpAtrPeriod;
   g_cfg_AtrStepMult         = InpAtrStepMult;
   g_cfg_AtrTpMult           = InpAtrTpMult;
   g_cfg_AtrMinPts           = InpAtrMinPts;
   g_cfg_AtrMaxPts           = InpAtrMaxPts;
   g_cfg_Enabled             = true;
}

//+------------------------------------------------------------------+
//| Load per-account JSON overlay from GvfxSignalEA\config\<acc>.json|
//| Falls back silently to defaults (shadow already init'd from Inp*)|
//+------------------------------------------------------------------+
void LoadAccountConfig() {
   long   account = AccountInfoInteger(ACCOUNT_LOGIN);
   string path    = "GvfxSignalEA\\config\\" + IntegerToString(account) + ".json";
   string json    = ReadFileToString(path);
   if (json == "") {
      PrintFormat("[Config] No override at %s — defaults", path);
      return;
   }

   g_cfg_Enabled             = JsonGetBool  (json, "enabled",                true);
   g_cfg_Magic               = (ulong)JsonGetLong  (json, "InpMagic",               (long)g_cfg_Magic);
   string _subdir = JsonGetString(json, "InpSignalSubdir");
   if (_subdir != "") g_cfg_SignalSubdir = _subdir;   // keep existing shadow if key absent
   g_cfg_LotPerOrder         = JsonGetDouble(json, "InpLotPerOrder",         g_cfg_LotPerOrder);
   g_cfg_MaxPositions        = (int)JsonGetLong  (json, "InpMaxPositions",        (long)g_cfg_MaxPositions);
   g_cfg_MaxLossPtsPerOrder  = (int)JsonGetLong  (json, "InpMaxLossPtsPerOrder",  (long)g_cfg_MaxLossPtsPerOrder);
   g_cfg_EodCutLeadMins      = (int)JsonGetLong  (json, "InpEodCutLeadMins",      (long)g_cfg_EodCutLeadMins);
   g_cfg_MaxSpreadPts        = (int)JsonGetLong  (json, "InpMaxSpreadPts",        (long)g_cfg_MaxSpreadPts);
   g_cfg_HistoryLookbackDays = (int)JsonGetLong  (json, "InpHistoryLookbackDays", (long)g_cfg_HistoryLookbackDays);
   // InpAtrTimeframe intentionally skipped — enum cast from arbitrary int is unsafe
   g_cfg_AtrPeriod           = (int)JsonGetLong  (json, "InpAtrPeriod",           (long)g_cfg_AtrPeriod);
   g_cfg_AtrStepMult         = JsonGetDouble(json, "InpAtrStepMult",         g_cfg_AtrStepMult);
   g_cfg_AtrTpMult           = JsonGetDouble(json, "InpAtrTpMult",           g_cfg_AtrTpMult);
   g_cfg_AtrMinPts           = (int)JsonGetLong  (json, "InpAtrMinPts",           (long)g_cfg_AtrMinPts);
   g_cfg_AtrMaxPts           = (int)JsonGetLong  (json, "InpAtrMaxPts",           (long)g_cfg_AtrMaxPts);

   PrintFormat("[Config] Loaded acc=%I64d enabled=%s magic=%I64u lot=%.2f maxpos=%d",
               account, g_cfg_Enabled ? "true" : "false",
               g_cfg_Magic, g_cfg_LotPerOrder, g_cfg_MaxPositions);
}

//+------------------------------------------------------------------+
//| Read entire file into a string                                   |
//+------------------------------------------------------------------+
string ReadFileToString(const string filename) {
   int flags = FILE_READ | FILE_TXT | FILE_ANSI;
   if (InpUseCommonDir) flags |= FILE_COMMON;

   int h = FileOpen(filename, flags);
   if (h == INVALID_HANDLE) {
      flags ^= FILE_COMMON;
      h = FileOpen(filename, flags);
      if (h == INVALID_HANDLE) return "";
   }

   string result = "";
   while (!FileIsEnding(h))
      result += FileReadString(h);
   FileClose(h);
   return result;
}

//+------------------------------------------------------------------+
//| Locate `"key"` followed by `:` (skipping whitespace).            |
//+------------------------------------------------------------------+
int FindJsonKey(const string json, const string key) {
   string needle = "\"" + key + "\"";
   int    from   = 0;
   int    len    = StringLen(json);

   while (from < len) {
      int p = StringFind(json, needle, from);
      if (p < 0) return -1;
      int after = p + StringLen(needle);
      while (after < len) {
         ushort c = StringGetCharacter(json, after);
         if (c != ' ' && c != '\t' && c != '\n' && c != '\r') break;
         after++;
      }
      if (after < len && StringGetCharacter(json, after) == ':')
         return after + 1;
      from = p + 1;
   }
   return -1;
}

//+------------------------------------------------------------------+
//| Extract a scalar value (string or numeric) for a given JSON key  |
//+------------------------------------------------------------------+
string JsonGetString(const string json, const string key) {
   int pos = FindJsonKey(json, key);
   if (pos < 0) return "";
   int len = StringLen(json);

   while (pos < len) {
      ushort c = StringGetCharacter(json, pos);
      if (c != ' ' && c != '\t' && c != '\n' && c != '\r') break;
      pos++;
   }
   if (pos >= len) return "";

   ushort first = StringGetCharacter(json, pos);

   if (first == '"') {
      pos++;
      string val = "";
      while (pos < len) {
         ushort c = StringGetCharacter(json, pos++);
         if (c == '\\' && pos < len) {
            val += ShortToString(StringGetCharacter(json, pos++));
            continue;
         }
         if (c == '"') break;
         val += ShortToString(c);
      }
      return val;
   }
   if (first == '[' || first == '{') return "";

   string val = "";
   while (pos < len) {
      ushort c = StringGetCharacter(json, pos);
      if (c == ',' || c == '}' || c == '\n' || c == '\r') break;
      val += ShortToString(c);
      pos++;
   }
   StringTrimLeft(val);
   StringTrimRight(val);
   return val;
}
//+------------------------------------------------------------------+
