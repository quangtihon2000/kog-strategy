//+------------------------------------------------------------------+
//|  ZoneSignalEA.mq5                                                |
//|  Trades M15 breakouts from a JSON zone signal file               |
//+------------------------------------------------------------------+
#property copyright   "ZoneSignal EA"
#property version     "3.00"
#property description "Three-tier entry: Scalp (breakout), Normal (retrace), Mid (zone)"

#include <Trade\Trade.mqh>

//--- Input Parameters
input double   InpLotPerTarget  = 0.01;          // Lot size per position
input double   InpMaxLots       = 0.10;          // Max lot size per position
input int      InpMaxPositions  = 10;            // Max open positions (all targets)
input double   InpMinTpPts      = 300;           // Min TP distance (points) to open
input double   InpSlBufferPts   = 50;            // Extra SL buffer (points)
input ulong    InpMagic         = 20240416;      // Magic number
input bool     InpUseCommonDir  = true;          // Use MT5 common Files folder
input double   InpScalpTpPts    = 400;           // Scalp TP distance (points)
input double   InpScalpBufPts   = 500;           // Scalp zone ceiling buffer from T1 (points)
input double   InpRetracePts    = 200;           // Normal entry: max retrace distance from redbox (points)
input bool     InpEnableMidEntry = true;         // Enable mid-zone entry (optional)
input double   InpBeProfitPts   = 70;            // Profit locked when moving to BE (points)

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
bool       g_breakoutBuy   = false;  // true once a BUY breakout has been taken
bool       g_breakoutSell  = false;  // true once a SELL breakout has been taken
bool       g_midEntryBuyDone  = false;  // true once a mid-zone BUY reentry has been taken
bool       g_midEntrySellDone = false;  // true once a mid-zone SELL reentry has been taken
bool       g_buyDone       = false;  // true once any BUY position hits SL → no more BUY entries
bool       g_sellDone      = false;  // true once any SELL position hits SL → no more SELL entries
bool       g_scalpBuyActive   = false;  // true while a scalp BUY position is open
bool       g_scalpSellActive  = false;  // true while a scalp SELL position is open
bool       g_normalBuyDone    = false;  // true once normal BUY entries have been placed
bool       g_normalSellDone   = false;  // true once normal SELL entries have been placed

//--- Ticket tracking: all positions opened by the current signal
ulong      g_signalTickets[];         // ticket numbers from this signal
ulong      g_t1BuyTicket   = 0;       // ticket of BUY target 1 (for BE trigger)
ulong      g_t1SellTicket  = 0;       // ticket of SELL target 1 (for BE trigger)
ulong      g_scalpBuyTicket   = 0;    // ticket of current scalp BUY position
ulong      g_scalpSellTicket  = 0;    // ticket of current scalp SELL position

//+------------------------------------------------------------------+
int OnInit() {
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(30);
   ZeroMemory(g_sig);

   //--- Derive signal file name from account number (Files/ZoneSignalEA/{account}.json)
   g_signalFile = "ZoneSignalEA\\" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ".json";

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

   //--- Entries: check on every tick after breakout confirmed
   if (g_sig.valid) {
      // 1) Scalp entries
      if (g_breakoutBuy && !g_scalpBuyActive && !g_buyDone)
         OpenScalpEntry(POSITION_TYPE_BUY);
      if (g_breakoutSell && !g_scalpSellActive && !g_sellDone)
         OpenScalpEntry(POSITION_TYPE_SELL);

      // 2) Normal entries — triggers immediately when price hits retrace zone
      if (g_breakoutBuy && !g_normalBuyDone && !g_buyDone) {
         double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         double retraceLim = g_sig.redbox_upper + InpRetracePts * _Point;
         if (ask >= g_sig.redbox_upper && ask <= retraceLim) {
            PrintFormat("[Normal] BUY retrace near redbox (Ask %.5f) → opening target positions", ask);
            OpenTrades(POSITION_TYPE_BUY);
            g_normalBuyDone = true;
         }
      }
      if (g_breakoutSell && !g_normalSellDone && !g_sellDone) {
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         double retraceLim = g_sig.redbox_lower - InpRetracePts * _Point;
         if (bid <= g_sig.redbox_lower && bid >= retraceLim) {
            PrintFormat("[Normal] SELL retrace near redbox (Bid %.5f) → opening target positions", bid);
            OpenTrades(POSITION_TYPE_SELL);
            g_normalSellDone = true;
         }
      }
   }

   //--- Other entries: once per new M15 bar
   datetime barTime = iTime(_Symbol, PERIOD_M15, 0);
   if (barTime == g_lastBarTime) return;
   g_lastBarTime = barTime;
   ProcessNewBar();
}

