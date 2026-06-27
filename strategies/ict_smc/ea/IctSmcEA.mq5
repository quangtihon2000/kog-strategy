//+------------------------------------------------------------------+
//|  IctSmcEA.mq5                                                     |
//|  ICT / Smart-Money-Concepts market-structure DETECTION EA        |
//|  Phase 1: detect + draw swings, BOS, MSS (CHoCH), HTF bias, Fibo  |
//|  NO trading yet — visualization + Print logs only.               |
//|  CI/CD deployed                                                  |
//+------------------------------------------------------------------+
#property copyright   "IctSmc EA"
#property version     "1.00"
#property description "Phase 1 — ICT structure detection: swings, BOS, MSS, HTF bias, OTE fib (no orders)"

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
//--- Identity / Phase-2 scaffolding
input ulong  InpMagic               = 20260627;     // Magic (reserved for Phase 2 orders)
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

//--- Globals
CTrade     g_trade;
StructureState g_htf;
StructureState g_ltf;
datetime   g_lastHTFBar = 0;
datetime   g_lastLTFBar = 0;

#define OBJ_PREFIX "IctSmc_"

//+------------------------------------------------------------------+
int OnInit() {
   InitShadowsFromInputs();
   LoadAccountConfig();

   g_trade.SetExpertMagicNumber(g_cfg_Magic);  // reserved for Phase 2

   g_htf.tf  = g_cfg_HTF;
   g_ltf.tf  = g_cfg_LTF;
   g_htf.bias = TREND_NONE;
   g_ltf.bias = TREND_NONE;
   ZeroBreaks(g_htf);
   ZeroBreaks(g_ltf);

   ObjectsDeleteAll(0, OBJ_PREFIX);

   if (!g_cfg_Enabled)
      Print("[IctSmc][Config] DISABLED — Phase 1 still draws (read-only); Phase 2 would gate entries.");

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
   g_cfg_Magic              = (ulong)JsonGetLong(json, "InpMagic",        (long)g_cfg_Magic);
   g_cfg_VerboseLog         = JsonGetBool(json,   "InpVerboseLog",  g_cfg_VerboseLog);

   PrintFormat("[IctSmc][Config] loaded %s — label='%s' owner='%s' enabled=%s magic=%I64u",
               path, label, owner, (g_cfg_Enabled ? "true" : "false"), g_cfg_Magic);
}
//+------------------------------------------------------------------+
