//+------------------------------------------------------------------+
//|  GvfxSignalEA.mq5                                                |
//|  Grid DCA from a target-price signal with daily P&L cut          |
//+------------------------------------------------------------------+
#property copyright   "GvfxSignal EA"
#property version     "1.00"
#property description "Reads {account}_{symbol}.json, grid-DCA toward adverse side until target reached"

#include <Trade\Trade.mqh>

//--- Input Parameters
input ulong  InpMagic               = 770001;        // Magic number
input string InpSignalSubdir        = "GvfxSignalEA";// Subdir under MQL5/Files
input bool   InpUseCommonDir        = false;         // Use MT5 common Files folder
input double InpLotPerOrder         = 0.01;          // Lot per grid level
input int    InpMaxPositions        = 20;            // Max OPEN positions on this magic+symbol
input int    InpMaxLossPtsPerOrder  = 10000;         // Hard SL distance per order (points)
input double InpDailyCutUsd         = 100.0;         // (realized - floating) > X → close all
input int    InpMaxSpreadPts        = 30;            // Max spread (pts) to allow entry; 0 disables
input int    InpHistoryLookbackDays = 7;             // History window for restart-safe dedup

//+------------------------------------------------------------------+
//| Signal data structure                                            |
//+------------------------------------------------------------------+
struct GvfxSig {
   ulong    timestamp;
   string   symbol;
   string   direction;   // "BUY" or "SELL"
   double   target;
   int      step;        // points
   int      tp;          // points
   bool     valid;
};

//--- Globals
CTrade   g_trade;
string   g_signalFile;
datetime g_lastTickCheck = 0;
ulong    g_lastSigTs     = 0;
bool     g_signalActive  = false;
double   g_lastEntryPrice= 0;
int      g_openCount     = 0;
double   g_floating      = 0;
datetime g_dailyAnchor   = 0;
double   g_dailyRealized = 0;
GvfxSig  g_currentSig;

//+------------------------------------------------------------------+
int OnInit() {
   g_trade.SetExpertMagicNumber(InpMagic);
   ZeroMemory(g_currentSig);

   g_signalFile = InpSignalSubdir + "\\"
                + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))
                + "_" + _Symbol + ".json";

   g_lastSigTs = ScanMaxSeenTimestamp();

   //--- Reconstruct g_lastEntryPrice from worst-adverse open position
   double lastBuy = 0, lastSell = DBL_MAX;
   int dummyOpen = 0; double dummyFloat = 0;
   RefreshOpenStats(dummyOpen, dummyFloat, lastBuy, lastSell);
   if (lastBuy > 0)              g_lastEntryPrice = lastBuy;
   else if (lastSell < DBL_MAX)  g_lastEntryPrice = lastSell;
   else                          g_lastEntryPrice = 0;

   //--- If signal file already executed and target not yet reached → keep active
   GvfxSig probe;
   if (LoadSignal(g_signalFile, probe) && probe.timestamp == g_lastSigTs && g_lastSigTs > 0) {
      g_currentSig    = probe;
      g_signalActive  = !TargetReached(probe);
   }

   RefreshDailyAnchor();

   PrintFormat("[GVFX] Initialized. Signal=%s lastSigTs=%s active=%s lastEntry=%.5f dailyRealized=%.2f",
               g_signalFile, IntegerToString(g_lastSigTs),
               g_signalActive ? "true" : "false",
               g_lastEntryPrice, g_dailyRealized);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   PrintFormat("[GVFX] Removed. Reason: %d", reason);
}

