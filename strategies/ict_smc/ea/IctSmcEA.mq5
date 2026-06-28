//+------------------------------------------------------------------+
//|  IctSmcEA.mq5                                                     |
//|  ICT / Smart-Money-Concepts market-structure EA                  |
//|  Phase 1: detect + draw swings, BOS, MSS (CHoCH), HTF bias, Fibo  |
//|  Phase 2: laddered OTE entries (3 limits), structure SL,          |
//|           opposing-liquidity TP. Gated by InpEnableTrading.       |
//|  Phase 3: break-even, trailing stop, partial close on filled pos. |
//|  Phase 4: per-tier TP ladder (tier1=nearest liquidity .. final).  |
//|  CI/CD deployed                                                  |
//+------------------------------------------------------------------+
#property copyright   "IctSmc EA"
#property version     "4.00"
#property description "ICT structure + laddered OTE entries + per-tier TP + BE/trailing (gated by InpEnableTrading)"

#include <Trade\Trade.mqh>

//--- Input Parameters --------------------------------------------------------
//--- Timeframes
input ENUM_TIMEFRAMES InpHTF        = PERIOD_H4;     // HTF (directional bias)
input ENUM_TIMEFRAMES InpLTF        = PERIOD_M15;    // LTF (MSS + entry zone)
//--- Swing detection
input int    InpSwingLookback       = 3;            // Fractal half-width N (bars each side)
input int    InpMaxHistoryBars      = 500;          // Bars scanned per timeframe
input int    InpMaxSwingsTracked    = 60;           // Max swings kept per timeframe
//--- Structure
input int    InpBiasSwingsForTrend  = 2;            // # of HH/HL (or LH/LL) pairs to confirm bias
//--- Fibonacci
input bool   InpDrawFib             = true;         // Draw OTE fib after an MSS
input bool   InpDrawEquilibrium     = true;         // Draw 0.5 (premium/discount) line
input double InpFibOTE1             = 0.62;         // OTE upper
input double InpFibOTE2             = 0.705;        // OTE sweet spot
input double InpFibOTE3             = 0.79;         // OTE lower
//--- Drawing
input bool   InpShowHTFObjects      = true;         // Draw HTF swings/BOS/MSS too
input color  InpColSwingHigh        = clrTomato;    // Swing-high marker
input color  InpColSwingLow         = clrDodgerBlue;// Swing-low marker
input color  InpColBOS              = clrLimeGreen; // BOS line (continuation)
input color  InpColMSS              = clrMagenta;   // MSS line (reversal/CHoCH)
input color  InpColFib              = clrGoldenrod; // Fib OTE band/levels
input color  InpColBiasBull         = clrLimeGreen; // Bias label when bullish
input color  InpColBiasBear         = clrTomato;    // Bias label when bearish
//--- Trading (Phase 2) — entry / SL / TP
input bool   InpEnableTrading       = false;        // MASTER: false=draw only, true=place orders
input double InpEntryFib1           = 0.62;         // Entry tier 1 (OTE upper)
input double InpEntryFib2           = 0.705;        // Entry tier 2 (OTE mid)
input double InpEntryFib3           = 0.785;        // Entry tier 3 (OTE lower)
input double InpLotPerEntry         = 0.01;         // Lot per entry (x3 ladder)
input int    InpMaxSetupPositions   = 3;            // Max positions/pendings per direction
input double InpMaxTotalLots        = 0.30;         // Max total lots per direction
input double InpSlBufferPts         = 200;          // SL buffer beyond the MSS swing (points)
input int    InpPendingExpiryBars   = 12;           // Cancel unfilled limit after N LTF bars
input double InpMinStopPts          = 150;          // Min SL distance to accept a setup (points)
input double InpFallbackRR          = 2.0;          // TP fallback R:R when no opposing liquidity
input bool   InpPerTierTP           = true;         // true=per-tier liquidity TP ladder; false=shared nearest TP
input bool   InpRequireBiasAlign    = true;         // Only trade MSS aligned with HTF bias
input long   InpMaxSpreadPts        = 50;           // Max spread to place entries (0=disabled)
input color  InpColEntry            = clrAqua;      // Entry lines
input color  InpColSL               = clrRed;       // SL line
input color  InpColTP               = clrLime;      // TP line
//--- Trade management (Phase 3) — applies to filled positions of this magic
input bool   InpEnableBreakEven     = true;         // Move SL to break-even in profit
input double InpBeTriggerPts        = 300;          // Profit to arm break-even (points)
input double InpBeOffsetPts         = 20;           // SL offset past entry at BE (points)
input bool   InpEnableTrailing      = true;         // Trail SL once far enough in profit
input double InpTrailStartPts       = 500;          // Profit to start trailing (points)
input double InpTrailDistPts        = 300;          // Trail SL this far behind price (points)
input double InpTrailStepPts        = 50;           // Min SL improvement before modify (points)
input bool   InpEnablePartialClose  = false;        // Take partial profit (needs lot >= 2x min)
input double InpPartialClosePts     = 400;          // Profit to take partial (points)
input double InpPartialClosePct     = 0.5;          // Fraction of volume to close (0..1)
//--- Identity
input ulong  InpMagic               = 20260627;     // Magic number
input bool   InpVerboseLog          = true;         // Verbose [IctSmc] logging

//--- Shadow globals (mutable, populated in InitShadowsFromInputs + LoadAccountConfig)
ENUM_TIMEFRAMES g_cfg_HTF;
ENUM_TIMEFRAMES g_cfg_LTF;
int     g_cfg_SwingLookback;
int     g_cfg_MaxHistoryBars;
int     g_cfg_MaxSwingsTracked;
int     g_cfg_BiasSwingsForTrend;
bool    g_cfg_DrawFib;
bool    g_cfg_DrawEquilibrium;
double  g_cfg_FibOTE1;
double  g_cfg_FibOTE2;
double  g_cfg_FibOTE3;
bool    g_cfg_ShowHTFObjects;
color   g_cfg_ColSwingHigh;
color   g_cfg_ColSwingLow;
color   g_cfg_ColBOS;
color   g_cfg_ColMSS;
color   g_cfg_ColFib;
color   g_cfg_ColBiasBull;
color   g_cfg_ColBiasBear;
bool    g_cfg_EnableTrading;
double  g_cfg_EntryFib1;
double  g_cfg_EntryFib2;
double  g_cfg_EntryFib3;
double  g_cfg_LotPerEntry;
int     g_cfg_MaxSetupPositions;
double  g_cfg_MaxTotalLots;
double  g_cfg_SlBufferPts;
int     g_cfg_PendingExpiryBars;
double  g_cfg_MinStopPts;
double  g_cfg_FallbackRR;
bool    g_cfg_PerTierTP;
bool    g_cfg_RequireBiasAlign;
long    g_cfg_MaxSpreadPts;
color   g_cfg_ColEntry;
color   g_cfg_ColSL;
color   g_cfg_ColTP;
bool    g_cfg_EnableBreakEven;
double  g_cfg_BeTriggerPts;
double  g_cfg_BeOffsetPts;
bool    g_cfg_EnableTrailing;
double  g_cfg_TrailStartPts;
double  g_cfg_TrailDistPts;
double  g_cfg_TrailStepPts;
bool    g_cfg_EnablePartialClose;
double  g_cfg_PartialClosePts;
double  g_cfg_PartialClosePct;
ulong   g_cfg_Magic;
bool    g_cfg_VerboseLog;
bool    g_cfg_Enabled = true;

//+------------------------------------------------------------------+
//| Market-structure data structures                                 |
//+------------------------------------------------------------------+
enum SwingType { SWING_HIGH, SWING_LOW };

struct SwingPoint {
   datetime  time;    // iTime of the pivot bar (stable key across chart periods)
   double    price;   // iHigh (high) or iLow (low)
   int       shift;   // bar index at detection (advisory only)
   SwingType type;
};

enum TrendDir { TREND_NONE, TREND_BULL, TREND_BEAR };

struct StructureState {
   ENUM_TIMEFRAMES tf;
   SwingPoint swings[];        // chronological (oldest first, newest last)
   TrendDir   bias;
   double     lastBrokenHigh;  // price of the last swing-high taken out
   double     lastBrokenLow;   // price of the last swing-low taken out
   datetime   lastBOSTime;
   datetime   lastMSSTime;
   bool       lastBreakWasMSS;
   bool       lastBreakBullish;   // direction of the most recent classified break
};