//+------------------------------------------------------------------+
//| Detect SL / TP hits, break-even logic, and mark signal done      |
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

   ENUM_DEAL_REASON reason = (ENUM_DEAL_REASON)HistoryDealGetInteger(deal, DEAL_REASON);
   ulong closedPosId = trans.position;  // position ticket that was closed

   //--- Determine the original position direction from the closing deal type
   //    Closing a BUY = DEAL_TYPE_SELL, closing a SELL = DEAL_TYPE_BUY
   ENUM_DEAL_TYPE dealType = (ENUM_DEAL_TYPE)HistoryDealGetInteger(deal, DEAL_TYPE);
   bool wasBuy  = (dealType == DEAL_TYPE_SELL);  // closing deal sells → was a BUY
   bool wasSell = (dealType == DEAL_TYPE_BUY);   // closing deal buys  → was a SELL

   //--- Handle scalp ticket closures (TP → allow re-entry)
   if (closedPosId == g_scalpBuyTicket && g_scalpBuyTicket != 0) {
      g_scalpBuyActive = false;
      g_scalpBuyTicket = 0;
      if (reason == DEAL_REASON_TP)
         Print("[Scalp] BUY scalp TP hit → re-entry allowed");
   }
   if (closedPosId == g_scalpSellTicket && g_scalpSellTicket != 0) {
      g_scalpSellActive = false;
      g_scalpSellTicket = 0;
      if (reason == DEAL_REASON_TP)
         Print("[Scalp] SELL scalp TP hit → re-entry allowed");
   }

   //--- Mark direction as DONE only on SL hit (TP allows continued trading)
   if (reason == DEAL_REASON_SL) {
      if (wasBuy && !g_buyDone) {
         g_buyDone = true;
         PrintFormat("[Signal] BUY direction DONE (ticket #%d hit SL) → no more BUY entries", closedPosId);
      }
      if (wasSell && !g_sellDone) {
         g_sellDone = true;
         PrintFormat("[Signal] SELL direction DONE (ticket #%d hit SL) → no more SELL entries", closedPosId);
      }
   }

   //--- Check if T1 hit TP → move remaining positions to break even
   if (reason == DEAL_REASON_TP) {
      if (closedPosId == g_t1BuyTicket && g_t1BuyTicket != 0) {
         PrintFormat("[BE] BUY T1 (ticket #%d) hit TP → moving remaining positions to break even", closedPosId);
         MoveSignalToBreakEven(POSITION_TYPE_BUY);
         g_t1BuyTicket = 0;  // consumed
      }
      if (closedPosId == g_t1SellTicket && g_t1SellTicket != 0) {
         PrintFormat("[BE] SELL T1 (ticket #%d) hit TP → moving remaining positions to break even", closedPosId);
         MoveSignalToBreakEven(POSITION_TYPE_SELL);
         g_t1SellTicket = 0;  // consumed
      }
   }

   //--- Remove closed ticket from tracking array
   RemoveTicket(closedPosId);

   //--- Deactivate signal when both directions are done (T1 reached or SL hit)
   if (g_buyDone && g_sellDone) {
      Print("[Signal] Both directions done → signal deactivated");
      g_sig.valid = false;
   }
}