//+------------------------------------------------------------------+
void OnTick() {
   datetime now = TimeCurrent();
   if (now == g_lastTickCheck) return;
   g_lastTickCheck = now;

   RefreshDailyAnchor();

   double lastBuy = 0, lastSell = DBL_MAX;
   RefreshOpenStats(g_openCount, g_floating, lastBuy, lastSell);

   //--- Daily cut: realized − floating > threshold → close all + cancel pendings
   if (g_dailyRealized - g_floating > InpDailyCutUsd) {
      PrintFormat("[GVFX] Daily cut: realized=%.2f floating=%.2f diff=%.2f > %.2f → close all",
                  g_dailyRealized, g_floating,
                  g_dailyRealized - g_floating, InpDailyCutUsd);
      CloseAllAndCancel();
      return;
   }

   //--- Load current signal
   GvfxSig sig;
   if (!LoadSignal(g_signalFile, sig)) return;

   //--- New signal detection
   if (sig.timestamp != g_lastSigTs) {
      g_currentSig     = sig;
      g_lastSigTs      = sig.timestamp;
      g_signalActive   = true;
      g_lastEntryPrice = 0;
      PrintFormat("[GVFX] New signal ts=%s dir=%s target=%.5f step=%d tp=%d",
                  IntegerToString(sig.timestamp), sig.direction,
                  sig.target, sig.step, sig.tp);
   }

   //--- Target reached → deactivate signal (don't enter more)
   if (g_signalActive && TargetReached(g_currentSig)) {
      g_signalActive = false;
      Print("[GVFX] Target reached — signal deactivated; existing positions continue");
   }

   //--- Entry attempt
   if (!g_signalActive) return;
   if (g_openCount >= InpMaxPositions) return;
   if (!IsSpreadOK("Entry")) return;

   bool   isBuy = (g_currentSig.direction == "BUY");
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double stepP = g_currentSig.step * _Point;

   bool shouldEnter = false;
   double trigger = 0;
   if (g_openCount == 0 || g_lastEntryPrice <= 0) {
      //--- First entry of this signal: price guard against target
      if (isBuy) {
         shouldEnter = (ask <= g_currentSig.target - stepP);
         trigger     = g_currentSig.target;
      } else {
         shouldEnter = (bid >= g_currentSig.target + stepP);
         trigger     = g_currentSig.target;
      }
   } else {
      //--- Subsequent entries: step from last entry, in adverse direction
      if (isBuy) {
         shouldEnter = (ask <= g_lastEntryPrice - stepP);
         trigger     = g_lastEntryPrice;
      } else {
         shouldEnter = (bid >= g_lastEntryPrice + stepP);
         trigger     = g_lastEntryPrice;
      }
   }

   if (shouldEnter)
      OpenMarket(isBuy, trigger);
}

//+------------------------------------------------------------------+
//| Open one market position with hard SL + TP per signal             |
//+------------------------------------------------------------------+
bool OpenMarket(const bool isBuy, const double triggerRef) {
   double lot = NormalizeLot(InpLotPerOrder);
   if (lot <= 0) {
      Print("[GVFX] lot normalized to 0 — skip");
      return false;
   }

   double entry = isBuy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                        : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   ENUM_POSITION_TYPE dir = isBuy ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;

   double slRaw = isBuy ? entry - InpMaxLossPtsPerOrder * _Point
                        : entry + InpMaxLossPtsPerOrder * _Point;
   double tpRaw = isBuy ? entry + g_currentSig.tp * _Point
                        : entry - g_currentSig.tp * _Point;

   double sl = ClampStop(dir, slRaw, true);
   double tp = ClampStop(dir, tpRaw, false);

   string comment = StringFormat("GVFX_T%s", IntegerToString(g_currentSig.timestamp));

   bool ok = isBuy ? g_trade.Buy (lot, _Symbol, 0.0, sl, tp, comment)
                   : g_trade.Sell(lot, _Symbol, 0.0, sl, tp, comment);

   PrintFormat("[GVFX %s] level=%d lot=%.2f entry=%.5f sl=%.5f tp=%.5f trig=%.5f  %s",
               isBuy ? "BUY" : "SELL", g_openCount + 1, lot, entry, sl, tp, triggerRef,
               ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());

   if (ok) {
      g_lastEntryPrice = entry;
      g_openCount++;
   }
   return ok;
}