//+------------------------------------------------------------------+
//| Trade setup derived from an LTF MSS                               |
//+------------------------------------------------------------------+
struct TradeSetup {
   bool      active;
   bool      bullish;
   datetime  mssTime;      // dedup key: one setup per MSS
   double    entry[3];     // 3 laddered OTE entry prices
   double    sl;
   double    tp[3];        // per-tier TP ladder (tier1=nearest liquidity .. tier3=final)
   double    rr[3];        // per-tier R:R
   datetime  expiry;       // pending-order expiry
};

//--- Globals
CTrade     g_trade;
StructureState g_htf;
StructureState g_ltf;
TradeSetup g_setup;
datetime   g_lastHTFBar = 0;
datetime   g_lastLTFBar = 0;
datetime   g_lastManageCheck = 0;

#define OBJ_PREFIX "IctSmc_"

//+------------------------------------------------------------------+
int OnInit() {
   InitShadowsFromInputs();
   LoadAccountConfig();

   g_trade.SetExpertMagicNumber(g_cfg_Magic);
   g_trade.SetDeviationInPoints(30);

   g_htf.tf  = g_cfg_HTF;
   g_ltf.tf  = g_cfg_LTF;
   g_htf.bias = TREND_NONE;
   g_ltf.bias = TREND_NONE;
   ZeroBreaks(g_htf);
   ZeroBreaks(g_ltf);
   ZeroMemory(g_setup);

   ObjectsDeleteAll(0, OBJ_PREFIX);
   EnsureOutcomesDir();

   if (!g_cfg_Enabled)
      Print("[IctSmc][Config] DISABLED — draws structure only, no new entries.");
   if (g_cfg_LTF == g_cfg_HTF)
      Print("[IctSmc][WARN] HTF == LTF — bias and entry structure use the same timeframe.");
   PrintFormat("[IctSmc] Trading=%s (InpEnableTrading). %s",
               g_cfg_EnableTrading ? "ON" : "OFF (draw-only)",
               g_cfg_EnableTrading ? "Orders WILL be placed on aligned MSS setups."
                                   : "Entry/SL/TP are drawn but no orders are placed.");

   //--- Seed both structures so the chart paints immediately
   RecomputeStructure(g_htf);
   g_htf.bias = ComputeBias(g_htf);
   DrawStructure(g_htf, g_cfg_ShowHTFObjects);
   DrawBiasLabel(g_htf.bias);

   RecomputeStructure(g_ltf);
   g_ltf.bias = ComputeBias(g_ltf);
   ClassifyLatestBreak(g_ltf);
   DrawStructure(g_ltf, true);

   g_lastHTFBar = iTime(_Symbol, g_cfg_HTF, 0);
   g_lastLTFBar = iTime(_Symbol, g_cfg_LTF, 0);
   ChartRedraw();

   PrintFormat("[IctSmc] Initialized. HTF=%s LTF=%s lookback=%d bias=%s",
               TfStr(g_cfg_HTF), TfStr(g_cfg_LTF), g_cfg_SwingLookback, BiasStr(g_htf.bias));
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   ObjectsDeleteAll(0, OBJ_PREFIX);
   ChartRedraw();
   Print("[IctSmc] Removed. Reason: ", reason);
}

//+------------------------------------------------------------------+
void OnTick() {
   //--- Manage filled positions of this magic (BE/trail/partial), throttled to 1Hz.
   //--- Runs regardless of InpEnableTrading so existing positions stay managed.
   datetime now = TimeCurrent();
   if (now != g_lastManageCheck) {
      g_lastManageCheck = now;
      ManageOpenPositions();
   }

   //--- HTF new-bar → recompute bias
   datetime htfBar = iTime(_Symbol, g_cfg_HTF, 0);
   if (htfBar != g_lastHTFBar) {
      g_lastHTFBar = htfBar;
      OnNewHTFBar();
   }

   //--- LTF new-bar → recompute structure + classify break + fib
   datetime ltfBar = iTime(_Symbol, g_cfg_LTF, 0);
   if (ltfBar != g_lastLTFBar) {
      g_lastLTFBar = ltfBar;
      OnNewLTFBar();
   }
}

//+------------------------------------------------------------------+
void OnNewHTFBar() {
   if (iBars(_Symbol, g_cfg_HTF) <= 2 * g_cfg_SwingLookback + 2) return;
   RecomputeStructure(g_htf);
   TrendDir newBias = ComputeBias(g_htf);
   ClassifyLatestBreak(g_htf);
   g_htf.bias = newBias;
   DrawStructure(g_htf, g_cfg_ShowHTFObjects);
   DrawBiasLabel(g_htf.bias);
   PruneOldObjects();
   ChartRedraw();
}

//+------------------------------------------------------------------+
void OnNewLTFBar() {
   if (iBars(_Symbol, g_cfg_LTF) <= 2 * g_cfg_SwingLookback + 2) return;
   RecomputeStructure(g_ltf);
   //--- classify break against the bias *before* folding this break in
   bool wasMSS = ClassifyLatestBreak(g_ltf);
   g_ltf.bias = ComputeBias(g_ltf);
   DrawStructure(g_ltf, true);
   if (wasMSS && g_cfg_DrawFib)
      DrawFibForLastMSS(g_ltf);

   //--- Phase 2: build a fresh setup on a new MSS, then (optionally) place orders
   if (wasMSS)
      BuildSetupFromMSS(g_ltf);

   ManagePendings();   // cancel expired limits
   PruneOldObjects();
   ChartRedraw();
}

//+------------------------------------------------------------------+
//| Reset the break-tracking fields of a structure                   |
//+------------------------------------------------------------------+
void ZeroBreaks(StructureState &st) {
   st.lastBrokenHigh   = 0.0;
   st.lastBrokenLow    = 0.0;
   st.lastBOSTime      = 0;
   st.lastMSSTime      = 0;
   st.lastBreakWasMSS  = false;
   st.lastBreakBullish = false;
}

//+------------------------------------------------------------------+
//| Recompute the swing array for a timeframe                        |
//+------------------------------------------------------------------+
void RecomputeStructure(StructureState &st) {
   DetectSwings(st);
}

//+------------------------------------------------------------------+
//| Fractal pivot detection                                          |
//+------------------------------------------------------------------+
bool IsFractalHigh(const ENUM_TIMEFRAMES tf, const int shift, const int N) {
   double h = iHigh(_Symbol, tf, shift);
   if (h <= 0.0) return false;
   for (int k = 1; k <= N; k++) {
      if (iHigh(_Symbol, tf, shift + k) >= h) return false;
      if (iHigh(_Symbol, tf, shift - k) >= h) return false;
   }
   return true;
}

