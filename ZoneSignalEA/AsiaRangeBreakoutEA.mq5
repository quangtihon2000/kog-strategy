//+------------------------------------------------------------------+
//|  AsiaRangeBreakoutEA.mq5                                         |
//|  Scalp XAUUSD phiên Âu-Mỹ: phá range Á = BuyStop/SellStop        |
//|  SL = giữa range, TP = range × InpTPratio. 1 trade/ngày.         |
//+------------------------------------------------------------------+
#property copyright "Asia Range Breakout EA"
#property version   "1.00"
#property description "Breakout of Asia session range at London open. XAUUSD scalp."

#include <Trade\Trade.mqh>

//--- Time inputs (BROKER hours — default assumes GMT+3 broker)
input int    InpSessionStartHour = 10;    // Broker hour to place pendings (~London open)
input int    InpSessionEndHour   = 16;    // Broker hour to cancel unfilled pendings
input int    InpRangeHours       = 14;    // H1 bars back from session start = Asia range

//--- Range filter
input double InpMinRangeUSD      = 3.0;   // Skip day if range < this ($)
input double InpMaxRangeUSD      = 15.0;  // Skip day if range > this ($)
input int    InpBufferPts        = 50;    // Entry buffer above/below range (points)

//--- Risk & targets
input double InpRiskPercent      = 0.5;   // % balance risked per trade
input double InpTPratio          = 0.75;  // TP distance = range × this
input double InpFallbackLot      = 0.01;  // Used if risk math fails

//--- Direction toggle (gold has long bias — short side unprofitable in prior tests)
input bool   InpEnableLong       = true;  // Place BuyStop above range high
input bool   InpEnableShort      = false; // Place SellStop below range low

//--- Identity
input ulong  InpMagic            = 20260421;
input int    InpSlippagePts      = 20;

//--- Globals
CTrade   g_trade;
int      g_lastTradeDateKey = 0;   // yyyymmdd of last placement
datetime g_lastCheck = 0;

//+------------------------------------------------------------------+
int OnInit() {
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints((ulong)InpSlippagePts);
   PrintFormat("[AsiaRangeEA] Init  sym=%s  start=%02d:00  end=%02d:00  rangeH=%d  risk=%.2f%%  TPratio=%.2f",
               _Symbol, InpSessionStartHour, InpSessionEndHour,
               InpRangeHours, InpRiskPercent, InpTPratio);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   PrintFormat("[AsiaRangeEA] Removed. Reason: %d", reason);
}

//+------------------------------------------------------------------+
void OnTick() {
   datetime now = TimeCurrent();
   if (now == g_lastCheck) return;   // 1 Hz throttle
   g_lastCheck = now;

   MqlDateTime tm; TimeToStruct(now, tm);
   int dateKey = tm.year * 10000 + tm.mon * 100 + tm.day;

   int positions = CountMyPositions();
   int pendings  = CountMyPendings();

   // Once a pending fills → cancel the opposite pending
   if (positions >= 1 && pendings >= 1) {
      DeleteMyPendings();
      return;
   }

   // Session end → cancel any unfilled pendings
   if (tm.hour >= InpSessionEndHour && pendings > 0) {
      PrintFormat("[AsiaRangeEA] Session end (%02d:00) — cancelling %d pending(s)",
                  InpSessionEndHour, pendings);
      DeleteMyPendings();
      return;
   }

   // Session start → place pendings once per day
   if (tm.hour == InpSessionStartHour
       && dateKey != g_lastTradeDateKey
       && positions == 0 && pendings == 0) {
      if (TryPlaceBreakoutPendings()) {
         g_lastTradeDateKey = dateKey;
      } else {
         // Even on skip (range out of bounds), mark today done to avoid retry spam
         g_lastTradeDateKey = dateKey;
      }
   }
}

//+------------------------------------------------------------------+
// Asia range = high/low of last InpRangeHours closed H1 bars
bool ComputeAsiaRange(double &rangeHigh, double &rangeLow) {
   rangeHigh = -DBL_MAX;
   rangeLow  =  DBL_MAX;
   for (int i = 1; i <= InpRangeHours; i++) {
      double h = iHigh(_Symbol, PERIOD_H1, i);
      double l = iLow (_Symbol, PERIOD_H1, i);
      if (h == 0.0 || l == 0.0) return false;
      if (h > rangeHigh) rangeHigh = h;
      if (l < rangeLow)  rangeLow  = l;
   }
   return true;
}