//+------------------------------------------------------------------+
//| Spread gate                                                      |
//+------------------------------------------------------------------+
bool IsSpreadOK(const string tag) {
   if (InpMaxSpreadPts <= 0) return true;
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if (spread > InpMaxSpreadPts) {
      static datetime lastWarn = 0;
      if (TimeCurrent() - lastWarn >= 30) {
         PrintFormat("[%s SKIP] Spread %d pts > max %d pts", tag, (int)spread, (int)InpMaxSpreadPts);
         lastWarn = TimeCurrent();
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
   datetime srv = TimeTradeServer();
   MqlDateTime mdt; TimeToStruct(srv, mdt);
   mdt.hour = 0; mdt.min = 0; mdt.sec = 0;
   datetime midnight = StructToTime(mdt);
   if (midnight != g_dailyAnchor) {
      g_dailyAnchor = midnight;
   }
   g_dailyRealized = ComputeRealizedSince(g_dailyAnchor);
}

//+------------------------------------------------------------------+
//| Sum profit + swap + commission of closed deals on this magic+sym |
//+------------------------------------------------------------------+
double ComputeRealizedSince(const datetime fromTime) {
   double total = 0;
   if (!HistorySelect(fromTime, TimeCurrent() + 60)) return 0;
   int n = HistoryDealsTotal();
   for (int i = 0; i < n; i++) {
      ulong d = HistoryDealGetTicket(i);
      if (d == 0) continue;
      if (HistoryDealGetInteger(d, DEAL_MAGIC)  != (long)InpMagic) continue;
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
void RefreshOpenStats(int &openCount, double &floating,
                     double &lastBuy, double &lastSell) {
   openCount = 0;
   floating  = 0;
   lastBuy   = 0;
   lastSell  = DBL_MAX;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong t = PositionGetTicket(i);
      if (!PositionSelectByTicket(t))                              continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)    continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)           continue;
      openCount++;
      floating += PositionGetDouble(POSITION_PROFIT)
                + PositionGetDouble(POSITION_SWAP);
      double e = PositionGetDouble(POSITION_PRICE_OPEN);
      long   typ = PositionGetInteger(POSITION_TYPE);
      if (typ == POSITION_TYPE_BUY  && e > lastBuy)  lastBuy  = e;
      if (typ == POSITION_TYPE_SELL && e < lastSell) lastSell = e;
   }
}

//+------------------------------------------------------------------+
//| Close all positions + cancel pendings of this magic+symbol       |
//+------------------------------------------------------------------+
void CloseAllAndCancel() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong t = PositionGetTicket(i);
      if (!PositionSelectByTicket(t))                              continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)    continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)           continue;
      bool ok = g_trade.PositionClose(t);
      PrintFormat("[GVFX cut] Close #%d  %s", t,
                  ok ? "OK" : "FAILED: " + g_trade.ResultRetcodeDescription());
   }
   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong t = OrderGetTicket(i);
      if (t == 0)                                                  continue;
      if (OrderGetInteger(ORDER_MAGIC)  != (long)InpMagic)         continue;
      if (OrderGetString(ORDER_SYMBOL)  != _Symbol)                continue;
      bool ok = g_trade.OrderDelete(t);
      PrintFormat("[GVFX cut] Cancel pending #%d  %s", t,
                  ok ? "OK" : "FAILED: " + g_trade.ResultRetcodeDescription());
   }
   //--- Reset grid anchor; signal stays active so EA re-arms next tick
   g_lastEntryPrice = 0;
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
//| Max ts seen in any GVFX_T{ts} comment — restart-safe dedup       |
//+------------------------------------------------------------------+
ulong ScanMaxSeenTimestamp() {
   ulong maxTs = 0;

   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong t = PositionGetTicket(i);
      if (!PositionSelectByTicket(t))                              continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)           continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)InpMagic)    continue;
      ulong ts = ParseTsFromComment(PositionGetString(POSITION_COMMENT));
      if (ts > maxTs) maxTs = ts;
   }

   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong t = OrderGetTicket(i);
      if (t == 0)                                                  continue;
      if (OrderGetString(ORDER_SYMBOL)  != _Symbol)                continue;
      if (OrderGetInteger(ORDER_MAGIC)  != (long)InpMagic)         continue;
      ulong ts = ParseTsFromComment(OrderGetString(ORDER_COMMENT));
      if (ts > maxTs) maxTs = ts;
   }

   datetime from = TimeCurrent() - (datetime)(InpHistoryLookbackDays * 86400);
   if (HistorySelect(from, TimeCurrent() + 60)) {
      int deals = HistoryDealsTotal();
      for (int i = deals - 1; i >= 0; i--) {
         ulong d = HistoryDealGetTicket(i);
         if (d == 0)                                                          continue;
         if (HistoryDealGetString(d, DEAL_SYMBOL)  != _Symbol)                continue;
         if (HistoryDealGetInteger(d, DEAL_MAGIC)  != (long)InpMagic)         continue;
         ulong ts = ParseTsFromComment(HistoryDealGetString(d, DEAL_COMMENT));
         if (ts > maxTs) maxTs = ts;
      }
      int orders = HistoryOrdersTotal();
      for (int i = orders - 1; i >= 0; i--) {
         ulong o = HistoryOrderGetTicket(i);
         if (o == 0)                                                          continue;
         if (HistoryOrderGetString(o, ORDER_SYMBOL)  != _Symbol)              continue;
         if (HistoryOrderGetInteger(o, ORDER_MAGIC)  != (long)InpMagic)       continue;
         ulong ts = ParseTsFromComment(HistoryOrderGetString(o, ORDER_COMMENT));
         if (ts > maxTs) maxTs = ts;
      }
   }
   return maxTs;
}

//+------------------------------------------------------------------+
//| Extract trailing ts from "GVFX_T{ts}"; 0 if not our format       |
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

   ulong ts = (ulong)StringToInteger(ts_str);
   if (ts == 0) { Print("[Validation] timestamp == 0"); return false; }

   sig.timestamp = ts;
   sig.symbol    = sym_str;
   sig.direction = dir_str;
   sig.target    = target;
   sig.step      = step;
   sig.tp        = tp;
   sig.valid     = true;
   return true;
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