bool IsFractalLow(const ENUM_TIMEFRAMES tf, const int shift, const int N) {
   double l = iLow(_Symbol, tf, shift);
   if (l <= 0.0) return false;
   for (int k = 1; k <= N; k++) {
      if (iLow(_Symbol, tf, shift + k) <= l) return false;
      if (iLow(_Symbol, tf, shift - k) <= l) return false;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Rebuild swings[] chronologically (oldest first)                  |
//+------------------------------------------------------------------+
void DetectSwings(StructureState &st) {
   ArrayResize(st.swings, 0);

   int N    = g_cfg_SwingLookback;
   int bars = (int)MathMin(g_cfg_MaxHistoryBars, iBars(_Symbol, st.tf));
   if (bars < 2 * N + 2) return;

   //--- iterate oldest → newest so the array stays chronological
   for (int shift = bars - N - 1; shift >= N; shift--) {
      if (IsFractalHigh(st.tf, shift, N)) {
         AppendSwing(st, iTime(_Symbol, st.tf, shift), iHigh(_Symbol, st.tf, shift), shift, SWING_HIGH);
      } else if (IsFractalLow(st.tf, shift, N)) {
         AppendSwing(st, iTime(_Symbol, st.tf, shift), iLow(_Symbol, st.tf, shift), shift, SWING_LOW);
      }
   }

   //--- trim to the newest g_cfg_MaxSwingsTracked entries
   int total = ArraySize(st.swings);
   if (total > g_cfg_MaxSwingsTracked) {
      int drop = total - g_cfg_MaxSwingsTracked;
      for (int i = 0; i < g_cfg_MaxSwingsTracked; i++)
         st.swings[i] = st.swings[i + drop];
      ArrayResize(st.swings, g_cfg_MaxSwingsTracked);
   }
}

void AppendSwing(StructureState &st, datetime t, double price, int shift, SwingType type) {
   int idx = ArraySize(st.swings);
   ArrayResize(st.swings, idx + 1);
   st.swings[idx].time  = t;
   st.swings[idx].price = price;
   st.swings[idx].shift = shift;
   st.swings[idx].type  = type;
}

//+------------------------------------------------------------------+
//| HTF/LTF bias from the swing sequence                             |
//+------------------------------------------------------------------+
TrendDir ComputeBias(StructureState &st) {
   int need = g_cfg_BiasSwingsForTrend + 1;
   double H[]; double L[];
   if (!LastSwings(st, SWING_HIGH, need, H)) return st.bias;
   if (!LastSwings(st, SWING_LOW,  need, L)) return st.bias;

   bool higherHighs = true, higherLows = true, lowerHighs = true, lowerLows = true;
   for (int i = 1; i < ArraySize(H); i++) {
      if (!(H[i] > H[i - 1])) higherHighs = false;
      if (!(H[i] < H[i - 1])) lowerHighs  = false;
   }
   for (int i = 1; i < ArraySize(L); i++) {
      if (!(L[i] > L[i - 1])) higherLows = false;
      if (!(L[i] < L[i - 1])) lowerLows  = false;
   }

   if (higherHighs && higherLows) return TREND_BULL;
   if (lowerHighs  && lowerLows)  return TREND_BEAR;
   return st.bias;  // ambiguous → hold previous (sticky)
}

//+------------------------------------------------------------------+
//| Collect the last `count` prices of a given swing type (chrono)   |
//+------------------------------------------------------------------+
bool LastSwings(StructureState &st, SwingType type, int count, double &out[]) {
   ArrayResize(out, 0);
   int n = ArraySize(st.swings);
   for (int i = n - 1; i >= 0 && ArraySize(out) < count; i--) {
      if (st.swings[i].type == type) {
         int idx = ArraySize(out);
         ArrayResize(out, idx + 1);
         out[idx] = st.swings[i].price;
      }
   }
   if (ArraySize(out) < count) return false;
   //--- reverse so out[] is chronological (oldest first)
   int m = ArraySize(out);
   for (int i = 0; i < m / 2; i++) {
      double tmp = out[i];
      out[i] = out[m - 1 - i];
      out[m - 1 - i] = tmp;
   }
   return true;
}

//+------------------------------------------------------------------+
//| Find the most recent swing of a type. Returns false if none.     |
//+------------------------------------------------------------------+
bool LastSwingOf(StructureState &st, SwingType type, SwingPoint &out) {
   for (int i = ArraySize(st.swings) - 1; i >= 0; i--) {
      if (st.swings[i].type == type) {
         out = st.swings[i];
         return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Most recent swing of a type strictly before `before`             |
//+------------------------------------------------------------------+
bool LastSwingBefore(StructureState &st, SwingType type, datetime before, SwingPoint &out) {
   for (int i = ArraySize(st.swings) - 1; i >= 0; i--) {
      if (st.swings[i].type == type && st.swings[i].time < before) {
         out = st.swings[i];
         return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Classify the latest break as BOS or MSS. Returns true if MSS.    |
//| Uses the last CLOSED bar to avoid repainting.                    |
//+------------------------------------------------------------------+
bool ClassifyLatestBreak(StructureState &st) {
   double  c        = iClose(_Symbol, st.tf, 1);
   datetime breakAt = iTime(_Symbol, st.tf, 1);
   if (c <= 0.0) return false;

   SwingPoint hi, lo;
   bool haveHi = LastSwingBefore(st, SWING_HIGH, breakAt, hi);
   bool haveLo = LastSwingBefore(st, SWING_LOW,  breakAt, lo);

   //--- bullish break: close above the most recent swing high
   if (haveHi && c > hi.price && hi.price != st.lastBrokenHigh) {
      bool isMSS = (st.bias != TREND_BULL);  // break against (or absent) bull trend = shift
      st.lastBrokenHigh   = hi.price;
      st.lastBreakWasMSS  = isMSS;
      st.lastBreakBullish = true;
      if (isMSS) st.lastMSSTime = breakAt; else st.lastBOSTime = breakAt;
      DrawBreakLine(st, hi, breakAt, isMSS, true);
      if (g_cfg_VerboseLog)
         PrintFormat("[IctSmc] %s %s on %s @ %.5f (took swing-high %.5f @ %s)",
                     isMSS ? "MSS" : "BOS", "BULL", TfStr(st.tf), c, hi.price,
                     TimeToString(hi.time));
      return isMSS;
   }

   //--- bearish break: close below the most recent swing low
   if (haveLo && c < lo.price && lo.price != st.lastBrokenLow) {
      bool isMSS = (st.bias != TREND_BEAR);
      st.lastBrokenLow    = lo.price;
      st.lastBreakWasMSS  = isMSS;
      st.lastBreakBullish = false;
      if (isMSS) st.lastMSSTime = breakAt; else st.lastBOSTime = breakAt;
      DrawBreakLine(st, lo, breakAt, isMSS, false);
      if (g_cfg_VerboseLog)
         PrintFormat("[IctSmc] %s %s on %s @ %.5f (took swing-low %.5f @ %s)",
                     isMSS ? "MSS" : "BOS", "BEAR", TfStr(st.tf), c, lo.price,
                     TimeToString(lo.time));
      return isMSS;
   }

   return false;
}

//+==================================================================+
//|  DRAWING LAYER                                                   |
//+==================================================================+

//+------------------------------------------------------------------+
//| Draw all swing markers for a timeframe (delete-and-redraw)       |
//+------------------------------------------------------------------+
void DrawStructure(StructureState &st, bool show) {
   //--- clear this TF's swing markers (BOS/MSS lines persist by event)
   DeleteByPrefix(OBJ_PREFIX + "SWH_" + TfStr(st.tf) + "_");
   DeleteByPrefix(OBJ_PREFIX + "SWL_" + TfStr(st.tf) + "_");
   if (!show) return;

   for (int i = 0; i < ArraySize(st.swings); i++) {
      SwingPoint sp = st.swings[i];
      bool isHigh = (sp.type == SWING_HIGH);
      string name = OBJ_PREFIX + (isHigh ? "SWH_" : "SWL_") + TfStr(st.tf) + "_"
                  + IntegerToString((long)sp.time);
      color clr   = isHigh ? g_cfg_ColSwingHigh : g_cfg_ColSwingLow;
      string glyph = isHigh ? "▼" : "▲";
      SetText(name, sp.time, sp.price, glyph, clr,
              isHigh ? ANCHOR_LOWER : ANCHOR_UPPER, 10);
   }
}

//+------------------------------------------------------------------+
//| Draw a BOS/MSS line at the broken swing price                    |
//+------------------------------------------------------------------+
void DrawBreakLine(StructureState &st, const SwingPoint &broken, datetime breakAt,
                   bool isMSS, bool bullish) {
   if (st.tf == g_cfg_HTF && !g_cfg_ShowHTFObjects) return;  // honor HTF visibility toggle
   string tag  = isMSS ? "MSS_" : "BOS_";
   string ttag = isMSS ? "MSST_" : "BOST_";
   string name = OBJ_PREFIX + tag  + TfStr(st.tf) + "_" + IntegerToString((long)breakAt);
   string tnm  = OBJ_PREFIX + ttag + TfStr(st.tf) + "_" + IntegerToString((long)breakAt);
   color  clr  = isMSS ? g_cfg_ColMSS : g_cfg_ColBOS;
   ENUM_LINE_STYLE style = isMSS ? STYLE_DASHDOT : STYLE_SOLID;

   SetTrend(name, broken.time, broken.price, breakAt, broken.price, clr, style, 2);
   SetText(tnm, breakAt, broken.price, (isMSS ? "MSS " : "BOS ") + TfStr(st.tf),
           clr, bullish ? ANCHOR_LOWER : ANCHOR_UPPER, 9);
}

//+------------------------------------------------------------------+
//| Draw the OTE fib for the most recent MSS leg on this TF          |
//+------------------------------------------------------------------+
void DrawFibForLastMSS(StructureState &st) {
   SwingPoint legStart, legEnd;
   if (st.lastBreakBullish) {
      //--- bullish MSS: leg from prior swing-low (0.0) up to broken swing-high (1.0)
      if (!LastSwingOf(st, SWING_HIGH, legEnd)) return;
      if (!LastSwingBefore(st, SWING_LOW, legEnd.time, legStart)) return;
   } else {
      //--- bearish MSS: leg from prior swing-high (0.0) down to broken swing-low (1.0)
      if (!LastSwingOf(st, SWING_LOW, legEnd)) return;
      if (!LastSwingBefore(st, SWING_HIGH, legEnd.time, legStart)) return;
   }

   DrawFibOTE(st, legStart, legEnd);
}

//+------------------------------------------------------------------+
//| Draw fib levels + OTE band for a leg (legStart=0.0, legEnd=1.0)  |
//+------------------------------------------------------------------+
void DrawFibOTE(StructureState &st, const SwingPoint &legStart, const SwingPoint &legEnd) {
   ClearFibObjects();

   double range = legEnd.price - legStart.price;
   if (MathAbs(range) < _Point) return;

   datetime t1 = legStart.time;
   datetime t2 = legEnd.time + 6 * PeriodSeconds(st.tf);  // extend a few bars right

   //--- equilibrium 0.5 (premium/discount boundary), distinct dashed style
   if (g_cfg_DrawEquilibrium) {
      double eq = legEnd.price - 0.5 * range;
      DrawFibLevel("0.50", 0.5, eq, t1, t2, clrSilver, STYLE_DASH);
   }

   //--- OTE band 0.62 ↔ 0.79
   double pUpper = legEnd.price - g_cfg_FibOTE1 * range;  // 0.62
   double pMid   = legEnd.price - g_cfg_FibOTE2 * range;  // 0.705
   double pLower = legEnd.price - g_cfg_FibOTE3 * range;  // 0.79

   SetRect(OBJ_PREFIX + "FIB_BAND", t1, pUpper, t2, pLower, g_cfg_ColFib);

   DrawFibLevel(DoubleToString(g_cfg_FibOTE1, 3), g_cfg_FibOTE1, pUpper, t1, t2, g_cfg_ColFib, STYLE_DOT);
   DrawFibLevel(DoubleToString(g_cfg_FibOTE2, 3), g_cfg_FibOTE2, pMid,   t1, t2, g_cfg_ColFib, STYLE_SOLID);
   DrawFibLevel(DoubleToString(g_cfg_FibOTE3, 3), g_cfg_FibOTE3, pLower, t1, t2, g_cfg_ColFib, STYLE_DOT);

   if (g_cfg_VerboseLog)
      PrintFormat("[IctSmc] Fib OTE drawn on %s: leg %.5f→%.5f  OTE[%.5f .. %.5f]",
                  TfStr(st.tf), legStart.price, legEnd.price, pUpper, pLower);
}

void DrawFibLevel(string ratio, double r, double price, datetime t1, datetime t2,
                  color clr, ENUM_LINE_STYLE style) {
   string ln = OBJ_PREFIX + "FIB_L_" + ratio;
   string tx = OBJ_PREFIX + "FIB_T_" + ratio;
   SetTrend(ln, t1, price, t2, price, clr, style, 1);
   SetText(tx, t2, price, ratio, clr, ANCHOR_LEFT, 8);
}

void ClearFibObjects() {
   DeleteByPrefix(OBJ_PREFIX + "FIB_");
}

//+------------------------------------------------------------------+
//| HTF bias label in the chart corner                               |
//+------------------------------------------------------------------+
void DrawBiasLabel(TrendDir bias) {
   string name = OBJ_PREFIX + "BIAS";
   string txt  = "ICT HTF " + TfStr(g_cfg_HTF) + " bias: " + BiasStr(bias);
   color  clr  = (bias == TREND_BULL) ? g_cfg_ColBiasBull
               : (bias == TREND_BEAR) ? g_cfg_ColBiasBear : clrSilver;

   if (ObjectFind(0, name) < 0) {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 12);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 22);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 11);
      ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
   }
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
}

//+------------------------------------------------------------------+
//| Low-level object helpers                                         |
//+------------------------------------------------------------------+
void SetTrend(string name, datetime t1, double p1, datetime t2, double p2,
              color clr, ENUM_LINE_STYLE style, int width) {
   if (ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_TREND, 0, t1, p1, t2, p2);
   ObjectMove(0, name, 0, t1, p1);
   ObjectMove(0, name, 1, t2, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, false);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
}

void SetText(string name, datetime t, double price, string text, color clr,
             ENUM_ANCHOR_POINT anchor, int fontsize) {
   if (ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_TEXT, 0, t, price);
   ObjectMove(0, name, 0, t, price);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontsize);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
}

void SetRect(string name, datetime t1, double p1, datetime t2, double p2, color clr) {
   if (ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, p1, t2, p2);
   ObjectMove(0, name, 0, t1, p1);
   ObjectMove(0, name, 1, t2, p2);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, name, OBJPROP_FILL, true);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
}

//+------------------------------------------------------------------+
//| Delete all objects whose name starts with `prefix`               |
//+------------------------------------------------------------------+
void DeleteByPrefix(string prefix) {
   int total = ObjectsTotal(0);
   for (int i = total - 1; i >= 0; i--) {
      string name = ObjectName(0, i);
      if (StringFind(name, prefix) == 0)
         ObjectDelete(0, name);
   }
}

//+------------------------------------------------------------------+
//| Prune swing/BOS/MSS objects older than the scan window          |
//+------------------------------------------------------------------+
void PruneOldObjects() {
   datetime cutoff = TimeCurrent() - (datetime)((long)g_cfg_MaxHistoryBars * PeriodSeconds(g_cfg_HTF));
   int total = ObjectsTotal(0);
   for (int i = total - 1; i >= 0; i--) {
      string name = ObjectName(0, i);
      if (StringFind(name, OBJ_PREFIX) != 0) continue;
      if (StringFind(name, "FIB_") >= 0)     continue;  // fib cleared separately
      if (StringFind(name, "BIAS") >= 0)     continue;  // singleton label
      int us = StringLen(name);
      while (us > 0 && StringGetCharacter(name, us - 1) != '_') us--;
      if (us <= 0) continue;
      long t = StringToInteger(StringSubstr(name, us));
      if (t > 0 && (datetime)t < cutoff)
         ObjectDelete(0, name);
   }
}

//+==================================================================+
//|  TRADING LAYER (Phase 2)                                         |
//+==================================================================+

//+------------------------------------------------------------------+
//| Build a trade setup from the most recent LTF MSS                 |
//+------------------------------------------------------------------+
void BuildSetupFromMSS(StructureState &st) {
   bool     bull    = st.lastBreakBullish;
   datetime mssTime = st.lastMSSTime;
   if (mssTime == 0)                 return;
   if (mssTime == g_setup.mssTime)   return;   // already built for this MSS

   //--- only trade MSS aligned with HTF bias
   if (g_cfg_RequireBiasAlign) {
      if (bull && g_htf.bias != TREND_BULL) {
         if (g_cfg_VerboseLog) Print("[IctSmc] MSS BULL skipped — HTF bias not bullish");
         return;
      }
      if (!bull && g_htf.bias != TREND_BEAR) {
         if (g_cfg_VerboseLog) Print("[IctSmc] MSS BEAR skipped — HTF bias not bearish");
         return;
      }
   }

   //--- impulse leg (same as the OTE fib leg)
   SwingPoint legStart, legEnd;
   if (bull) {
      if (!LastSwingOf(st, SWING_HIGH, legEnd))                  return;
      if (!LastSwingBefore(st, SWING_LOW, legEnd.time, legStart)) return;
   } else {
      if (!LastSwingOf(st, SWING_LOW, legEnd))                    return;
      if (!LastSwingBefore(st, SWING_HIGH, legEnd.time, legStart))return;
   }
   double range = legEnd.price - legStart.price;
   if (MathAbs(range) < _Point) return;

   TradeSetup s;
   ZeroMemory(s);
   s.bullish = bull;
   s.mssTime = mssTime;

   //--- 3 laddered OTE entries
   double f[3];
   f[0] = g_cfg_EntryFib1; f[1] = g_cfg_EntryFib2; f[2] = g_cfg_EntryFib3;
   for (int i = 0; i < 3; i++)
      s.entry[i] = NormalizeDouble(legEnd.price - f[i] * range, _Digits);

   //--- SL beyond the protected swing (leg origin) + buffer
   double buf = g_cfg_SlBufferPts * _Point;
   s.sl = bull ? NormalizeDouble(legStart.price - buf, _Digits)
               : NormalizeDouble(legStart.price + buf, _Digits);

   double entryAvg = (s.entry[0] + s.entry[1] + s.entry[2]) / 3.0;
   double stopDist = MathAbs(entryAvg - s.sl);
   if (stopDist < g_cfg_MinStopPts * _Point) {
      if (g_cfg_VerboseLog)
         PrintFormat("[IctSmc] Setup skipped — SL distance %.0f pts < min %.0f",
                     stopDist / _Point, g_cfg_MinStopPts);
      return;
   }

   //--- TP ladder: successive opposing-liquidity levels beyond the leg end
   //--- tier 1 (shallow entry) → nearest liquidity ... tier 3 (deep entry) → farthest
   double lv[];
   int n = CollectLiquidity(st, bull, legEnd.price, lv);
   for (int i = 0; i < 3; i++) {
      double tpi;
      if (!g_cfg_PerTierTP) {
         tpi = (n > 0) ? lv[0]
                       : (bull ? entryAvg + g_cfg_FallbackRR * stopDist
                               : entryAvg - g_cfg_FallbackRR * stopDist);
      } else if (n > 0) {
         tpi = lv[MathMin(i, n - 1)];           // missing tiers reuse the farthest level
      } else {
         tpi = bull ? entryAvg + (g_cfg_FallbackRR + i) * stopDist
                    : entryAvg - (g_cfg_FallbackRR + i) * stopDist;  // RR ladder 2R/3R/4R
      }
      s.tp[i] = NormalizeDouble(tpi, _Digits);
      //--- ensure TP sits the correct side of this tier's entry; else fallback RR ladder
      if (bull  && s.tp[i] <= s.entry[i])
         s.tp[i] = NormalizeDouble(s.entry[i] + (g_cfg_FallbackRR + i) * stopDist, _Digits);
      if (!bull && s.tp[i] >= s.entry[i])
         s.tp[i] = NormalizeDouble(s.entry[i] - (g_cfg_FallbackRR + i) * stopDist, _Digits);
      s.rr[i] = bull ? (s.tp[i] - s.entry[i]) / MathAbs(s.entry[i] - s.sl)
                     : (s.entry[i] - s.tp[i]) / MathAbs(s.entry[i] - s.sl);
   }

   s.expiry = TimeCurrent() + (datetime)(g_cfg_PendingExpiryBars * PeriodSeconds(g_cfg_LTF));
   s.active = true;

   //--- a new setup supersedes the old one: drop stale unfilled pendings
   CancelAllPendings();
   g_setup = s;

   DrawSetup(g_setup);
   PrintFormat("[IctSmc] Setup %s @MSS %s  E[%.5f/%.5f/%.5f] SL %.5f TP[%.5f/%.5f/%.5f] RR[%.2f/%.2f/%.2f]",
               bull ? "BULL" : "BEAR", TimeToString(mssTime),
               s.entry[0], s.entry[1], s.entry[2], s.sl,
               s.tp[0], s.tp[1], s.tp[2], s.rr[0], s.rr[1], s.rr[2]);

   if (g_cfg_EnableTrading && g_cfg_Enabled)
      PlaceSetupOrders(g_setup);
   else if (g_cfg_VerboseLog)
      Print("[IctSmc] Trading OFF — setup drawn only, no orders placed.");
}

//+------------------------------------------------------------------+
//| Collect opposing-liquidity levels beyond the leg end, sorted by  |
//| distance (nearest first), deduped by >= InpMinStopPts spacing.   |
//| bull → swing-highs above legEnd; bear → swing-lows below legEnd.  |
//+------------------------------------------------------------------+
int CollectLiquidity(StructureState &st, bool bull, double legEndPrice, double &out[]) {
   double cand[];
   ArrayResize(cand, 0);
   for (int i = 0; i < ArraySize(st.swings); i++) {
      SwingPoint sp = st.swings[i];
      if (bull  && sp.type == SWING_HIGH && sp.price > legEndPrice) {
         int k = ArraySize(cand); ArrayResize(cand, k + 1); cand[k] = sp.price;
      }
      if (!bull && sp.type == SWING_LOW && sp.price < legEndPrice) {
         int k = ArraySize(cand); ArrayResize(cand, k + 1); cand[k] = sp.price;
      }
   }
   int m = ArraySize(cand);
   if (m == 0) { ArrayResize(out, 0); return 0; }

   //--- insertion sort by distance from legEnd (nearest first)
   for (int i = 1; i < m; i++) {
      double v = cand[i];
      int j = i - 1;
      //--- bull: nearer = smaller price; bear: nearer = larger price
      while (j >= 0 && ((bull && cand[j] > v) || (!bull && cand[j] < v))) {
         cand[j + 1] = cand[j];
         j--;
      }
      cand[j + 1] = v;
   }

   //--- dedup levels closer than the min-stop spacing
   double minGap = g_cfg_MinStopPts * _Point;
   ArrayResize(out, 0);
   for (int i = 0; i < m; i++) {
      int o = ArraySize(out);
      if (o > 0 && MathAbs(cand[i] - out[o - 1]) < minGap) continue;
      ArrayResize(out, o + 1);
      out[o] = cand[i];
   }
   return ArraySize(out);
}

//+------------------------------------------------------------------+
//| Place the 3 laddered limit orders (guards + dedup)               |
//+------------------------------------------------------------------+
void PlaceSetupOrders(TradeSetup &s) {
   if (!IsSpreadOK("Entry")) return;

   ENUM_POSITION_TYPE dir = s.bullish ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
   double lot     = NormalizeLot(g_cfg_LotPerEntry);
   double minDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   double ask     = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid     = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   string tsStr   = IntegerToString((long)s.mssTime);

   for (int i = 0; i < 3; i++) {
      if (CountOpenPositions(dir) >= g_cfg_MaxSetupPositions) {
         PrintFormat("[IctSmc] E%d skipped — max %d positions/pendings reached", i + 1, g_cfg_MaxSetupPositions);
         break;
      }
      if (SumOpenLots(dir) + lot > g_cfg_MaxTotalLots + 1e-8) {
         PrintFormat("[IctSmc] E%d skipped — total lots cap %.2f reached", i + 1, g_cfg_MaxTotalLots);
         break;
      }
      string prefix = StringFormat("ICT_E%d_%s_", i + 1, tsStr);
      if (TradeExistsByCommentPrefix(prefix)) continue;

      double price = s.entry[i];
      double sl    = s.sl;
      double tp    = s.tp[i];
      //--- keep SL/TP at least the broker min distance from this entry
      if (s.bullish) {
         if (price - sl < minDist) sl = price - minDist;
         if (tp - price < minDist) tp = price + minDist;
      } else {
         if (sl - price < minDist) sl = price + minDist;
         if (price - tp < minDist) tp = price - minDist;
      }
      sl = NormalizeDouble(sl, _Digits);
      tp = NormalizeDouble(tp, _Digits);

      string comment = prefix + (s.bullish ? "B" : "S");
      bool ok = false;
      if (s.bullish) {
         if (price >= ask) { PrintFormat("[IctSmc] E%d skipped — price already at/through %.5f", i + 1, price); continue; }
         ok = g_trade.BuyLimit(lot, price, _Symbol, sl, tp, ORDER_TIME_SPECIFIED, s.expiry, comment);
      } else {
         if (price <= bid) { PrintFormat("[IctSmc] E%d skipped — price already at/through %.5f", i + 1, price); continue; }
         ok = g_trade.SellLimit(lot, price, _Symbol, sl, tp, ORDER_TIME_SPECIFIED, s.expiry, comment);
      }
      PrintFormat("[IctSmc] %s E%d limit %.5f SL %.5f TP %.5f lot %.2f  %s",
                  s.bullish ? "BUY" : "SELL", i + 1, price, sl, tp, lot,
                  ok ? "OK" : "FAILED: " + g_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Cancel unfilled limits when the setup expires                    |
//+------------------------------------------------------------------+
void ManagePendings() {
   if (!g_setup.active) return;
   if (TimeCurrent() <= g_setup.expiry) return;
   int n = CancelAllPendings();
   if (n > 0 && g_cfg_VerboseLog)
      PrintFormat("[IctSmc] Expired setup — cancelled %d unfilled limit(s)", n);
   g_setup.active = false;
}

//+------------------------------------------------------------------+
//| Delete all this EA's pending orders (magic + symbol)             |
//+------------------------------------------------------------------+
int CancelAllPendings() {
   int cnt = 0;
   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong t = OrderGetTicket(i);
      if (t == 0) continue;
      if (OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
      if (OrderGetInteger(ORDER_MAGIC) != (long)g_cfg_Magic) continue;
      if (g_trade.OrderDelete(t)) cnt++;
   }
   return cnt;
}

//+------------------------------------------------------------------+
//| Manage filled positions: partial close, break-even, trailing     |
//| Stateless (derived from each position) → restart-safe.           |
//+------------------------------------------------------------------+
void ManageOpenPositions() {
   if (!g_cfg_EnableBreakEven && !g_cfg_EnableTrailing && !g_cfg_EnablePartialClose) return;

   double bid    = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask    = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double lotFull = NormalizeLot(g_cfg_LotPerEntry);

   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket)) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)          continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)g_cfg_Magic) continue;

      ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      bool   isBuy     = (type == POSITION_TYPE_BUY);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL     = PositionGetDouble(POSITION_SL);
      double tp        = PositionGetDouble(POSITION_TP);
      double vol       = PositionGetDouble(POSITION_VOLUME);
      double profitPts = (isBuy ? (bid - openPrice) : (openPrice - ask)) / _Point;

      //--- partial profit (once: only while still full size)
      if (g_cfg_EnablePartialClose && profitPts >= g_cfg_PartialClosePts
          && vol >= lotFull - 1e-8) {
         double closeVol = NormalizeLot(vol * g_cfg_PartialClosePct);
         if (closeVol >= minLot && (vol - closeVol) >= minLot) {
            bool okp = g_trade.PositionClosePartial(ticket, closeVol);
            PrintFormat("[IctSmc] Partial close #%I64u %.2f/%.2f @ +%.0f pts  %s",
                        ticket, closeVol, vol, profitPts,
                        okp ? "OK" : "FAILED: " + g_trade.ResultRetcodeDescription());
         }
      }

      //--- desired SL: trailing takes priority over break-even
      double desiredSL = curSL;
      string stage = "";
      if (g_cfg_EnableTrailing && profitPts >= g_cfg_TrailStartPts) {
         desiredSL = isBuy ? (bid - g_cfg_TrailDistPts * _Point)
                           : (ask + g_cfg_TrailDistPts * _Point);
         stage = "Trail";
      } else if (g_cfg_EnableBreakEven && profitPts >= g_cfg_BeTriggerPts) {
         desiredSL = isBuy ? (openPrice + g_cfg_BeOffsetPts * _Point)
                           : (openPrice - g_cfg_BeOffsetPts * _Point);
         stage = "BE";
      }
      if (stage == "") continue;
      desiredSL = NormalizeDouble(desiredSL, _Digits);

      //--- only move if strictly improving by at least the step
      if (isBuy) {
         if (desiredSL < curSL + g_cfg_TrailStepPts * _Point) continue;
      } else {
         if (curSL != 0.0 && desiredSL > curSL - g_cfg_TrailStepPts * _Point) continue;
      }
      //--- never cross TP
      if (tp > 0.0) {
         if (isBuy  && desiredSL >= tp) continue;
         if (!isBuy && desiredSL <= tp) continue;
      }

      bool ok = g_trade.PositionModify(ticket, desiredSL, tp);
      PrintFormat("[IctSmc][%s] #%I64u SL %.5f -> %.5f (+%.0f pts)  %s",
                  stage, ticket, curSL, desiredSL, profitPts,
                  ok ? "OK" : "FAILED: " + g_trade.ResultRetcodeDescription());
   }
}

