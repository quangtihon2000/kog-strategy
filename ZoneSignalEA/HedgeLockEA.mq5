//+------------------------------------------------------------------+
//|  HedgeLockEA.mq5                                                 |
//|  Buy market + Sell limit above; close pair on floating > 0,      |
//|  then reopen a fresh pair. One pair at a time.                   |
//+------------------------------------------------------------------+
#property copyright   "HedgeLock EA"
#property version     "1.00"
#property description "Single buy+sell pair, sell always above buy, recycle on profit."

#include <Trade\Trade.mqh>

//--- Input Parameters
input double InpLots         = 0.01;       // Lot size (same for both legs)
input double InpSpreadMult   = 1.5;        // Distance = spread × mult + buffer
input int    InpBufferPts    = 10;         // Extra points above spread (safety cushion)
input double InpMinProfit    = 0.0;        // Min combined floating (account currency) to close
input ulong  InpMagic        = 70260421;   // Magic number
input int    InpSlippagePts  = 20;         // Deviation (points) for market orders

//--- Globals
CTrade   g_trade;
datetime g_lastCheck = 0;

//+------------------------------------------------------------------+
int OnInit() {
   if (AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) {
      Alert("[HedgeLockEA] Account must be HEDGING mode — aborting.");
      return INIT_FAILED;
   }
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints((ulong)InpSlippagePts);
   PrintFormat("[HedgeLockEA] Init  sym=%s  lots=%.2f  spreadMult=%.2f  buffer=%d pts  minProfit=%.2f",
               _Symbol, InpLots, InpSpreadMult, InpBufferPts, InpMinProfit);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   PrintFormat("[HedgeLockEA] Removed. Reason: %d", reason);
}

//+------------------------------------------------------------------+
void OnTick() {
   datetime now = TimeCurrent();
   if (now == g_lastCheck) return;   // throttle to 1 Hz
   g_lastCheck = now;

   int positions = CountMyPositions();
   int pendings  = CountMyPendings();

   // Both legs filled → check floating, cycle if profitable
   if (positions >= 2 && pendings == 0) {
      double floating = FloatingPL();
      if (floating > InpMinProfit) {
         PrintFormat("[HedgeLockEA] Floating=%.2f > %.2f — recycling pair",
                     floating, InpMinProfit);
         CloseAll();
         OpenPair();
      }
      return;
   }

   // Nothing open → open fresh pair
   if (positions == 0 && pendings == 0) {
      OpenPair();
      return;
   }

   // Buy filled but sell limit missing (cancelled / never placed) → restore it
   if (positions == 1 && pendings == 0) {
      RestoreSellLimit();
      return;
   }

   // positions==1 && pendings==1  → normal waiting state
   // positions==0 && pendings==1  → unusual (buy rejected?) — delete orphan and retry
   if (positions == 0 && pendings >= 1) {
      DeleteMyPendings();
   }
}

//+------------------------------------------------------------------+
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

//+------------------------------------------------------------------+
int CountMyPendings() {
   int c = 0;
   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong tk = OrderGetTicket(i);
      if (tk == 0) continue;
      if (OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if ((ulong)OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
      c++;
   }
   return c;
}

//+------------------------------------------------------------------+
double FloatingPL() {
   double total = 0.0;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong tk = PositionGetTicket(i);
      if (tk == 0) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if ((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      total += PositionGetDouble(POSITION_PROFIT)
             + PositionGetDouble(POSITION_SWAP);
   }
   return total;
}

//+------------------------------------------------------------------+
// Distance (points) = max(spread × mult + buffer, broker stop level + buffer)
int ComputeDistancePts() {
   long spreadPts = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   long stopLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   long fromSpread = (long)MathCeil(spreadPts * InpSpreadMult) + InpBufferPts;
   long fromStops  = stopLevel + InpBufferPts;
   return (int)MathMax(fromSpread, fromStops);
}

//+------------------------------------------------------------------+
void OpenPair() {
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if (ask <= 0) return;

   int distPts = ComputeDistancePts();

   if (!g_trade.Buy(InpLots, _Symbol, 0, 0, 0, "HedgeLock-BUY")) {
      PrintFormat("[HedgeLockEA] Buy failed: %u %s",
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
      return;
   }
   double buyFill = g_trade.ResultPrice();
   double sellPx  = NormalizeDouble(buyFill + distPts * _Point, _Digits);

   if (!g_trade.SellLimit(InpLots, sellPx, _Symbol, 0, 0,
                          ORDER_TIME_GTC, 0, "HedgeLock-SELL")) {
      PrintFormat("[HedgeLockEA] SellLimit failed: %u %s",
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
      return;
   }
   long curSpread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   PrintFormat("[HedgeLockEA] Opened pair  buy=%.5f  sellLimit=%.5f  dist=%d pts  spread=%d pts",
               buyFill, sellPx, distPts, (int)curSpread);
}

//+------------------------------------------------------------------+
void RestoreSellLimit() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong tk = PositionGetTicket(i);
      if (tk == 0) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if ((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if (PositionGetInteger(POSITION_TYPE) != POSITION_TYPE_BUY) continue;
      double buyPx  = PositionGetDouble(POSITION_PRICE_OPEN);
      int    distPts = ComputeDistancePts();
      double sellPx = NormalizeDouble(buyPx + distPts * _Point, _Digits);
      if (!g_trade.SellLimit(InpLots, sellPx, _Symbol, 0, 0,
                             ORDER_TIME_GTC, 0, "HedgeLock-SELL")) {
         PrintFormat("[HedgeLockEA] Restore SellLimit failed: %u",
                     g_trade.ResultRetcode());
      } else {
         PrintFormat("[HedgeLockEA] SellLimit restored at %.5f", sellPx);
      }
      return;
   }
}

//+------------------------------------------------------------------+
void CloseAll() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong tk = PositionGetTicket(i);
      if (tk == 0) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if ((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      if (!g_trade.PositionClose(tk)) {
         PrintFormat("[HedgeLockEA] Close %I64u failed: %u",
                     tk, g_trade.ResultRetcode());
      }
   }
   DeleteMyPendings();
}

//+------------------------------------------------------------------+
void DeleteMyPendings() {
   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong tk = OrderGetTicket(i);
      if (tk == 0) continue;
      if (OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if ((ulong)OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
      if (!g_trade.OrderDelete(tk)) {
         PrintFormat("[HedgeLockEA] Delete %I64u failed: %u",
                     tk, g_trade.ResultRetcode());
      }
   }
}
//+------------------------------------------------------------------+
