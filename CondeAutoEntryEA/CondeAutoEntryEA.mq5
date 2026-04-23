//+------------------------------------------------------------------+
//|  CondeAutoEntryEA.mq5                                            |
//|  Opens one position per TP from a pre-computed JSON signal       |
//+------------------------------------------------------------------+
#property copyright   "CondeAutoEntry EA"
#property version     "1.01"
#property description "Reads {account}_{symbol}.json, market-fires at entry, one position per TP"

#include <Trade\Trade.mqh>

//--- Input Parameters
input double InpLotPerTarget        = 0.01;        // Lot size per TP position
input double InpMaxLotsPerPosition  = 0.05;        // Max lot size per individual position
input double InpMaxTotalLotsPerDir  = 0.30;        // Max total lots across all open positions in one direction
input int    InpMaxPositions        = 20;          // Max open EA positions per direction on this symbol
input double InpMaxSlippagePts      = 100;         // Max distance (points) between market and entry_price to fire
input double InpSlBufferPts         = 20;           // Extra SL buffer (points)
input ulong  InpMagic               = 20260421;    // Magic number
input bool   InpUseCommonDir        = true;        // Use MT5 common Files folder
input int    InpHistoryLookbackDays = 30;          // History window for restart-safe dedup

input bool   InpEnableTrailing      = true;        // Enable break-even + trailing stop
input double InpBeTriggerPts        = 300;         // Profit (pts) to move SL to break-even
input double InpBeOffsetPts         = 50;          // Offset beyond entry at BE (covers spread+commission)
input double InpTrailStartPts       = 400;         // Profit (pts) to start trailing past BE
input double InpTrailDistPts        = 200;         // SL trails this far behind current price (pts)
input double InpTrailStepPts        = 100;          // Minimum SL improvement before modify (anti-spam)

//+------------------------------------------------------------------+
//| Signal data structure                                            |
//+------------------------------------------------------------------+
struct CondeSignal {
   ulong    timestamp;
   string   symbol;
   string   direction;   // "BUY" or "SELL"
   double   entry_price;
   double   sl;
   double   tps[];
   bool     valid;
};

//--- Globals
CTrade      g_trade;
string      g_signalFile;
datetime    g_lastTickCheck = 0;
ulong       g_lastSigTs     = 0;   // timestamp of last successfully executed signal
ulong       g_lastWaitTs    = 0;   // timestamp we've already logged "waiting" for
CondeSignal g_sig;

//+------------------------------------------------------------------+
int OnInit() {
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints((ulong)InpMaxSlippagePts);
   ZeroMemory(g_sig);

   g_signalFile = "CondeAutoEntryEA\\"
                + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))
                + "_" + _Symbol + ".json";

   g_lastSigTs = ScanMaxSeenTimestamp();

   if (InpEnableTrailing && InpTrailDistPts >= InpTrailStartPts)
      PrintFormat("[WARN] InpTrailDistPts (%.0f) >= InpTrailStartPts (%.0f) — trail would lock a loss on activation",
                  InpTrailDistPts, InpTrailStartPts);

   PrintFormat("[CondeAutoEntryEA] Initialized. Signal=%s  lastSigTs=%s",
               g_signalFile, IntegerToString(g_lastSigTs));
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   Print("[CondeAutoEntryEA] Removed. Reason: ", reason);
}

//+------------------------------------------------------------------+
void OnTick() {
   datetime now = TimeCurrent();
   if (now == g_lastTickCheck) return;
   g_lastTickCheck = now;

   ManageTrailingStops();

   CondeSignal sig;
   if (!LoadSignal(g_signalFile, sig)) return;
   if (sig.timestamp == g_lastSigTs)   return;   // already executed

   //--- Price must be within InpMaxSlippagePts of entry_price
   double market = (sig.direction == "BUY")
                   ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                   : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double distPts = MathAbs(market - sig.entry_price) / _Point;
   if (distPts > InpMaxSlippagePts) {
      if (sig.timestamp != g_lastWaitTs) {
         PrintFormat("[Wait] %s %.5f is %.0f pts from entry %.5f (max %.0f) — holding",
                     sig.direction, market, distPts, sig.entry_price, InpMaxSlippagePts);
         g_lastWaitTs = sig.timestamp;
      }
      return;
   }

   g_sig = sig;
   if (OpenTrades(sig))
      g_lastSigTs = sig.timestamp;
}