//+------------------------------------------------------------------+
//| Draw the entry/SL/TP levels of the active setup                  |
//+------------------------------------------------------------------+
void ClearTradeObjects() { DeleteByPrefix(OBJ_PREFIX + "TRADE_"); }

void DrawSetup(TradeSetup &s) {
   ClearTradeObjects();
   if (!s.active) return;
   datetime t1 = s.mssTime;
   datetime t2 = s.expiry;
   for (int i = 0; i < 3; i++) {
      SetTrend(OBJ_PREFIX + "TRADE_E" + IntegerToString(i + 1), t1, s.entry[i], t2, s.entry[i],
               g_cfg_ColEntry, STYLE_DOT, 1);
      SetText(OBJ_PREFIX + "TRADE_ET" + IntegerToString(i + 1), t2, s.entry[i],
              "E" + IntegerToString(i + 1), g_cfg_ColEntry, ANCHOR_LEFT, 8);
   }
   SetTrend(OBJ_PREFIX + "TRADE_SL", t1, s.sl, t2, s.sl, g_cfg_ColSL, STYLE_SOLID, 1);
   SetText(OBJ_PREFIX + "TRADE_SLT", t2, s.sl, "SL", g_cfg_ColSL, ANCHOR_LEFT, 8);
   for (int i = 0; i < 3; i++) {
      SetTrend(OBJ_PREFIX + "TRADE_TP" + IntegerToString(i + 1), t1, s.tp[i], t2, s.tp[i],
               g_cfg_ColTP, STYLE_SOLID, 1);
      SetText(OBJ_PREFIX + "TRADE_TPT" + IntegerToString(i + 1), t2, s.tp[i],
              StringFormat("TP%d RR=%.2f", i + 1, s.rr[i]), g_cfg_ColTP, ANCHOR_LEFT, 8);
   }
}

