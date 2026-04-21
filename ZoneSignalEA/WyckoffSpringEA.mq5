//+------------------------------------------------------------------+
//|  WyckoffSpringEA.mq5                                             |
//|  Wyckoff Spring/Upthrust reversal on XAUUSD H1.                  |
//|  Detect sideways range → trade the failed breakout back inside.  |
//+------------------------------------------------------------------+
#property copyright "Wyckoff Spring EA"
#property version   "1.00"
#property description "Range accumulation/distribution reversal on H1. Spring=buy, Upthrust=sell."

#include <Trade\Trade.mqh>

//--- Range detection
input int    InpRangeBars        = 18;    // H1 bars to scan for a range (bars 2..N+1)
input double InpMinRangeUSD      = 4.0;   // Skip if range < this ($)
input double InpMaxRangeUSD      = 12.0;  // Skip if range > this (trending, not consolidation)
input double InpRangeRespectPct  = 0.70;  // Min fraction of bar closes that stay inside range

//--- Spring / Upthrust filter
input double InpMinWickUSD       = 0.50;  // Min wick penetration beyond range ($)
input double InpMaxWickUSD       = 4.0;   // Max wick depth (deeper = structural break, not spring)
input double InpMinBodyRatio     = 0.30;  // Min (body / total bar range) — rejects doji traps

//--- Risk & targets
input double InpRiskPercent      = 0.5;   // % balance risked per trade
input double InpTPBufferUSD      = 0.30;  // TP = opposite range edge − this buffer
input double InpSLBufferUSD      = 0.30;  // SL = wick extreme ± this buffer
input double InpFallbackLot      = 0.01;

//--- Trade management
input int    InpCooldownBars     = 6;     // Min H1 bars between trades on same range
input int    InpMaxTradesPerDay  = 2;

//--- Identity
input ulong  InpMagic            = 20260422;
input int    InpSlippagePts      = 20;

//--- Globals
CTrade   g_trade;
datetime g_lastBarTime   = 0;   // detect new H1 bar close
datetime g_lastEntryTime = 0;   // cooldown anchor
int      g_todayDateKey  = 0;
int      g_todayTrades   = 0;

//+------------------------------------------------------------------+
int OnInit() {
   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints((ulong)InpSlippagePts);
   PrintFormat("[Wyckoff] Init  sym=%s  rangeBars=%d  minR=%.2f maxR=%.2f  minWick=%.2f maxWick=%.2f  risk=%.2f%%",
               _Symbol, InpRangeBars, InpMinRangeUSD, InpMaxRangeUSD,
               InpMinWickUSD, InpMaxWickUSD, InpRiskPercent);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   PrintFormat("[Wyckoff] Removed. Reason: %d", reason);
}

//+------------------------------------------------------------------+
void OnTick() {
   // Only act on a fresh H1 bar close
   datetime bar1Time = iTime(_Symbol, PERIOD_H1, 1);
   if (bar1Time == 0 || bar1Time == g_lastBarTime) return;
   g_lastBarTime = bar1Time;

   // Daily trade cap
   MqlDateTime tm; TimeToStruct(TimeCurrent(), tm);
   int dateKey = tm.year * 10000 + tm.mon * 100 + tm.day;
   if (dateKey != g_todayDateKey) {
      g_todayDateKey = dateKey;
      g_todayTrades  = 0;
   }
   if (g_todayTrades >= InpMaxTradesPerDay) return;

   // Skip if we already have a position from this EA
   if (CountMyPositions() > 0) return;

   // Cooldown: wait N bars since last entry
   if (g_lastEntryTime > 0) {
      int barsSince = (int)((bar1Time - g_lastEntryTime) / (PeriodSeconds(PERIOD_H1)));
      if (barsSince < InpCooldownBars) return;
   }

   // --- Detect range on bars 2..N+1 ---
   double rangeHigh, rangeLow;
   if (!ComputeRange(rangeHigh, rangeLow)) return;

   double rangeUSD = rangeHigh - rangeLow;
   if (rangeUSD < InpMinRangeUSD || rangeUSD > InpMaxRangeUSD) return;

   // --- Check spring / upthrust on bar 1 ---
   double o1 = iOpen (_Symbol, PERIOD_H1, 1);
   double h1 = iHigh (_Symbol, PERIOD_H1, 1);
   double l1 = iLow  (_Symbol, PERIOD_H1, 1);
   double c1 = iClose(_Symbol, PERIOD_H1, 1);
   if (o1 == 0 || h1 == 0) return;

   double barRange = h1 - l1;
   if (barRange <= 0) return;
   double bodyRatio = MathAbs(c1 - o1) / barRange;

   // SPRING = wick breaks below range, closes back inside, bullish body
   bool isSpring =
        l1 < rangeLow
     && c1 > rangeLow
     && c1 > o1
     && (rangeLow - l1) >= InpMinWickUSD
     && (rangeLow - l1) <= InpMaxWickUSD
     && bodyRatio >= InpMinBodyRatio;

   // UPTHRUST = wick breaks above range, closes back inside, bearish body
   bool isUpthrust =
        h1 > rangeHigh
     && c1 < rangeHigh
     && c1 < o1
     && (h1 - rangeHigh) >= InpMinWickUSD
     && (h1 - rangeHigh) <= InpMaxWickUSD
     && bodyRatio >= InpMinBodyRatio;

   if (isSpring) {
      PlaceSpringBuy(rangeHigh, rangeLow, l1);
   } else if (isUpthrust) {
      PlaceUpthrustSell(rangeHigh, rangeLow, h1);
   }
}