//+------------------------------------------------------------------+
//| Open one position per TP, respecting position and lot caps.      |
//| Returns true iff every TP either succeeded or was already        |
//| accounted for (open position / historical deal) or was terminally|
//| blocked by a cap. A live broker failure returns false so the     |
//| caller retries on the next tick without re-opening prior TPs.    |
//+------------------------------------------------------------------+
bool OpenTrades(const CondeSignal &sig) {
   ENUM_POSITION_TYPE dir = (sig.direction == "BUY") ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   int    nTps  = ArraySize(sig.tps);
   string tsStr = IntegerToString(sig.timestamp);
   int    failed = 0;

   PrintFormat("[Signal] Applied — %s entry=%.5f sl=%.5f tps=%d ts=%s",
               sig.direction, sig.entry_price, sig.sl, nTps, tsStr);

   for (int i = 0; i < nTps; i++) {
      string comment = StringFormat("CAE_T%d_%s", i + 1, tsStr);

      //--- Skip TPs already accounted for (restart or partial-fill retry)
      if (PositionExistsByComment(comment) || HistoryDealExistsByComment(comment)) {
         PrintFormat("[SKIP] TP #%d — %s already recorded", i + 1, comment);
         continue;
      }

      //--- Cap: max positions per direction on this symbol
      int dirOpen = CountOpenPositions(dir);
      if (dirOpen >= InpMaxPositions) {
         PrintFormat("[SKIP] TP #%d — max %s positions (%d) reached",
                     i + 1, sig.direction, InpMaxPositions);
         break;
      }

      //--- Cap: per-position lot size (invariant across iterations — break on zero)
      double lot = NormalizeLot(MathMin(InpLotPerTarget, InpMaxLotsPerPosition));
      if (lot <= 0) {
         PrintFormat("[SKIP] TP #%d — lot size normalized to 0", i + 1);
         break;
      }

      //--- Cap: total lots in this direction
      double openedLots = SumOpenLots(dir);
      if (openedLots + lot > InpMaxTotalLotsPerDir + 1e-8) {
         PrintFormat("[SKIP] TP #%d — would exceed total lots cap (%.2f + %.2f > %.2f)",
                     i + 1, openedLots, lot, InpMaxTotalLotsPerDir);
         break;
      }

      double buffer = InpSlBufferPts * _Point;
      double slRaw  = (dir == POSITION_TYPE_BUY) ? sig.sl - buffer : sig.sl + buffer;
      double sl     = ClampStop(dir, slRaw,       true);
      double tp     = ClampStop(dir, sig.tps[i],  false);

      bool ok = (dir == POSITION_TYPE_BUY)
                ? g_trade.Buy (lot, _Symbol, 0.0, sl, tp, comment)
                : g_trade.Sell(lot, _Symbol, 0.0, sl, tp, comment);

      PrintFormat("[%s #%d] lots=%.2f sl=%.5f tp=%.5f  %s",
                  sig.direction, i + 1, lot, sl, tp,
                  ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
      if (!ok) failed++;
   }

   return failed == 0;
}

//+------------------------------------------------------------------+
//| Per-position break-even + trailing stop manager.                 |
//|  Stage 1: profit >= InpBeTriggerPts → SL to entry +/- BeOffset.  |
//|  Stage 2: profit >= InpTrailStartPts → SL trails TrailDist       |
//|           behind current price, gated by TrailStep.              |
//| SL only moves in the direction of profit — never backward.       |
//+------------------------------------------------------------------+
void ManageTrailingStops() {
   if (!InpEnableTrailing) return;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   for (int i = PositionsTotal() - 1; i >= 0; --i) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket))                         continue;
      if (PositionGetString(POSITION_SYMBOL)  != _Symbol)          continue;
      if (PositionGetInteger(POSITION_MAGIC)  != (long)InpMagic)   continue;

      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double tp        = PositionGetDouble(POSITION_TP);

      double profitPts = (type == POSITION_TYPE_BUY)
                         ? (bid - openPrice) / _Point
                         : (openPrice - ask) / _Point;
      if (profitPts < InpBeTriggerPts) continue;

      double desiredSL;
      string stage;
      if (profitPts >= InpTrailStartPts) {
         desiredSL = (type == POSITION_TYPE_BUY)
                     ? NormalizeDouble(bid - InpTrailDistPts * _Point, _Digits)
                     : NormalizeDouble(ask + InpTrailDistPts * _Point, _Digits);
         stage = "Trail";
      } else {
         double offset = InpBeOffsetPts * _Point;
         desiredSL = (type == POSITION_TYPE_BUY)
                     ? NormalizeDouble(openPrice + offset, _Digits)
                     : NormalizeDouble(openPrice - offset, _Digits);
         stage = "BE";
      }

      //--- Strictly improving + step threshold
      if (type == POSITION_TYPE_BUY) {
         if (desiredSL < currentSL + InpTrailStepPts * _Point) continue;
      } else {
         if (currentSL != 0 && desiredSL > currentSL - InpTrailStepPts * _Point) continue;
      }

      //--- Never cross TP
      if (tp > 0) {
         if (type == POSITION_TYPE_BUY  && desiredSL >= tp) continue;
         if (type == POSITION_TYPE_SELL && desiredSL <= tp) continue;
      }

      //--- Respect broker stops level; clamp can pull SL back toward price
      desiredSL = ClampStop(type, desiredSL, true);
      if (type == POSITION_TYPE_BUY  && desiredSL <= currentSL) continue;
      if (type == POSITION_TYPE_SELL && currentSL != 0 && desiredSL >= currentSL) continue;

      bool ok = g_trade.PositionModify(ticket, desiredSL, tp);
      PrintFormat("[%s] Ticket #%d SL: %.5f → %.5f (profit=%.0f pts)  %s",
                  stage, ticket, currentSL, desiredSL, profitPts,
                  ok ? "OK" : "FAILED: " + g_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Normalize lot size to broker step/min/max                        |
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
      double ref  = (dir == POSITION_TYPE_BUY)
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
//| Returns the number of EA positions open on this symbol/direction |
//+------------------------------------------------------------------+
int CountOpenPositions(const ENUM_POSITION_TYPE dir) {
   int count = 0;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (PositionSelectByTicket(ticket)) {
         if (PositionGetString(POSITION_SYMBOL) == _Symbol          &&
             PositionGetInteger(POSITION_MAGIC) == (long)InpMagic   &&
             PositionGetInteger(POSITION_TYPE)  == (long)dir)
            count++;
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Sum of open lots for this EA in a given direction                |
//+------------------------------------------------------------------+
double SumOpenLots(const ENUM_POSITION_TYPE dir) {
   double total = 0.0;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket)) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)       continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)InpMagic) continue;
      if (PositionGetInteger(POSITION_TYPE)  != (long)dir)      continue;
      total += PositionGetDouble(POSITION_VOLUME);
   }
   return total;
}