//+------------------------------------------------------------------+
//| Order / position helpers (reused patterns from CondeAutoEntryEA) |
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

bool IsSpreadOK(const string tag) {
   if (g_cfg_MaxSpreadPts <= 0) return true;
   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if (spread > g_cfg_MaxSpreadPts) {
      PrintFormat("[IctSmc][%s SKIP] Spread %d pts > max %d pts", tag, (int)spread, (int)g_cfg_MaxSpreadPts);
      return false;
   }
   return true;
}

bool IsPendingForDir(const long otype, const ENUM_POSITION_TYPE dir) {
   if (dir == POSITION_TYPE_BUY)
      return (otype == ORDER_TYPE_BUY_LIMIT || otype == ORDER_TYPE_BUY_STOP);
   return (otype == ORDER_TYPE_SELL_LIMIT || otype == ORDER_TYPE_SELL_STOP);
}

int CountOpenPositions(const ENUM_POSITION_TYPE dir) {
   int count = 0;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket)) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)          continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)g_cfg_Magic) continue;
      if (PositionGetInteger(POSITION_TYPE)  == (long)dir) count++;
   }
   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong ticket = OrderGetTicket(i);
      if (ticket == 0) continue;
      if (OrderGetString(ORDER_SYMBOL) != _Symbol)          continue;
      if (OrderGetInteger(ORDER_MAGIC) != (long)g_cfg_Magic) continue;
      if (IsPendingForDir(OrderGetInteger(ORDER_TYPE), dir)) count++;
   }
   return count;
}

