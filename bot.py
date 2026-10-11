
#property strict
#property version   "1.00"
#property description "Forex Intelligence EA - Risk Managed Starter"

#include <Trade/Trade.mqh>
CTrade trade;

// ===== SETTINGS =====
input ulong  MagicNumber            = 20261011;
input double RiskPercent            = 1.0;
input double DailyLossLimitPercent  = 3.0;
input int    MaxTradesPerDay        = 3;

input int    FastEMA                = 20;
input int    SlowEMA                = 50;
input int    RSIPeriod              = 14;
input int    ATRPeriod              = 14;

input double BuyRSIMin              = 50.0;
input double BuyRSIMax              = 70.0;
input double SellRSIMin             = 30.0;
input double SellRSIMax             = 50.0;

input double SL_ATR_Multiplier      = 2.0;
input double TP_RiskReward          = 2.0;
input int    MaxSpreadPoints        = 30;
input ENUM_TIMEFRAMES SignalTF      = PERIOD_M15;

// ===== INDICATOR HANDLES =====
int fastHandle = INVALID_HANDLE;
int slowHandle = INVALID_HANDLE;
int rsiHandle  = INVALID_HANDLE;
int atrHandle  = INVALID_HANDLE;

datetime lastBarTime = 0;

// ===== INITIALIZATION =====
int OnInit()
{
   if(RiskPercent <= 0 ||
      RiskPercent > 5 ||
      DailyLossLimitPercent <= 0 ||
      MaxTradesPerDay < 1 ||
      FastEMA < 1 ||
      SlowEMA <= FastEMA ||
      RSIPeriod < 2 ||
      ATRPeriod < 1 ||
      SL_ATR_Multiplier <= 0 ||
      TP_RiskReward <= 0)
   {
      Print("Invalid EA settings. Check inputs.");
      return INIT_PARAMETERS_INCORRECT;
   }

   fastHandle = iMA(_Symbol, SignalTF,
                    FastEMA, 0, MODE_EMA, PRICE_CLOSE);

   slowHandle = iMA(_Symbol, SignalTF,
                    SlowEMA, 0, MODE_EMA, PRICE_CLOSE);

   rsiHandle = iRSI(_Symbol, SignalTF,
                     RSIPeriod, PRICE_CLOSE);

   atrHandle = iATR(_Symbol, SignalTF, ATRPeriod);

   if(fastHandle == INVALID_HANDLE ||
      slowHandle == INVALID_HANDLE ||
      rsiHandle  == INVALID_HANDLE ||
      atrHandle  == INVALID_HANDLE)
   {
      Print("Could not create indicator handles.");
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetTypeFillingBySymbol(_Symbol);
   trade.SetDeviationInPoints(10);

   Print("Forex Intelligence EA initialized.");
   return INIT_SUCCEEDED;
}

// ===== CLEANUP =====
void OnDeinit(const int reason)
{
   if(fastHandle != INVALID_HANDLE)
      IndicatorRelease(fastHandle);

   if(slowHandle != INVALID_HANDLE)
      IndicatorRelease(slowHandle);

   if(rsiHandle != INVALID_HANDLE)
      IndicatorRelease(rsiHandle);

   if(atrHandle != INVALID_HANDLE)
      IndicatorRelease(atrHandle);
}

// ===== NEW BAR CHECK =====
bool IsNewBar()
{
   datetime currentBar = iTime(_Symbol, SignalTF, 0);

   if(currentBar == 0 || currentBar == lastBarTime)
      return false;

   lastBarTime = currentBar;
   return true;
}

// ===== DAILY STATISTICS =====
bool GetDailyStats(double &dailyProfit, int &tradeCount)
{
   dailyProfit = 0.0;
   tradeCount = 0;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);

   dt.hour = 0;
   dt.min  = 0;
   dt.sec  = 0;

   datetime dayStart = StructToTime(dt);

   if(!HistorySelect(dayStart, TimeCurrent()))
   {
      Print("Could not load today's trade history.");
      return false;
   }

   int total = HistoryDealsTotal();

   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);

      if(ticket == 0)
         continue;

      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != _Symbol)
         continue;

      if((ulong)HistoryDealGetInteger(ticket, DEAL_MAGIC)
         != MagicNumber)
         continue;

      ENUM_DEAL_TYPE type =
         (ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket, DEAL_TYPE);

      if(type != DEAL_TYPE_BUY && type != DEAL_TYPE_SELL)
         continue;

      dailyProfit += HistoryDealGetDouble(ticket, DEAL_PROFIT);
      dailyProfit += HistoryDealGetDouble(ticket, DEAL_SWAP);
      dailyProfit += HistoryDealGetDouble(ticket, DEAL_COMMISSION);

      ENUM_DEAL_ENTRY entry =
         (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);

      if(entry == DEAL_ENTRY_IN || entry == DEAL_ENTRY_INOUT)
         tradeCount++;
   }

   return true;
}

// ===== CHECK OPEN POSITION =====
bool HasOpenPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);

      if(ticket == 0)
         continue;

      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC) == MagicNumber)
      {
         return true;
      }
   }

   return false;
}

// ===== SPREAD CHECK =====
bool SpreadIsAcceptable()
{
   MqlTick tick;

   if(!SymbolInfoTick(_Symbol, tick))
      return false;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   if(point <= 0)
      return false;

   double spread = (tick.ask - tick.bid) / point;

   return spread <= MaxSpreadPoints;
}

