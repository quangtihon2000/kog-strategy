//+------------------------------------------------------------------+
//|  ZoneSignalEA.mq5                                                |
//|  Trades M15 breakouts from a JSON zone signal file               |
//+------------------------------------------------------------------+
#property copyright   "ZoneSignal EA"
#property version     "2.00"
#property description "Reads a JSON signal file and trades M15 zone breakouts"

#include <Trade\Trade.mqh>

//--- Input Parameters
input double   InpLotPerTarget  = 0.01;          // Lot size per position
input double   InpSlBufferPts   = 50;            // Extra SL buffer (points)
input ulong    InpMagic         = 20240416;      // Magic number
input bool     InpUseCommonDir  = true;          // Use MT5 common Files folder

//+------------------------------------------------------------------+
//| Signal data structure                                            |
//+------------------------------------------------------------------+
struct ZoneSignal {
   ulong    timestamp;
   string   symbol;
   double   redbox_upper;
   double   redbox_lower;
   double   targets_above[];
   double   targets_below[];
   bool     valid;
};

//--- Globals
CTrade     g_trade;
string     g_signalFile;          // derived from account number in OnInit
datetime   g_lastBarTime   = 0;
datetime   g_lastTickCheck = 0;   // throttle: check JSON at most once per second
ulong      g_lastSigTs     = 0;   // timestamp of last applied signal
ZoneSignal g_sig;

//+------------------------------------------------------------------+
int OnInit() {
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(30);
   ZeroMemory(g_sig);

   //--- Derive signal file name from account number
   g_signalFile = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ".json";

   if (!IsPeriod(PERIOD_M15))
      Print("[WARN] EA is attached to a non-M15 chart. Logic still uses M15 bars.");

   Print("[ZoneSignalEA] Initialized. Signal file: ", g_signalFile);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   Print("[ZoneSignalEA] Removed. Reason: ", reason);
}

//+------------------------------------------------------------------+
void OnTick() {
   //--- Check signal file on every new second (tick-level polling)
   datetime now = TimeCurrent();
   if (now != g_lastTickCheck) {
      g_lastTickCheck = now;
      CheckSignalFile();
   }

   //--- Trade entry logic: once per new M15 bar
   datetime barTime = iTime(_Symbol, PERIOD_M15, 0);
   if (barTime == g_lastBarTime) return;
   g_lastBarTime = barTime;
   ProcessNewBar();
}

//+------------------------------------------------------------------+
//| Detect SL / TP hits and mark signal done                         |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result) {
   if (trans.type != TRADE_TRANSACTION_DEAL_ADD) return;

   ulong deal = trans.deal;
   if (!HistoryDealSelect(deal)) return;

   //--- Must be our EA on this symbol
   if (HistoryDealGetInteger(deal, DEAL_MAGIC)  != (long)InpMagic) return;
   if (HistoryDealGetString (deal, DEAL_SYMBOL) != _Symbol)        return;

   //--- Only closing deals (SL or TP)
   ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY);
   if (entry != DEAL_ENTRY_OUT) return;

   //--- If no more open positions → signal fully completed
   if (!HasOpenPositions()) {
      Print("[Signal] All positions closed (SL/TP hit) → signal deactivated");
      g_sig.valid = false;
   }
}

//+------------------------------------------------------------------+
//| Poll JSON file and apply if a new timestamp is detected          |
//+------------------------------------------------------------------+
void CheckSignalFile() {
   ZoneSignal sig;
   if (!LoadSignal(g_signalFile, sig)) return;
   if (sig.timestamp == g_lastSigTs) return;   // same signal — skip
   ApplySignal(sig);                            // new timestamp → load it
}

//+------------------------------------------------------------------+
//| Store signal into globals and log it                             |
//+------------------------------------------------------------------+
void ApplySignal(const ZoneSignal &sig) {
   g_sig       = sig;
   g_lastSigTs = sig.timestamp;
   PrintFormat("[Signal] Applied — Zone %.5f – %.5f | Targets above: %d | below: %d",
               sig.redbox_lower, sig.redbox_upper,
               ArraySize(sig.targets_above), ArraySize(sig.targets_below));
}

//+------------------------------------------------------------------+
//| Called once per new M15 bar                                      |
//+------------------------------------------------------------------+
void ProcessNewBar() {
   if (!g_sig.valid) return;

   //--- Skip if we already have open positions from this EA
   if (HasOpenPositions()) return;

   double close1 = iClose(_Symbol, PERIOD_M15, 1);
   PrintFormat("[Bar] M15 close[1]=%.5f  Zone [%.5f – %.5f]",
               close1, g_sig.redbox_lower, g_sig.redbox_upper);

   if (close1 > g_sig.redbox_upper) {
      Print("[Signal] Close ABOVE zone → opening BUY positions");
      OpenTrades(POSITION_TYPE_BUY);
   } else if (close1 < g_sig.redbox_lower) {
      Print("[Signal] Close BELOW zone → opening SELL positions");
      OpenTrades(POSITION_TYPE_SELL);
   }
}