double SumOpenLots(const ENUM_POSITION_TYPE dir) {
   double total = 0.0;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket)) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)          continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)g_cfg_Magic) continue;
      if (PositionGetInteger(POSITION_TYPE)  == (long)dir)
         total += PositionGetDouble(POSITION_VOLUME);
   }
   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong ticket = OrderGetTicket(i);
      if (ticket == 0) continue;
      if (OrderGetString(ORDER_SYMBOL) != _Symbol)          continue;
      if (OrderGetInteger(ORDER_MAGIC) != (long)g_cfg_Magic) continue;
      if (IsPendingForDir(OrderGetInteger(ORDER_TYPE), dir))
         total += OrderGetDouble(ORDER_VOLUME_CURRENT);
   }
   return total;
}

//+------------------------------------------------------------------+
//| Restart-safe dedup: open + pending + recent history by comment   |
//+------------------------------------------------------------------+
bool TradeExistsByCommentPrefix(const string prefix) {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (!PositionSelectByTicket(ticket)) continue;
      if (PositionGetString(POSITION_SYMBOL) != _Symbol)          continue;
      if (PositionGetInteger(POSITION_MAGIC) != (long)g_cfg_Magic) continue;
      if (StringFind(PositionGetString(POSITION_COMMENT), prefix) == 0) return true;
   }
   for (int i = OrdersTotal() - 1; i >= 0; i--) {
      ulong ticket = OrderGetTicket(i);
      if (ticket == 0) continue;
      if (OrderGetString(ORDER_SYMBOL) != _Symbol)          continue;
      if (OrderGetInteger(ORDER_MAGIC) != (long)g_cfg_Magic) continue;
      if (StringFind(OrderGetString(ORDER_COMMENT), prefix) == 0) return true;
   }
   datetime from = TimeCurrent() - (datetime)(3 * 86400);
   if (HistorySelect(from, TimeCurrent() + 60)) {
      int deals = HistoryDealsTotal();
      for (int i = deals - 1; i >= 0; i--) {
         ulong deal = HistoryDealGetTicket(i);
         if (deal == 0) continue;
         if (HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol)          continue;
         if (HistoryDealGetInteger(deal, DEAL_MAGIC) != (long)g_cfg_Magic) continue;
         if (HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_IN)     continue;
         if (StringFind(HistoryDealGetString(deal, DEAL_COMMENT), prefix) == 0) return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Outcome capture                                                  |
//+------------------------------------------------------------------+
void EnsureOutcomesDir() {
   FolderCreate("IctSmcEA\\outcomes");  // no-op if it already exists
}

long ParseTsFromComment(const string c) {
   string p[];
   int n = StringSplit(c, '_', p);   // ICT_E1_<ts>_B
   if (n >= 3 && p[0] == "ICT") return StringToInteger(p[2]);
   return 0;
}

int ParseTierFromComment(const string c) {
   string p[];
   int n = StringSplit(c, '_', p);
   if (n >= 2 && StringLen(p[1]) >= 2) return (int)StringToInteger(StringSubstr(p[1], 1));
   return 0;
}

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &req,
                        const MqlTradeResult &res) {
   if (trans.type != TRADE_TRANSACTION_DEAL_ADD) return;
   ulong deal = trans.deal;
   if (deal == 0) return;
   if (!HistoryDealSelect(deal)) return;
   if ((ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_OUT) return;
   if (HistoryDealGetString(deal, DEAL_SYMBOL) != _Symbol) return;

   long position_id = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   if (position_id == 0) return;

   string out_comment = HistoryDealGetString(deal, DEAL_COMMENT);
   long   signal_ts   = ParseTsFromComment(out_comment);
   int    entry_tier  = ParseTierFromComment(out_comment);
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
         direction   = (HistoryDealGetInteger(d, DEAL_TYPE) == DEAL_TYPE_BUY) ? "BUY" : "SELL";
         string in_comment = HistoryDealGetString(d, DEAL_COMMENT);
         if (signal_ts  == 0) signal_ts  = ParseTsFromComment(in_comment);
         if (entry_tier == 0) entry_tier = ParseTierFromComment(in_comment);
         break;
      }
   }
   if (in_magic != (long)g_cfg_Magic) return;

   double exit_price  = HistoryDealGetDouble(deal, DEAL_PRICE);
   double profit      = HistoryDealGetDouble(deal, DEAL_PROFIT);
   double swap        = HistoryDealGetDouble(deal, DEAL_SWAP);
   double commission  = HistoryDealGetDouble(deal, DEAL_COMMISSION);
   double volume      = HistoryDealGetDouble(deal, DEAL_VOLUME);
   long   closed_at   = (long)HistoryDealGetInteger(deal, DEAL_TIME);
   long   reason_int  = HistoryDealGetInteger(deal, DEAL_REASON);
   string close_reason = (reason_int == DEAL_REASON_TP)     ? "TP"
                       : (reason_int == DEAL_REASON_SL)     ? "SL"
                       : (reason_int == DEAL_REASON_EXPERT) ? "EXPERT" : "OTHER";

   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   string json = "{";
   json += "\"position_id\":"     + IntegerToString(position_id) + ",";
   json += "\"deal_out_ticket\":" + IntegerToString((long)deal) + ",";
   json += "\"signal_ts\":"       + IntegerToString((long)signal_ts) + ",";
   json += "\"entry_tier\":"      + IntegerToString(entry_tier) + ",";
   json += "\"comment\":\""       + out_comment + "\",";
   json += "\"account\":"         + IntegerToString((long)AccountInfoInteger(ACCOUNT_LOGIN)) + ",";
   json += "\"symbol\":\""        + _Symbol + "\",";
   json += "\"direction\":\""     + direction + "\",";
   json += "\"magic\":"           + IntegerToString((long)g_cfg_Magic) + ",";
   json += "\"volume\":"          + DoubleToString(volume, 2) + ",";
   json += "\"entry_price\":"     + DoubleToString(entry_price, digits) + ",";
   json += "\"exit_price\":"      + DoubleToString(exit_price, digits) + ",";
   json += "\"profit\":"          + DoubleToString(profit, 2) + ",";
   json += "\"swap\":"            + DoubleToString(swap, 2) + ",";
   json += "\"commission\":"      + DoubleToString(commission, 2) + ",";
   json += "\"opened_at\":"       + IntegerToString(opened_at) + ",";
   json += "\"closed_at\":"       + IntegerToString(closed_at) + ",";
   json += "\"close_reason\":\""  + close_reason + "\"";
   json += "}";

   string path = "IctSmcEA\\outcomes\\" + IntegerToString(position_id) + ".json";
   int h = FileOpen(path, FILE_WRITE | FILE_TXT | FILE_ANSI);
   if (h == INVALID_HANDLE) {
      PrintFormat("[IctSmc][Outcome] Cannot write '%s' (err %d)", path, GetLastError());
      return;
   }
   FileWriteString(h, json);
   FileClose(h);
   PrintFormat("[IctSmc][Outcome] saved pos=%s tier=%d reason=%s profit=%.2f",
               IntegerToString(position_id), entry_tier, close_reason, profit);
}