//+------------------------------------------------------------------+
//| Move SL to entry (break even) for all tracked positions          |
//|  matching the given direction                                     |
//+------------------------------------------------------------------+
void MoveSignalToBreakEven(const ENUM_POSITION_TYPE dir) {
   for (int i = ArraySize(g_signalTickets) - 1; i >= 0; i--) {
      ulong ticket = g_signalTickets[i];
      if (!PositionSelectByTicket(ticket)) continue;

      //--- Filter by direction
      if ((ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE) != dir) continue;

      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentSL = PositionGetDouble(POSITION_SL);
      double tp        = PositionGetDouble(POSITION_TP);
      double offset    = InpBeProfitPts * _Point;
      double newSL     = (dir == POSITION_TYPE_BUY)
                         ? NormalizeDouble(openPrice + offset, _Digits)
                         : NormalizeDouble(openPrice - offset, _Digits);

      //--- Skip if SL is already at or better than break-even target
      if (dir == POSITION_TYPE_BUY  && currentSL >= newSL) continue;
      if (dir == POSITION_TYPE_SELL && currentSL <= newSL && currentSL > 0) continue;

      //--- Skip if TP is too close (would invert SL/TP)
      if (dir == POSITION_TYPE_BUY  && tp > 0 && newSL >= tp) continue;
      if (dir == POSITION_TYPE_SELL && tp > 0 && newSL <= tp) continue;

      bool ok = g_trade.PositionModify(ticket, newSL, tp);
      PrintFormat("[BE] Ticket #%d SL: %.5f → %.5f (entry %.5f +%.0f pts)  %s",
                  ticket, currentSL, newSL, openPrice, InpBeProfitPts,
                  ok ? "OK" : "FAILED: " + g_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Track a newly opened ticket                                      |
//+------------------------------------------------------------------+
void AddTicket(ulong ticket) {
   int n = ArraySize(g_signalTickets);
   ArrayResize(g_signalTickets, n + 1);
   g_signalTickets[n] = ticket;
}

//+------------------------------------------------------------------+
//| Remove a closed ticket from tracking array                       |
//+------------------------------------------------------------------+
void RemoveTicket(ulong ticket) {
   int n = ArraySize(g_signalTickets);
   for (int i = 0; i < n; i++) {
      if (g_signalTickets[i] == ticket) {
         // shift remaining elements left
         for (int j = i; j < n - 1; j++)
            g_signalTickets[j] = g_signalTickets[j + 1];
         ArrayResize(g_signalTickets, n - 1);
         return;
      }
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
   g_breakoutBuy      = false;
   g_breakoutSell     = false;
   g_midEntryBuyDone  = false;
   g_midEntrySellDone = false;
   g_buyDone          = false;
   g_sellDone         = false;
   g_scalpBuyActive   = false;
   g_scalpSellActive  = false;
   g_normalBuyDone    = false;
   g_normalSellDone   = false;
   g_t1BuyTicket      = 0;
   g_t1SellTicket     = 0;
   g_scalpBuyTicket   = 0;
   g_scalpSellTicket  = 0;
   ArrayResize(g_signalTickets, 0);   // clear ticket tracking
   PrintFormat("[Signal] Applied — Zone %.5f – %.5f | Targets above: %d | below: %d",
               sig.redbox_lower, sig.redbox_upper,
               ArraySize(sig.targets_above), ArraySize(sig.targets_below));
}

//+------------------------------------------------------------------+
//| Called once per new M15 bar                                      |
//+------------------------------------------------------------------+
void ProcessNewBar() {
   if (!g_sig.valid) return;

   double close1 = iClose(_Symbol, PERIOD_M15, 1);
   double midZone = NormalizeDouble((g_sig.redbox_upper + g_sig.redbox_lower) / 2.0, _Digits);
   PrintFormat("[Bar] M15 close[1]=%.5f  Zone [%.5f – %.5f]  Mid=%.5f",
               close1, g_sig.redbox_lower, g_sig.redbox_upper, midZone);

   //--- 1) Breakout detection — flag only, no immediate trade opening
   if (close1 > g_sig.redbox_upper && !g_breakoutBuy && !g_buyDone) {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      int nAbove = ArraySize(g_sig.targets_above);
      if (nAbove > 0 && ask >= g_sig.targets_above[0]) {
         PrintFormat("[Signal] BUY IGNORED — price %.5f already at/past T1 (%.5f)", ask, g_sig.targets_above[0]);
         g_buyDone = true;
      } else {
         Print("[Signal] Close ABOVE zone → BUY breakout confirmed");
         g_breakoutBuy = true;
      }
   }
   if (close1 < g_sig.redbox_lower && !g_breakoutSell && !g_sellDone) {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      int nBelow = ArraySize(g_sig.targets_below);
      if (nBelow > 0 && bid <= g_sig.targets_below[0]) {
         PrintFormat("[Signal] SELL IGNORED — price %.5f already at/past T1 (%.5f)", bid, g_sig.targets_below[0]);
         g_sellDone = true;
      } else {
         Print("[Signal] Close BELOW zone → SELL breakout confirmed");
         g_breakoutSell = true;
      }
   }

   //--- 2) Mid-zone reentry (M15 close based, optional blocked if direction is done)
   if (InpEnableMidEntry && close1 >= g_sig.redbox_lower && close1 <= g_sig.redbox_upper) {
      if (g_breakoutBuy && !g_midEntryBuyDone && !g_buyDone) {
         Print("[MidZone] Price back in zone → opening extra BUY at mid-zone");
         OpenMidZoneEntry(POSITION_TYPE_BUY);
         g_midEntryBuyDone = true;
      }
      if (g_breakoutSell && !g_midEntrySellDone && !g_sellDone) {
         Print("[MidZone] Price back in zone → opening extra SELL at mid-zone");
         OpenMidZoneEntry(POSITION_TYPE_SELL);
         g_midEntrySellDone = true;
      }
   }
}

//+------------------------------------------------------------------+
//| Open 1 scalp position — can re-enter after TP hit                |
//+------------------------------------------------------------------+
void OpenScalpEntry(const ENUM_POSITION_TYPE dir) {
   double buffer   = InpSlBufferPts * _Point;
   double lotSize  = MathMin(InpLotPerTarget, InpMaxLots);
   double midZone  = NormalizeDouble((g_sig.redbox_upper + g_sig.redbox_lower) / 2.0, _Digits);

   //--- Check max positions cap
   if (CountOpenPositions(dir) >= InpMaxPositions) {
      PrintFormat("[Scalp SKIP] Max positions (%d) reached for %s",
                  InpMaxPositions, dir == POSITION_TYPE_BUY ? "BUY" : "SELL");
      return;
   }

   if (dir == POSITION_TYPE_BUY) {
      int n = ArraySize(g_sig.targets_above);
      if (n == 0) return;

      double entry        = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double scalpCeiling = g_sig.targets_above[0] - InpScalpBufPts * _Point;

      //--- Entry must be in scalp zone: [midZone, scalpCeiling]
      if (entry < midZone || entry > scalpCeiling) {
         PrintFormat("[Scalp SKIP] BUY entry %.5f outside scalp zone [%.5f – %.5f]",
                     entry, midZone, scalpCeiling);
         return;
      }

      double sl    = NormalizeDouble(g_sig.redbox_lower - buffer, _Digits);
      double rawTp = entry + InpScalpTpPts * _Point;
      double tp    = NormalizeDouble(MathMin(rawTp, g_sig.targets_above[0]), _Digits);

      if (tp <= entry) {
         PrintFormat("[Scalp SKIP] BUY TP %.5f <= entry %.5f", tp, entry);
         return;
      }

      string comment = StringFormat("ZB_SCALP_%d", (int)g_sig.timestamp);
      bool   ok      = g_trade.Buy(lotSize, _Symbol, entry, sl, tp, comment);
      PrintFormat("[Scalp BUY] lots=%.2f  entry=%.5f  sl=%.5f  tp=%.5f  %s",
                  lotSize, entry, sl, tp,
                  ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
      if (ok) {
         ulong ticket = g_trade.ResultOrder();
         AddTicket(ticket);
         g_scalpBuyActive = true;
         g_scalpBuyTicket = ticket;
      }

   } else { // SELL
      int n = ArraySize(g_sig.targets_below);
      if (n == 0) return;

      double entry      = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double scalpFloor = g_sig.targets_below[0] + InpScalpBufPts * _Point;

      //--- Entry must be in scalp zone: [scalpFloor, midZone]
      if (entry > midZone || entry < scalpFloor) {
         PrintFormat("[Scalp SKIP] SELL entry %.5f outside scalp zone [%.5f – %.5f]",
                     entry, scalpFloor, midZone);
         return;
      }

      double sl    = NormalizeDouble(g_sig.redbox_upper + buffer, _Digits);
      double rawTp = entry - InpScalpTpPts * _Point;
      double tp    = NormalizeDouble(MathMax(rawTp, g_sig.targets_below[0]), _Digits);

      if (tp >= entry) {
         PrintFormat("[Scalp SKIP] SELL TP %.5f >= entry %.5f", tp, entry);
         return;
      }

      string comment = StringFormat("ZS_SCALP_%d", (int)g_sig.timestamp);
      bool   ok      = g_trade.Sell(lotSize, _Symbol, entry, sl, tp, comment);
      PrintFormat("[Scalp SELL] lots=%.2f  entry=%.5f  sl=%.5f  tp=%.5f  %s",
                  lotSize, entry, sl, tp,
                  ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
      if (ok) {
         ulong ticket = g_trade.ResultOrder();
         AddTicket(ticket);
         g_scalpSellActive = true;
         g_scalpSellTicket = ticket;
      }
   }
}

//+------------------------------------------------------------------+
//| Open 1 extra position at mid-zone with last target's TP/SL       |
//+------------------------------------------------------------------+
void OpenMidZoneEntry(const ENUM_POSITION_TYPE dir) {
   double buffer  = InpSlBufferPts * _Point;
   double lotSize = MathMin(InpLotPerTarget, InpMaxLots);

   //--- Check max positions cap
   if (CountOpenPositions(dir) >= InpMaxPositions) {
      PrintFormat("[MidZone SKIP] Max positions (%d) reached for %s",
                  InpMaxPositions, dir == POSITION_TYPE_BUY ? "BUY" : "SELL");
      return;
   }

   if (dir == POSITION_TYPE_BUY) {
      int n = ArraySize(g_sig.targets_above);
      if (n == 0) return;
      double entry = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl    = NormalizeDouble(g_sig.redbox_lower - buffer, _Digits);
      double tp    = NormalizeDouble(g_sig.targets_above[n - 1], _Digits); // last target
      double tpDist = (tp - entry) / _Point;
      if (tp <= entry || tpDist < InpMinTpPts) {
         PrintFormat("[MidZone SKIP] BUY TP distance %.0f pts < min %.0f pts", tpDist, InpMinTpPts);
         return;
      }
      string comment = StringFormat("ZB_MID_%d", (int)g_sig.timestamp);
      bool   ok   = g_trade.Buy(lotSize, _Symbol, entry, sl, tp, comment);
      PrintFormat("[MidZone BUY] lots=%.2f  entry=%.5f  sl=%.5f  tp=%.5f (last target)  %s",
                  lotSize, entry, sl, tp,
                  ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
      if (ok) AddTicket(g_trade.ResultOrder());
   } else {
      int n = ArraySize(g_sig.targets_below);
      if (n == 0) return;
      double entry = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl    = NormalizeDouble(g_sig.redbox_upper + buffer, _Digits);
      double tp    = NormalizeDouble(g_sig.targets_below[n - 1], _Digits); // last target
      double tpDist = (entry - tp) / _Point;
      if (tp >= entry || tpDist < InpMinTpPts) {
         PrintFormat("[MidZone SKIP] SELL TP distance %.0f pts < min %.0f pts", tpDist, InpMinTpPts);
         return;
      }
      string comment = StringFormat("ZS_MID_%d", (int)g_sig.timestamp);
      bool   ok   = g_trade.Sell(lotSize, _Symbol, entry, sl, tp, comment);
      PrintFormat("[MidZone SELL] lots=%.2f  entry=%.5f  sl=%.5f  tp=%.5f (last target)  %s",
                  lotSize, entry, sl, tp,
                  ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
      if (ok) AddTicket(g_trade.ResultOrder());
   }
}

//+------------------------------------------------------------------+
//| Open one position per target                                     |
//+------------------------------------------------------------------+
void OpenTrades(const ENUM_POSITION_TYPE dir) {
   double buffer  = InpSlBufferPts * _Point;
   double lotSize = MathMin(InpLotPerTarget, InpMaxLots); // respect max lots cap

   if (dir == POSITION_TYPE_BUY) {
      double entry   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl      = NormalizeDouble(g_sig.redbox_lower - buffer, _Digits);
      int    n       = ArraySize(g_sig.targets_above);
      int    opened  = CountOpenPositions(POSITION_TYPE_BUY); // BUY positions only

      for (int i = 0; i < n; i++) {
         if (opened >= InpMaxPositions) {
            PrintFormat("[SKIP] BUY target #%d — max positions (%d) reached", i+1, InpMaxPositions);
            break;
         }
         double tp = NormalizeDouble(g_sig.targets_above[i], _Digits);
         if (tp <= entry) {
            PrintFormat("[SKIP] BUY target #%d (%.5f) is below/at entry (%.5f)", i+1, tp, entry);
            continue;
         }
         double tpDist = (tp - entry) / _Point;
         if (tpDist < InpMinTpPts) {
            PrintFormat("[SKIP] BUY target #%d — TP distance %.0f pts < min %.0f pts", i+1, tpDist, InpMinTpPts);
            continue;
         }
         string comment = StringFormat("ZB_T%d_%d", i+1, (int)g_sig.timestamp);
         bool   ok      = g_trade.Buy(lotSize, _Symbol, entry, sl, tp, comment);
         PrintFormat("[BUY #%d] lots=%.2f  entry=%.5f  sl=%.5f  tp=%.5f  %s",
                     i+1, lotSize, entry, sl, tp,
                     ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
         if (ok) {
            ulong ticket = g_trade.ResultOrder();
            AddTicket(ticket);
            if (i == 0) g_t1BuyTicket = ticket;  // first target = T1
            opened++;
         }
      }

   } else { // SELL
      double entry   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double sl      = NormalizeDouble(g_sig.redbox_upper + buffer, _Digits);
      int    n       = ArraySize(g_sig.targets_below);
      int    opened  = CountOpenPositions(POSITION_TYPE_SELL); // SELL positions only

      for (int i = 0; i < n; i++) {
         if (opened >= InpMaxPositions) {
            PrintFormat("[SKIP] SELL target #%d — max positions (%d) reached", i+1, InpMaxPositions);
            break;
         }
         double tp = NormalizeDouble(g_sig.targets_below[i], _Digits);
         if (tp >= entry) {
            PrintFormat("[SKIP] SELL target #%d (%.5f) is above/at entry (%.5f)", i+1, tp, entry);
            continue;
         }
         double tpDist = (entry - tp) / _Point;
         if (tpDist < InpMinTpPts) {
            PrintFormat("[SKIP] SELL target #%d — TP distance %.0f pts < min %.0f pts", i+1, tpDist, InpMinTpPts);
            continue;
         }
         string comment = StringFormat("ZS_T%d_%d", i+1, (int)g_sig.timestamp);
         bool   ok      = g_trade.Sell(lotSize, _Symbol, entry, sl, tp, comment);
         PrintFormat("[SELL #%d] lots=%.2f  entry=%.5f  sl=%.5f  tp=%.5f  %s",
                     i+1, lotSize, entry, sl, tp,
                     ok ? "Opened" : "FAILED: " + g_trade.ResultRetcodeDescription());
         if (ok) {
            ulong ticket = g_trade.ResultOrder();
            AddTicket(ticket);
            if (i == 0) g_t1SellTicket = ticket;  // first target = T1
            opened++;
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Check if EA already has open positions on this symbol            |
//+------------------------------------------------------------------+
//--- Returns true if at least one EA position (any direction) is open on this symbol
bool HasOpenPositions() {
   return (CountOpenPositions(POSITION_TYPE_BUY) + CountOpenPositions(POSITION_TYPE_SELL)) > 0;
}

//--- Returns the number of EA positions open on this symbol for a given direction
int CountOpenPositions(const ENUM_POSITION_TYPE dir) {
   int count = 0;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (PositionSelectByTicket(ticket)) {
         if (PositionGetString(POSITION_SYMBOL)   == _Symbol            &&
             PositionGetInteger(POSITION_MAGIC)   == (long)InpMagic     &&
             PositionGetInteger(POSITION_TYPE)    == (long)dir)
            count++;
      }
   }
   return count;
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