//+------------------------------------------------------------------+
//| Open one position per target                                     |
//+------------------------------------------------------------------+
void OpenTrades(const ENUM_POSITION_TYPE dir) {
   double buffer = InpSlBufferPts * _Point;

   if (dir == POSITION_TYPE_BUY) {
      double entry = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl    = NormalizeDouble(g_sig.redbox_lower - buffer, _Digits);
      int    n     = ArraySize(g_sig.targets_above);

      for (int i = 0; i < n; i++) {
         double tp = NormalizeDouble(g_sig.targets_above[i], _Digits);
         if (tp <= entry) {
            PrintFormat("[SKIP] BUY target #%d (%.5f) is below/at entry (%.5f)", i+1, tp, entry);
            continue;
         }
         string comment = StringFormat("ZB_T%d_%d", i+1, (int)g_sig.timestamp);
         bool   ok      = g_trade.Buy(InpLotPerTarget, _Symbol, entry, sl, tp, comment);
         PrintFormat("[BUY #%d] entry=%.5f  sl=%.5f  tp=%.5f  %s",
                     i+1, entry, sl, tp,
                     ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
      }

   } else { // SELL
      double entry = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl    = NormalizeDouble(g_sig.redbox_upper + buffer, _Digits);
      int    n     = ArraySize(g_sig.targets_below);

      for (int i = 0; i < n; i++) {
         double tp = NormalizeDouble(g_sig.targets_below[i], _Digits);
         if (tp >= entry) {
            PrintFormat("[SKIP] SELL target #%d (%.5f) is above/at entry (%.5f)", i+1, tp, entry);
            continue;
         }
         string comment = StringFormat("ZS_T%d_%d", i+1, (int)g_sig.timestamp);
         bool   ok      = g_trade.Sell(InpLotPerTarget, _Symbol, entry, sl, tp, comment);
         PrintFormat("[SELL #%d] entry=%.5f  sl=%.5f  tp=%.5f  %s",
                     i+1, entry, sl, tp,
                     ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
      }
   }
}

//+------------------------------------------------------------------+
//| Check if EA already has open positions on this symbol            |
//+------------------------------------------------------------------+
bool HasOpenPositions() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (PositionSelectByTicket(ticket)) {
         if (PositionGetString(POSITION_SYMBOL)  == _Symbol &&
             PositionGetInteger(POSITION_MAGIC)  == (long)InpMagic)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Master loader: read file → validate → populate struct            |
//+------------------------------------------------------------------+
bool LoadSignal(const string filename, ZoneSignal &sig) {
   ZeroMemory(sig);
   sig.valid = false;

   string json = ReadFileToString(filename);
   if (json == "") return false;

   //--- Required fields
   string ts_str  = JsonGetString(json, "timestamp");
   string rbu_str = JsonGetString(json, "redbox_upper");
   string rbl_str = JsonGetString(json, "redbox_lower");

   if (ts_str  == "") { Print("[Validation] Missing: timestamp");    return false; }
   if (rbu_str == "") { Print("[Validation] Missing: redbox_upper"); return false; }
   if (rbl_str == "") { Print("[Validation] Missing: redbox_lower"); return false; }

   //--- Parse targets
   double ta[], tb[];
   if (!JsonGetDoubleArray(json, "targets_above", ta) || ArraySize(ta) == 0) {
      Print("[Validation] Missing or empty: targets_above"); return false;
   }
   if (!JsonGetDoubleArray(json, "targets_below", tb) || ArraySize(tb) == 0) {
      Print("[Validation] Missing or empty: targets_below"); return false;
   }

   //--- Timestamp: expiry check (only for "fresh" signals we haven't seen before)
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

   //--- Populate
   sig.timestamp    = ts;
   sig.symbol       = JsonGetString(json, "symbol");
   sig.redbox_upper = StringToDouble(rbu_str);
   sig.redbox_lower = StringToDouble(rbl_str);
   ArrayCopy(sig.targets_above, ta);
   ArrayCopy(sig.targets_below, tb);
   sig.valid        = true;
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
      //--- Fallback: try the other directory
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
//|  Handles both:  "key": "value"  and  "key": 1234                |
//+------------------------------------------------------------------+
string JsonGetString(const string json, const string key) {
   string needle = "\"" + key + "\"";
   int    pos    = StringFind(json, needle);
   if (pos < 0) return "";

   pos += StringLen(needle);
   int len = StringLen(json);

   //--- Skip whitespace and the colon
   while (pos < len) {
      ushort c = StringGetCharacter(json, pos);
      if (c != ' ' && c != '\t' && c != ':') break;
      pos++;
   }
   if (pos >= len) return "";

   ushort first = StringGetCharacter(json, pos);

   if (first == '"') {
      //--- Quoted string — read until closing quote
      pos++;
      string val = "";
      while (pos < len) {
         ushort c = StringGetCharacter(json, pos++);
         if (c == '"') break;
         val += ShortToString(c);
      }
      return val;
   } else if (first == '[' || first == '{') {
      return ""; // not a scalar
   } else {
      //--- Unquoted value (number / bool / null)
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
//|  Accepts both:  ["4843","4860"]  and  [4843, 4860]              |
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
//| Helper: is chart period M15?                                     |
//+------------------------------------------------------------------+
bool IsPeriod(ENUM_TIMEFRAMES tf) {
   return Period() == tf;
}
//+------------------------------------------------------------------+
