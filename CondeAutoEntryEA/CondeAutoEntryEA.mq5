//+------------------------------------------------------------------+
//|  CondeAutoEntryEA.mq5                                            |
//|  Opens one position per TP from a pre-computed JSON signal       |
//+------------------------------------------------------------------+
#property copyright   "CondeAutoEntry EA"
#property version     "1.00"
#property description "Reads {account}_{symbol}.json, market-fires at entry, one position per TP"

#include <Trade\Trade.mqh>

//--- Input Parameters
input double InpLotPerTarget        = 0.01;        // Lot size per TP position
input double InpMaxLotsPerPosition  = 0.10;        // Max lot size per individual position
input double InpMaxTotalLotsPerDir  = 1.00;        // Max total lots across all open positions in one direction
input int    InpMaxPositions        = 10;          // Max total open EA positions on this symbol
input double InpMaxSlippagePts      = 100;         // Max distance (points) between market and entry_price to fire
input double InpSlBufferPts         = 0;           // Extra SL buffer (points)
input ulong  InpMagic               = 20260421;    // Magic number
input bool   InpUseCommonDir        = true;        // Use MT5 common Files folder

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
   g_trade.SetDeviationInPoints(30);
   ZeroMemory(g_sig);

   g_signalFile = "CondeAutoEntryEA\\"
                + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))
                + "_" + _Symbol + ".json";

   Print("[CondeAutoEntryEA] Initialized. Signal file: ", g_signalFile);
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
   OpenTrades(sig);
   g_lastSigTs = sig.timestamp;
}

//+------------------------------------------------------------------+
//| Open one position per TP, respecting position and lot caps       |
//+------------------------------------------------------------------+
void OpenTrades(const CondeSignal &sig) {
   ENUM_POSITION_TYPE dir = (sig.direction == "BUY") ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   int nTps = ArraySize(sig.tps);

   PrintFormat("[Signal] Applied — %s entry=%.5f sl=%.5f tps=%d ts=%d",
               sig.direction, sig.entry_price, sig.sl, nTps, (int)sig.timestamp);

   for (int i = 0; i < nTps; i++) {
      //--- Cap: max total positions on this symbol
      int totalOpen = CountOpenPositions(POSITION_TYPE_BUY) + CountOpenPositions(POSITION_TYPE_SELL);
      if (totalOpen >= InpMaxPositions) {
         PrintFormat("[SKIP] TP #%d — max positions (%d) reached", i + 1, InpMaxPositions);
         break;
      }

      //--- Cap: per-position lot size
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

      double entry = (dir == POSITION_TYPE_BUY)
                     ? SymbolInfoDouble(_Symbol, SYMBOL_ASK)
                     : SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double buffer = InpSlBufferPts * _Point;
      double sl = (dir == POSITION_TYPE_BUY)
                  ? NormalizeDouble(sig.sl - buffer, _Digits)
                  : NormalizeDouble(sig.sl + buffer, _Digits);
      double tp = NormalizeDouble(sig.tps[i], _Digits);

      string comment = StringFormat("CAE_T%d_%d", i + 1, (int)sig.timestamp);
      bool ok = (dir == POSITION_TYPE_BUY)
                ? g_trade.Buy (lot, _Symbol, entry, sl, tp, comment)
                : g_trade.Sell(lot, _Symbol, entry, sl, tp, comment);

      PrintFormat("[%s #%d] lots=%.2f entry=%.5f sl=%.5f tp=%.5f  %s",
                  sig.direction, i + 1, lot, entry, sl, tp,
                  ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
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

   //--- Timestamp freshness (only for unseen signals)
   ulong ts  = (ulong)StringToInteger(ts_str);
   ulong now = (ulong)TimeGMT();
   if (ts != g_lastSigTs) {
      if (ts > now) {
         PrintFormat("[Validation] Timestamp in future (ts=%d now=%d)", ts, now);
         return false;
      }
      if (now - ts > 86400) {
         PrintFormat("[Validation] Signal expired — age=%d s (max 86400)", now - ts);
         return false;
      }
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
//| Extract a scalar string value for a given JSON key               |
//+------------------------------------------------------------------+
string JsonGetString(const string json, const string key) {
   string needle = "\"" + key + "\"";
   int    pos    = StringFind(json, needle);
   if (pos < 0) return "";

   pos += StringLen(needle);
   int len = StringLen(json);

   while (pos < len) {
      ushort c = StringGetCharacter(json, pos);
      if (c != ' ' && c != '\t' && c != ':') break;
      pos++;
   }
   if (pos >= len) return "";

   ushort first = StringGetCharacter(json, pos);

   if (first == '"') {
      pos++;
      string val = "";
      while (pos < len) {
         ushort c = StringGetCharacter(json, pos++);
         if (c == '"') break;
         val += ShortToString(c);
      }
      return val;
   } else if (first == '[' || first == '{') {
      return "";
   } else {
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
}

//+------------------------------------------------------------------+
//| Extract a JSON array into a double[]                             |
//+------------------------------------------------------------------+
bool JsonGetDoubleArray(const string json, const string key, double &arr[]) {
   ArrayResize(arr, 0);

   string needle = "\"" + key + "\"";
   int    pos    = StringFind(json, needle);
   if (pos < 0) return false;

   int bracket = StringFind(json, "[", pos + StringLen(needle));
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