// ===== CALCULATE SAFE LOT SIZE =====
double CalculateLotSize(double stopDistance)
{
   if(stopDistance <= 0)
      return 0.0;

   double balance =
      AccountInfoDouble(ACCOUNT_BALANCE);

   double riskMoney =
      balance * RiskPercent / 100.0;

   double tickSize =
      SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);

   double tickValue =
      SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE_LOSS);

   double minLot =
      SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);

   double maxLot =
      SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   double lotStep =
      SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(tickSize <= 0 ||
      tickValue <= 0 ||
      minLot <= 0 ||
      lotStep <= 0 ||
      riskMoney <= 0)
   {
      Print("Cannot calculate lot size safely.");
      return 0.0;
   }

   double lossPerLot =
      (stopDistance / tickSize) * tickValue;

   if(lossPerLot <= 0)
      return 0.0;

   double rawLot = riskMoney / lossPerLot;

   // Never force the volume up to the broker's minimum.
   if(rawLot < minLot)
   {
      Print("Calculated lot is below minimum. Trade skipped.");
      return 0.0;
   }

   double lot =
      MathFloor(rawLot / lotStep) * lotStep;

   lot = MathMin(lot, maxLot);

   int volumeDigits = 0;
   double stepCheck = lotStep;

   while(volumeDigits < 8 &&
         MathAbs(stepCheck - MathRound(stepCheck)) > 0.00000001)
   {
      stepCheck *= 10.0;
      volumeDigits++;
   }

   lot = NormalizeDouble(lot, volumeDigits);

   if(lot < minLot)
      return 0.0;

   return lot;
}

// ===== OPEN BUY =====
void OpenBuy(double atrValue)
{
   MqlTick tick;

   if(!SymbolInfoTick(_Symbol, tick))
      return;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   if(point <= 0)
      return;

   double stopDistance = atrValue * SL_ATR_Multiplier;

   long stopsLevel =
      SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);

   stopDistance = MathMax(
      stopDistance,
      (stopsLevel + 2) * point
   );

   double lot = CalculateLotSize(stopDistance);

   if(lot <= 0)
      return;

   double sl = NormalizeDouble(
      tick.ask - stopDistance, digits
   );

   double tp = NormalizeDouble(
      tick.ask + stopDistance * TP_RiskReward, digits
   );

   bool sent = trade.Buy(
      lot, _Symbol, 0.0, sl, tp,
      "Forex Intelligence BUY"
   );

   if(!sent)
      Print("BUY failed: ", trade.ResultRetcodeDescription());
}

// ===== OPEN SELL =====
void OpenSell(double atrValue)
{
   MqlTick tick;

   if(!SymbolInfoTick(_Symbol, tick))
      return;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   if(point <= 0)
      return;

   double stopDistance = atrValue * SL_ATR_Multiplier;

   long stopsLevel =
      SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);

   stopDistance = MathMax(
      stopDistance,
      (stopsLevel + 2) * point
   );

   double lot = CalculateLotSize(stopDistance);

   if(lot <= 0)
      return;

   double sl = NormalizeDouble(
      tick.bid + stopDistance, digits
   );

   double tp = NormalizeDouble(
      tick.bid - stopDistance * TP_RiskReward, digits
   );

   bool sent = trade.Sell(
      lot, _Symbol, 0.0, sl, tp,
      "Forex Intelligence SELL"
   );

   if(!sent)
      Print("SELL failed: ", trade.ResultRetcodeDescription());
}

// ===== MAIN TRADING LOGIC =====
void OnTick()
{
   if(!TerminalInfoInteger(TERMINAL_CONNECTED))
      return;

   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
      return;

   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
      return;

   if(!IsNewBar())
      return;

   if(Bars(_Symbol, SignalTF) < SlowEMA + 10)
      return;

   double dailyProfit;
   int tradesToday;

   if(!GetDailyStats(dailyProfit, tradesToday))
      return;

   double balance = AccountInfoDouble(ACCOUNT_BALANCE);

   double dailyLossLimit =
      balance * DailyLossLimitPercent / 100.0;

   if(dailyProfit <= -dailyLossLimit)
   {
      Print("Daily loss limit reached. Trading stopped for today.");
      return;
   }

   if(tradesToday >= MaxTradesPerDay)
   {
      Print("Maximum daily trades reached.");
      return;
   }

   if(HasOpenPosition())
      return;

   if(!SpreadIsAcceptable())
      return;

   double fast1[1], fast2[1];
   double slow1[1], slow2[1];
   double rsi1[1], atr1[1];

   // Read the last two CLOSED candles.
   if(CopyBuffer(fastHandle, 0, 1, 1, fast1) != 1) return;
   if(CopyBuffer(fastHandle, 0, 2, 1, fast2) != 1) return;

   if(CopyBuffer(slowHandle, 0, 1, 1, slow1) != 1) return;
   if(CopyBuffer(slowHandle, 0, 2, 1, slow2) != 1) return;

   if(CopyBuffer(rsiHandle, 0, 1, 1, rsi1) != 1) return;
   if(CopyBuffer(atrHandle, 0, 1, 1, atr1) != 1) return;

   if(atr1[0] <= 0)
      return;

   bool bullishCross =
      fast2[0] <= slow2[0] &&
      fast1[0] > slow1[0];

   bool bearishCross =
      fast2[0] >= slow2[0] &&
      fast1[0] < slow1[0];

   bool buySignal =
      bullishCross &&
      rsi1[0] >= BuyRSIMin &&
      rsi1[0] <= BuyRSIMax;

   bool sellSignal =
      bearishCross &&
      rsi1[0] >= SellRSIMin &&
      rsi1[0] <= SellRSIMax;

   if(buySignal)
      OpenBuy(atr1[0]);

   else if(sellSignal)
      OpenSell(atr1[0]);
}