//+------------------------------------------------------------------+
// Range = high/low of bars 2..N+1 (skip bar 1 = candidate spring, bar 0 = forming)
// Also validate range is "respected": enough closes stay inside
bool ComputeRange(double &rangeHigh, double &rangeLow) {
   rangeHigh = -DBL_MAX;
   rangeLow  =  DBL_MAX;
   int bars = InpRangeBars;
   for (int i = 2; i <= bars + 1; i++) {
      double h = iHigh(_Symbol, PERIOD_H1, i);
      double l = iLow (_Symbol, PERIOD_H1, i);
      if (h == 0.0 || l == 0.0) return false;
      if (h > rangeHigh) rangeHigh = h;
      if (l < rangeLow)  rangeLow  = l;
   }
   // Respect check: % of closes inside [rangeLow + 10%, rangeHigh - 10%] buffer
   double buffer = (rangeHigh - rangeLow) * 0.10;
   double innerHigh = rangeHigh - buffer;
   double innerLow  = rangeLow  + buffer;
   int inside = 0;
   for (int i = 2; i <= bars + 1; i++) {
      double c = iClose(_Symbol, PERIOD_H1, i);
      if (c >= innerLow && c <= innerHigh) inside++;
   }
   double frac = (double)inside / (double)bars;
   if (frac < InpRangeRespectPct) return false;
   return true;
}

//+------------------------------------------------------------------+
double CalcLot(double stopPriceDist) {
   if (stopPriceDist <= 0) return InpFallbackLot;
   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   double tickVal  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if (tickVal <= 0 || tickSize <= 0) return InpFallbackLot;

   double riskMoney  = balance * InpRiskPercent / 100.0;
   double lossPerLot = (stopPriceDist / tickSize) * tickVal;
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
void PlaceSpringBuy(double rangeHigh, double rangeLow, double wickLow) {
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if (ask <= 0) return;

   double sl = NormalizeDouble(wickLow   - InpSLBufferUSD, _Digits);
   double tp = NormalizeDouble(rangeHigh - InpTPBufferUSD, _Digits);
   double slDist = ask - sl;
   double tpDist = tp  - ask;
   if (slDist <= 0 || tpDist <= 0) {
      PrintFormat("[Wyckoff] Spring skip — bad geometry  ask=%.2f sl=%.2f tp=%.2f", ask, sl, tp);
      return;
   }
   if (tpDist < slDist * 1.0) {
      PrintFormat("[Wyckoff] Spring skip — RR<1  slDist=%.2f tpDist=%.2f", slDist, tpDist);
      return;
   }
   double lot = CalcLot(slDist);
   if (!g_trade.Buy(lot, _Symbol, 0, sl, tp, "Wyckoff-Spring")) {
      PrintFormat("[Wyckoff] Spring Buy failed: %u %s",
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
      return;
   }
   g_lastEntryTime = iTime(_Symbol, PERIOD_H1, 1);
   g_todayTrades++;
   PrintFormat("[Wyckoff] SPRING  buy@%.2f lot=%.2f SL=%.2f TP=%.2f  range[%.2f..%.2f] wickLow=%.2f",
               ask, lot, sl, tp, rangeLow, rangeHigh, wickLow);
}

//+------------------------------------------------------------------+
void PlaceUpthrustSell(double rangeHigh, double rangeLow, double wickHigh) {
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   if (bid <= 0) return;

   double sl = NormalizeDouble(wickHigh + InpSLBufferUSD, _Digits);
   double tp = NormalizeDouble(rangeLow + InpTPBufferUSD, _Digits);
   double slDist = sl  - bid;
   double tpDist = bid - tp;
   if (slDist <= 0 || tpDist <= 0) {
      PrintFormat("[Wyckoff] Upthrust skip — bad geometry  bid=%.2f sl=%.2f tp=%.2f", bid, sl, tp);
      return;
   }
   if (tpDist < slDist * 1.0) {
      PrintFormat("[Wyckoff] Upthrust skip — RR<1  slDist=%.2f tpDist=%.2f", slDist, tpDist);
      return;
   }
   double lot = CalcLot(slDist);
   if (!g_trade.Sell(lot, _Symbol, 0, sl, tp, "Wyckoff-Upthrust")) {
      PrintFormat("[Wyckoff] Upthrust Sell failed: %u %s",
                  g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
      return;
   }
   g_lastEntryTime = iTime(_Symbol, PERIOD_H1, 1);
   g_todayTrades++;
   PrintFormat("[Wyckoff] UPTHRUST  sell@%.2f lot=%.2f SL=%.2f TP=%.2f  range[%.2f..%.2f] wickHigh=%.2f",
               bid, lot, sl, tp, rangeLow, rangeHigh, wickHigh);
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