//+------------------------------------------------------------------+
//| True if an open position has this comment on this symbol/magic   |
//+------------------------------------------------------------------+
bool PositionExistsByComment(const string comment) {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket))                         continue;
      if (PositionGetString(POSITION_SYMBOL)  != _Symbol)          continue;
      if (PositionGetInteger(POSITION_MAGIC)  != (long)InpMagic)   continue;
      if (PositionGetString(POSITION_COMMENT) == comment) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| True if a historical entry deal with this comment exists         |
//+------------------------------------------------------------------+
bool HistoryDealExistsByComment(const string comment) {
   datetime from = TimeCurrent() - (datetime)(InpHistoryLookbackDays * 86400);
   if (!HistorySelect(from, TimeCurrent() + 60)) return false;

   int total = HistoryDealsTotal();
   for (int i = total - 1; i >= 0; i--) {
      ulong deal = HistoryDealGetTicket(i);
      if (deal == 0) continue;
      if (HistoryDealGetString(deal, DEAL_SYMBOL)  != _Symbol)        continue;
      if (HistoryDealGetInteger(deal, DEAL_MAGIC)  != (long)InpMagic) continue;
      if (HistoryDealGetInteger(deal, DEAL_ENTRY)  != DEAL_ENTRY_IN)  continue;
      if (HistoryDealGetString(deal, DEAL_COMMENT) == comment) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| Max timestamp seen in prior CAE_T*_{ts} comments — for restart   |
//| dedup so a re-attached EA never re-fires a completed signal.     |
//+------------------------------------------------------------------+
ulong ScanMaxSeenTimestamp() {
   ulong maxTs = 0;

   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket))                      continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)        continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)InpMagic) continue;
      ulong ts = ParseTsFromComment(PositionGetString(POSITION_COMMENT));
      if (ts > maxTs) maxTs = ts;
   }

   datetime from = TimeCurrent() - (datetime)(InpHistoryLookbackDays * 86400);
   if (HistorySelect(from, TimeCurrent() + 60)) {
      int total = HistoryDealsTotal();
      for (int i = total - 1; i >= 0; i--) {
         ulong deal = HistoryDealGetTicket(i);
         if (deal == 0) continue;
         if (HistoryDealGetString(deal, DEAL_SYMBOL)  != _Symbol)        continue;
         if (HistoryDealGetInteger(deal, DEAL_MAGIC)  != (long)InpMagic) continue;
         ulong ts = ParseTsFromComment(HistoryDealGetString(deal, DEAL_COMMENT));
         if (ts > maxTs) maxTs = ts;
      }
   }
   return maxTs;
}