//+------------------------------------------------------------------+
// Lot size from risk % and stop distance (price units)
double CalcLot(double stopPriceDist) {
   if (stopPriceDist <= 0) return InpFallbackLot;

   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if (tickVal <= 0 || tickSize <= 0) return InpFallbackLot;

   double riskMoney   = balance * InpRiskPercent / 100.0;
   double lossPerLot  = (stopPriceDist / tickSize) * tickVal;
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

//+------------------------------------------------------------------+
bool TryPlaceBreakoutPendings() {
   double rangeHigh, rangeLow;
   if (!ComputeAsiaRange(rangeHigh, rangeLow)) {
      Print("[AsiaRangeEA] Cannot compute range — H1 history missing");
      return false;
   }
   double rangeUSD = rangeHigh - rangeLow;
   if (rangeUSD < InpMinRangeUSD) {
      PrintFormat("[AsiaRangeEA] Range=%.2f < min=%.2f — skip day",
                  rangeUSD, InpMinRangeUSD);
      return false;
   }
   if (rangeUSD > InpMaxRangeUSD) {
      PrintFormat("[AsiaRangeEA] Range=%.2f > max=%.2f — skip day",
                  rangeUSD, InpMaxRangeUSD);
      return false;
   }

   double buffer = InpBufferPts * _Point;
   double mid    = (rangeHigh + rangeLow) / 2.0;
   double tpDist = rangeUSD * InpTPratio;

   if (!InpEnableLong && !InpEnableShort) {
      Print("[AsiaRangeEA] Both directions disabled — skip");
      return false;
   }

   long stopLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   double stopDist = stopLevel * _Point;
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   bool placedAny = false;

   if (InpEnableLong) {
      double buyEntry = NormalizeDouble(rangeHigh + buffer,  _Digits);
      double buySL    = NormalizeDouble(mid,                 _Digits);
      double buyTP    = NormalizeDouble(buyEntry + tpDist,   _Digits);
      double buyLot   = CalcLot(buyEntry - buySL);

      if (buyEntry - ask < stopDist) {
         PrintFormat("[AsiaRangeEA] BuyStop entry %.2f too close to Ask %.2f (stopLvl=%d pts) — skip long",
                     buyEntry, ask, (int)stopLevel);
      } else if (!g_trade.BuyStop(buyLot, buyEntry, _Symbol, buySL, buyTP,
                                  ORDER_TIME_DAY, 0, "AsiaRange-BUY")) {
         PrintFormat("[AsiaRangeEA] BuyStop failed: %u %s",
                     g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
      } else {
         PrintFormat("[AsiaRangeEA] BuyStop placed  entry=%.2f lot=%.2f SL=%.2f TP=%.2f",
                     buyEntry, buyLot, buySL, buyTP);
         placedAny = true;
      }
   }

   if (InpEnableShort) {
      double sellEntry = NormalizeDouble(rangeLow - buffer,   _Digits);
      double sellSL    = NormalizeDouble(mid,                 _Digits);
      double sellTP    = NormalizeDouble(sellEntry - tpDist,  _Digits);
      double sellLot   = CalcLot(sellSL - sellEntry);

      if (bid - sellEntry < stopDist) {
         PrintFormat("[AsiaRangeEA] SellStop entry %.2f too close to Bid %.2f (stopLvl=%d pts) — skip short",
                     sellEntry, bid, (int)stopLevel);
      } else if (!g_trade.SellStop(sellLot, sellEntry, _Symbol, sellSL, sellTP,
                                   ORDER_TIME_DAY, 0, "AsiaRange-SELL")) {
         PrintFormat("[AsiaRangeEA] SellStop failed: %u %s",
                     g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
      } else {
         PrintFormat("[AsiaRangeEA] SellStop placed  entry=%.2f lot=%.2f SL=%.2f TP=%.2f",
                     sellEntry, sellLot, sellSL, sellTP);
         placedAny = true;
      }
   }

   PrintFormat("[AsiaRangeEA] Session setup  range=%.2f  H=%.2f L=%.2f  long=%s short=%s",
               rangeUSD, rangeHigh, rangeLow,
               InpEnableLong  ? "on" : "off",
               InpEnableShort ? "on" : "off");
   return placedAny;
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
void DeleteMyPendings() {
   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong tk = OrderGetTicket(i);
      if (tk == 0) continue;
      if (OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if ((ulong)OrderGetInteger(ORDER_MAGIC) != InpMagic) continue;
      if (!g_trade.OrderDelete(tk)) {
         PrintFormat("[AsiaRangeEA] Delete %I64u failed: %u",
                     tk, g_trade.ResultRetcode());
      }
   }
}
//+------------------------------------------------------------------+