//+==================================================================+
//|  HELPERS                                                         |
//+==================================================================+
string TfStr(ENUM_TIMEFRAMES tf) {
   string s = EnumToString(tf);          // e.g. "PERIOD_H4"
   int p = StringFind(s, "PERIOD_");
   if (p == 0) return StringSubstr(s, 7);
   return s;
}

string BiasStr(TrendDir b) {
   if (b == TREND_BULL) return "BULL";
   if (b == TREND_BEAR) return "BEAR";
   return "NONE";
}

//+------------------------------------------------------------------+
//| Read a whole file into a string                                  |
//+------------------------------------------------------------------+
string ReadFileToString(const string filename) {
   int h = FileOpen(filename, FILE_READ | FILE_TXT | FILE_ANSI);
   if (h == INVALID_HANDLE) {
      PrintFormat("[IctSmc][File] Cannot open '%s' (err %d)", filename, GetLastError());
      return "";
   }
   string result = "";
   while (!FileIsEnding(h))
      result += FileReadString(h);
   FileClose(h);
   return result;
}

//+------------------------------------------------------------------+
//| Hand-rolled JSON scalar parsing (no MQL5 JSON library)           |
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

bool JsonGetBool(const string &json, const string key, bool defval) {
   string v = JsonGetString(json, key);
   if (v == "") return defval;
   if (v == "true" || v == "1") return true;
   if (v == "false" || v == "0") return false;
   return defval;
}

double JsonGetDouble(const string &json, const string key, double defval) {
   string v = JsonGetString(json, key);
   if (v == "") return defval;
   return StringToDouble(v);
}

long JsonGetLong(const string &json, const string key, long defval) {
   string v = JsonGetString(json, key);
   if (v == "") return defval;
   return StringToInteger(v);
}

//+------------------------------------------------------------------+
//| Copy Inp* inputs into mutable g_cfg_* shadows                    |
//+------------------------------------------------------------------+
void InitShadowsFromInputs() {
   g_cfg_HTF                = InpHTF;
   g_cfg_LTF                = InpLTF;
   g_cfg_SwingLookback      = InpSwingLookback;
   g_cfg_MaxHistoryBars     = InpMaxHistoryBars;
   g_cfg_MaxSwingsTracked   = InpMaxSwingsTracked;
   g_cfg_BiasSwingsForTrend = InpBiasSwingsForTrend;
   g_cfg_DrawFib            = InpDrawFib;
   g_cfg_DrawEquilibrium    = InpDrawEquilibrium;
   g_cfg_FibOTE1            = InpFibOTE1;
   g_cfg_FibOTE2            = InpFibOTE2;
   g_cfg_FibOTE3            = InpFibOTE3;
   g_cfg_ShowHTFObjects     = InpShowHTFObjects;
   g_cfg_ColSwingHigh       = InpColSwingHigh;
   g_cfg_ColSwingLow        = InpColSwingLow;
   g_cfg_ColBOS             = InpColBOS;
   g_cfg_ColMSS             = InpColMSS;
   g_cfg_ColFib             = InpColFib;
   g_cfg_ColBiasBull        = InpColBiasBull;
   g_cfg_ColBiasBear        = InpColBiasBear;
   g_cfg_EnableTrading      = InpEnableTrading;
   g_cfg_EntryFib1          = InpEntryFib1;
   g_cfg_EntryFib2          = InpEntryFib2;
   g_cfg_EntryFib3          = InpEntryFib3;
   g_cfg_LotPerEntry        = InpLotPerEntry;
   g_cfg_MaxSetupPositions  = InpMaxSetupPositions;
   g_cfg_MaxTotalLots       = InpMaxTotalLots;
   g_cfg_SlBufferPts        = InpSlBufferPts;
   g_cfg_PendingExpiryBars  = InpPendingExpiryBars;
   g_cfg_MinStopPts         = InpMinStopPts;
   g_cfg_FallbackRR         = InpFallbackRR;
   g_cfg_PerTierTP          = InpPerTierTP;
   g_cfg_RequireBiasAlign   = InpRequireBiasAlign;
   g_cfg_MaxSpreadPts       = InpMaxSpreadPts;
   g_cfg_ColEntry           = InpColEntry;
   g_cfg_ColSL              = InpColSL;
   g_cfg_ColTP              = InpColTP;
   g_cfg_EnableBreakEven    = InpEnableBreakEven;
   g_cfg_BeTriggerPts       = InpBeTriggerPts;
   g_cfg_BeOffsetPts        = InpBeOffsetPts;
   g_cfg_EnableTrailing     = InpEnableTrailing;
   g_cfg_TrailStartPts      = InpTrailStartPts;
   g_cfg_TrailDistPts       = InpTrailDistPts;
   g_cfg_TrailStepPts       = InpTrailStepPts;
   g_cfg_EnablePartialClose = InpEnablePartialClose;
   g_cfg_PartialClosePts    = InpPartialClosePts;
   g_cfg_PartialClosePct    = InpPartialClosePct;
   g_cfg_Magic              = InpMagic;
   g_cfg_VerboseLog         = InpVerboseLog;
   g_cfg_Enabled            = true;
}