//+------------------------------------------------------------------+
//| Extract trailing ts from "CAE_T{n}_{ts}"; 0 if not our format    |
//+------------------------------------------------------------------+
ulong ParseTsFromComment(const string comment) {
   if (StringFind(comment, "CAE_T") != 0) return 0;
   int sep = StringFind(comment, "_", 5);   // skip past "CAE_T"
   if (sep < 0) return 0;
   return (ulong)StringToInteger(StringSubstr(comment, sep + 1));
}

//+------------------------------------------------------------------+
//| Master loader: read file → validate → populate struct            |
//+------------------------------------------------------------------+
bool LoadSignal(const string filename, CondeSignal &sig) {
   ZeroMemory(sig);
   sig.valid = false;

   string json = ReadFileToString(filename);
   if (json == "") return false;

   string ts_str   = JsonGetString(json, "timestamp");
   string sym_str  = JsonGetString(json, "symbol");
   string dir_str  = JsonGetString(json, "direction");
   string ep_str   = JsonGetString(json, "entry_price");
   string sl_str   = JsonGetString(json, "sl");

   if (ts_str  == "") { Print("[Validation] Missing: timestamp");    return false; }
   if (sym_str == "") { Print("[Validation] Missing: symbol");       return false; }
   if (dir_str == "") { Print("[Validation] Missing: direction");    return false; }
   if (ep_str  == "") { Print("[Validation] Missing: entry_price");  return false; }
   if (sl_str  == "") { Print("[Validation] Missing: sl");           return false; }

   double tps[];
   if (!JsonGetDoubleArray(json, "tps", tps) || ArraySize(tps) == 0) {
      Print("[Validation] Missing or empty: tps"); return false;
   }

   //--- Symbol must match chart
   if (sym_str != _Symbol) {
      PrintFormat("[Validation] Symbol mismatch: file=%s chart=%s", sym_str, _Symbol);
      return false;
   }

   //--- Direction
   StringToUpper(dir_str);
   if (dir_str != "BUY" && dir_str != "SELL") {
      PrintFormat("[Validation] Invalid direction: %s", dir_str);
      return false;
   }

   double entry = StringToDouble(ep_str);
   double sl    = StringToDouble(sl_str);
   if (entry <= 0) { Print("[Validation] entry_price <= 0");       return false; }
   if (sl    <= 0) { Print("[Validation] sl <= 0");                return false; }

   for (int i = 0; i < ArraySize(tps); i++) {
      if (tps[i] <= 0) {
         PrintFormat("[Validation] tps[%d] <= 0 (%.5f)", i, tps[i]);
         return false;
      }
   }

   //--- Directional sanity
   if (dir_str == "BUY") {
      if (sl >= entry) {
         PrintFormat("[Validation] BUY sl (%.5f) >= entry (%.5f)", sl, entry);
         return false;
      }
      for (int i = 0; i < ArraySize(tps); i++) {
         if (tps[i] <= entry) {
            PrintFormat("[Validation] BUY tps[%d] (%.5f) <= entry (%.5f)", i, tps[i], entry);
            return false;
         }
      }
   } else {
      if (sl <= entry) {
         PrintFormat("[Validation] SELL sl (%.5f) <= entry (%.5f)", sl, entry);
         return false;
      }
      for (int i = 0; i < ArraySize(tps); i++) {
         if (tps[i] >= entry) {
            PrintFormat("[Validation] SELL tps[%d] (%.5f) >= entry (%.5f)", i, tps[i], entry);
            return false;
         }
      }
   }

   //--- Timestamp freshness
   ulong ts  = (ulong)StringToInteger(ts_str);
   ulong now = (ulong)TimeGMT();
   if (ts > now) {
      PrintFormat("[Validation] Timestamp in future (ts=%s now=%s)",
                  IntegerToString(ts), IntegerToString(now));
      return false;
   }
   if (now - ts > 86400) {
      PrintFormat("[Validation] Signal expired — age=%s s (max 86400)",
                  IntegerToString(now - ts));
      return false;
   }

   sig.timestamp   = ts;
   sig.symbol      = sym_str;
   sig.direction   = dir_str;
   sig.entry_price = entry;
   sig.sl          = sl;
   ArrayCopy(sig.tps, tps);
   sig.valid       = true;
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
      if (h == INVALID_HANDLE) {
         PrintFormat("[File] Cannot open '%s' (err %d)", filename, GetLastError());
         return "";
      }
   }

   string result = "";
   while (!FileIsEnding(h))
      result += FileReadString(h);
   FileClose(h);
   return result;
}

//+------------------------------------------------------------------+
//| Locate `"key"` followed by `:` (skipping whitespace).            |
//| Returns index just past the colon, or -1. Skips matches that     |
//| happen to appear inside a string value (no colon after).         |
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
//| Extract a scalar string value for a given JSON key               |
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
//| Extract a JSON array into a double[]                             |
//+------------------------------------------------------------------+
bool JsonGetDoubleArray(const string json, const string key, double &arr[]) {
   ArrayResize(arr, 0);

   int pos = FindJsonKey(json, key);
   if (pos < 0) return false;

   int bracket = StringFind(json, "[", pos);
   if (bracket < 0) return false;

   int end = StringFind(json, "]", bracket + 1);
   if (end < 0) return false;

   string content = StringSubstr(json, bracket + 1, end - bracket - 1);

   string parts[];
   int    n = StringSplit(content, ',', parts);

   for (int i = 0; i < n; i++) {
      string s = parts[i];
      StringReplace(s, "\"", "");
      StringTrimLeft(s);
      StringTrimRight(s);
      if (s == "") continue;

      int idx = ArraySize(arr);
      ArrayResize(arr, idx + 1);
      arr[idx] = StringToDouble(s);
   }
   return ArraySize(arr) > 0;
}
//+------------------------------------------------------------------+