//+------------------------------------------------------------------+
//| Load per-account JSON config and overlay g_cfg_* shadows         |
//+------------------------------------------------------------------+
void LoadAccountConfig() {
   string path = "IctSmcEA\\config\\" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ".json";
   string json = ReadFileToString(path);
   if (json == "") {
      Print("[IctSmc][Config] no per-account config at ", path, " — using EA inputs");
      return;
   }

   // meta block
   g_cfg_Enabled            = JsonGetBool(json,   "enabled", g_cfg_Enabled);
   string label             = JsonGetString(json, "label");
   string owner             = JsonGetString(json, "owner");

   // inputs block (sparse merge — missing keys keep the shadow default)
   g_cfg_HTF                = (ENUM_TIMEFRAMES)JsonGetLong(json, "InpHTF", (long)g_cfg_HTF);
   g_cfg_LTF                = (ENUM_TIMEFRAMES)JsonGetLong(json, "InpLTF", (long)g_cfg_LTF);
   g_cfg_SwingLookback      = (int)JsonGetLong(json, "InpSwingLookback",      (long)g_cfg_SwingLookback);
   g_cfg_MaxHistoryBars     = (int)JsonGetLong(json, "InpMaxHistoryBars",     (long)g_cfg_MaxHistoryBars);
   g_cfg_MaxSwingsTracked   = (int)JsonGetLong(json, "InpMaxSwingsTracked",   (long)g_cfg_MaxSwingsTracked);
   g_cfg_BiasSwingsForTrend = (int)JsonGetLong(json, "InpBiasSwingsForTrend", (long)g_cfg_BiasSwingsForTrend);
   g_cfg_DrawFib            = JsonGetBool(json,   "InpDrawFib",         g_cfg_DrawFib);
   g_cfg_DrawEquilibrium    = JsonGetBool(json,   "InpDrawEquilibrium", g_cfg_DrawEquilibrium);
   g_cfg_FibOTE1            = JsonGetDouble(json, "InpFibOTE1",         g_cfg_FibOTE1);
   g_cfg_FibOTE2            = JsonGetDouble(json, "InpFibOTE2",         g_cfg_FibOTE2);
   g_cfg_FibOTE3            = JsonGetDouble(json, "InpFibOTE3",         g_cfg_FibOTE3);
   g_cfg_ShowHTFObjects     = JsonGetBool(json,   "InpShowHTFObjects",  g_cfg_ShowHTFObjects);
   g_cfg_ColSwingHigh       = (color)JsonGetLong(json, "InpColSwingHigh", (long)g_cfg_ColSwingHigh);
   g_cfg_ColSwingLow        = (color)JsonGetLong(json, "InpColSwingLow",  (long)g_cfg_ColSwingLow);
   g_cfg_ColBOS             = (color)JsonGetLong(json, "InpColBOS",       (long)g_cfg_ColBOS);
   g_cfg_ColMSS             = (color)JsonGetLong(json, "InpColMSS",       (long)g_cfg_ColMSS);
   g_cfg_ColFib             = (color)JsonGetLong(json, "InpColFib",       (long)g_cfg_ColFib);
   g_cfg_ColBiasBull        = (color)JsonGetLong(json, "InpColBiasBull",  (long)g_cfg_ColBiasBull);
   g_cfg_ColBiasBear        = (color)JsonGetLong(json, "InpColBiasBear",  (long)g_cfg_ColBiasBear);
   g_cfg_EnableTrading      = JsonGetBool(json,   "InpEnableTrading",     g_cfg_EnableTrading);
   g_cfg_EntryFib1          = JsonGetDouble(json, "InpEntryFib1",         g_cfg_EntryFib1);
   g_cfg_EntryFib2          = JsonGetDouble(json, "InpEntryFib2",         g_cfg_EntryFib2);
   g_cfg_EntryFib3          = JsonGetDouble(json, "InpEntryFib3",         g_cfg_EntryFib3);
   g_cfg_LotPerEntry        = JsonGetDouble(json, "InpLotPerEntry",       g_cfg_LotPerEntry);
   g_cfg_MaxSetupPositions  = (int)JsonGetLong(json, "InpMaxSetupPositions", (long)g_cfg_MaxSetupPositions);
   g_cfg_MaxTotalLots       = JsonGetDouble(json, "InpMaxTotalLots",      g_cfg_MaxTotalLots);
   g_cfg_SlBufferPts        = JsonGetDouble(json, "InpSlBufferPts",       g_cfg_SlBufferPts);
   g_cfg_PendingExpiryBars  = (int)JsonGetLong(json, "InpPendingExpiryBars", (long)g_cfg_PendingExpiryBars);
   g_cfg_MinStopPts         = JsonGetDouble(json, "InpMinStopPts",        g_cfg_MinStopPts);
   g_cfg_FallbackRR         = JsonGetDouble(json, "InpFallbackRR",        g_cfg_FallbackRR);
   g_cfg_PerTierTP          = JsonGetBool(json,   "InpPerTierTP",         g_cfg_PerTierTP);
   g_cfg_RequireBiasAlign   = JsonGetBool(json,   "InpRequireBiasAlign",  g_cfg_RequireBiasAlign);
   g_cfg_MaxSpreadPts       = JsonGetLong(json,   "InpMaxSpreadPts",      g_cfg_MaxSpreadPts);
   g_cfg_ColEntry           = (color)JsonGetLong(json, "InpColEntry",     (long)g_cfg_ColEntry);
   g_cfg_ColSL              = (color)JsonGetLong(json, "InpColSL",        (long)g_cfg_ColSL);
   g_cfg_ColTP              = (color)JsonGetLong(json, "InpColTP",        (long)g_cfg_ColTP);
   g_cfg_EnableBreakEven    = JsonGetBool(json,   "InpEnableBreakEven",   g_cfg_EnableBreakEven);
   g_cfg_BeTriggerPts       = JsonGetDouble(json, "InpBeTriggerPts",      g_cfg_BeTriggerPts);
   g_cfg_BeOffsetPts        = JsonGetDouble(json, "InpBeOffsetPts",       g_cfg_BeOffsetPts);
   g_cfg_EnableTrailing     = JsonGetBool(json,   "InpEnableTrailing",    g_cfg_EnableTrailing);
   g_cfg_TrailStartPts      = JsonGetDouble(json, "InpTrailStartPts",     g_cfg_TrailStartPts);
   g_cfg_TrailDistPts       = JsonGetDouble(json, "InpTrailDistPts",      g_cfg_TrailDistPts);
   g_cfg_TrailStepPts       = JsonGetDouble(json, "InpTrailStepPts",      g_cfg_TrailStepPts);
   g_cfg_EnablePartialClose = JsonGetBool(json,   "InpEnablePartialClose", g_cfg_EnablePartialClose);
   g_cfg_PartialClosePts    = JsonGetDouble(json, "InpPartialClosePts",   g_cfg_PartialClosePts);
   g_cfg_PartialClosePct    = JsonGetDouble(json, "InpPartialClosePct",   g_cfg_PartialClosePct);
   g_cfg_Magic              = (ulong)JsonGetLong(json, "InpMagic",        (long)g_cfg_Magic);
   g_cfg_VerboseLog         = JsonGetBool(json,   "InpVerboseLog",  g_cfg_VerboseLog);

   PrintFormat("[IctSmc][Config] loaded %s — label='%s' owner='%s' enabled=%s magic=%I64u",
               path, label, owner, (g_cfg_Enabled ? "true" : "false"), g_cfg_Magic);
}
//+------------------------------------------------------------------+
