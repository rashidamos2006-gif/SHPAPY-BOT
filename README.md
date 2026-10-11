# SHPA"""
Forex Intelligence EA - Risk Managed Starter (Python version)

Inahitaji:  pip install MetaTrader5 pandas numpy
Mazingira:  Windows + MT5 desktop terminal imefunguliwa na umeingia (login),
            Algo Trading imewashwa. (Haiendeshwi kwenye MT5 ya simu.)
"""

import math
import time
import logging
from datetime import datetime, timezone, timedelta

import MetaTrader5 as mt5
import pandas as pd

# ===================== SETTINGS =====================
SYMBOL                  = "EURUSD"
MAGIC_NUMBER            = 20261011
RISK_PERCENT            = 1.0
DAILY_LOSS_LIMIT_PERCENT = 3.0
MAX_TRADES_PER_DAY      = 3

FAST_EMA                = 20
SLOW_EMA                = 50
RSI_PERIOD              = 14
ATR_PERIOD              = 14

BUY_RSI_MIN             = 50.0
BUY_RSI_MAX             = 70.0
SELL_RSI_MIN            = 30.0
SELL_RSI_MAX            = 50.0

SL_ATR_MULTIPLIER       = 2.0
TP_RISK_REWARD          = 2.0
MAX_SPREAD_POINTS       = 30
SIGNAL_TF               = mt5.TIMEFRAME_M15
DEVIATION_POINTS        = 10
POLL_SECONDS            = 1.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("ForexIntelligenceEA")

last_bar_time = 0


# ===================== INITIALIZATION =====================
def validate_inputs() -> bool:
    if (RISK_PERCENT <= 0 or RISK_PERCENT > 5 or
            DAILY_LOSS_LIMIT_PERCENT <= 0 or
            MAX_TRADES_PER_DAY < 1 or
            FAST_EMA < 1 or SLOW_EMA <= FAST_EMA or
            RSI_PERIOD < 2 or ATR_PERIOD < 1 or
            SL_ATR_MULTIPLIER <= 0 or TP_RISK_REWARD <= 0):
        log.error("Invalid EA settings. Check inputs.")
        return False
    return True


def init_ea() -> bool:
    if not validate_inputs():
        return False
    if not mt5.initialize():
        log.error("MT5 initialize failed: %s", mt5.last_error())
        return False
    if not mt5.symbol_select(SYMBOL, True):
        log.error("Could not select symbol %s", SYMBOL)
        mt5.shutdown()
        return False
    log.info("Forex Intelligence EA initialized.")
    return True


# ===================== NEW BAR CHECK =====================
def is_new_bar() -> bool:
    global last_bar_time
    rates = mt5.copy_rates_from_pos(SYMBOL, SIGNAL_TF, 0, 1)
    if rates is None or len(rates) == 0:
        return False
    current_bar = int(rates[0]["time"])
    if current_bar == 0 or current_bar == last_bar_time:
        return False
    last_bar_time = current_bar
    return True


# ===================== INDICATORS =====================
def get_indicators():
    """Rudisha (fast1, fast2, slow1, slow2, rsi1, atr1) kutoka candles 2 za mwisho
    zilizofungwa. Rudisha None kama data haitoshi."""
    count = max(SLOW_EMA, RSI_PERIOD, ATR_PERIOD) * 10 + 50
    rates = mt5.copy_rates_from_pos(SYMBOL, SIGNAL_TF, 0, count)
    if rates is None or len(rates) < SLOW_EMA + 10:
        return None

    df = pd.DataFrame(rates)
    close = df["close"]

    fast = close.ewm(span=FAST_EMA, adjust=False).mean()
    slow = close.ewm(span=SLOW_EMA, adjust=False).mean()

    # RSI (Wilder smoothing)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / RSI_PERIOD, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / RSI_PERIOD, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.fillna(100.0)

    # ATR (MT5 hutumia wastani rahisi wa True Range)
    prev_close = close.shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(ATR_PERIOD).mean()

    # iloc[-1] = candle inayoundwa; iloc[-2] = bar 1; iloc[-3] = bar 2
    fast1, fast2 = fast.iloc[-2], fast.iloc[-3]
    slow1, slow2 = slow.iloc[-2], slow.iloc[-3]
    rsi1 = rsi.iloc[-2]
    atr1 = atr.iloc[-2]

    if any(math.isnan(v) for v in (fast1, fast2, slow1, slow2, rsi1, atr1)):
        return None
    return fast1, fast2, slow1, slow2, rsi1, atr1


# ===================== DAILY STATISTICS =====================
def get_daily_stats():
    """Rudisha (daily_profit, trade_count) au None kama imeshindwa."""
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        return None

    now = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    deals = mt5.history_deals_get(day_start, now + timedelta(days=1))
    if deals is None:
        log.warning("Could not load today's trade history.")
        return None

    daily_profit = 0.0
    trade_count = 0
    for d in deals:
        if d.symbol != SYMBOL or d.magic != MAGIC_NUMBER:
            continue
        if d.type not in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
            continue
        daily_profit += d.profit + d.swap + d.commission
        if d.entry in (mt5.DEAL_ENTRY_IN, mt5.DEAL_ENTRY_INOUT):
            trade_count += 1
    return daily_profit, trade_count


# ===================== CHECK OPEN POSITION =====================
def has_open_position() -> bool:
    positions = mt5.positions_get(symbol=SYMBOL)
    if not positions:
        return False
    return any(p.magic == MAGIC_NUMBER for p in positions)


# ===================== SPREAD CHECK =====================
def spread_is_acceptable() -> bool:
    tick = mt5.symbol_info_tick(SYMBOL)
    info = mt5.symbol_info(SYMBOL)
    if tick is None or info is None or info.point <= 0:
        return False
    spread = (tick.ask - tick.bid) / info.point
    return spread <= MAX_SPREAD_POINTS


# ===================== CALCULATE SAFE LOT SIZE =====================
def calculate_lot_size(stop_distance: float) -> float:
    if stop_distance <= 0:
        return 0.0

    info = mt5.symbol_info(SYMBOL)
    acc = mt5.account_info()
    if info is None or acc is None:
        return 0.0

    risk_money = acc.balance * RISK_PERCENT / 100.0
    tick_size = info.trade_tick_size
    tick_value = info.trade_tick_value_loss
    min_lot, max_lot, lot_step = info.volume_min, info.volume_max, info.volume_step

    if (tick_size <= 0 or tick_value <= 0 or min_lot <= 0 or
            lot_step <= 0 or risk_money <= 0):
        log.warning("Cannot calculate lot size safely.")
        return 0.0

    loss_per_lot = (stop_distance / tick_size) * tick_value
    if loss_per_lot <= 0:
        return 0.0

    raw_lot = risk_money / loss_per_lot

    # Usilazimishe volume ipande hadi minimum ya broker.
    if raw_lot < min_lot:
        log.info("Calculated lot is below minimum. Trade skipped.")
        return 0.0

    lot = math.floor(raw_lot / lot_step + 1e-9) * lot_step
    lot = min(lot, max_lot)

    volume_digits = 0
    step_check = lot_step
    while volume_digits < 8 and abs(step_check - round(step_check)) > 1e-8:
        step_check *= 10.0
        volume_digits += 1

    lot = round(lot, volume_digits)
    if lot < min_lot:
        return 0.0
    return lot


# ===================== ORDER SENDING =====================
def get_filling_mode() -> int:
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        return mt5.ORDER_FILLING_IOC
    modes = info.filling_mode
    if modes & 1:      # SYMBOL_FILLING_FOK
        return mt5.ORDER_FILLING_FOK
    if modes & 2:      # SYMBOL_FILLING_IOC
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def open_trade(is_buy: bool, atr_value: float) -> None:
    tick = mt5.symbol_info_tick(SYMBOL)
    info = mt5.symbol_info(SYMBOL)
    if tick is None or info is None or info.point <= 0:
        return

    point, digits = info.point, info.digits
    stop_distance = atr_value * SL_ATR_MULTIPLIER
    stops_level = info.trade_stops_level
    stop_distance = max(stop_distance, (stops_level + 2) * point)

    lot = calculate_lot_size(stop_distance)
    if lot <= 0:
        return

    if is_buy:
        price = tick.ask
        sl = round(price - stop_distance, digits)
        tp = round(price + stop_distance * TP_RISK_REWARD, digits)
        order_type = mt5.ORDER_TYPE_BUY
        comment = "Forex Intelligence BUY"
    else:
        price = tick.bid
        sl = round(price + stop_distance, digits)
        tp = round(price - stop_distance * TP_RISK_REWARD, digits)
        order_type = mt5.ORDER_TYPE_SELL
        comment = "Forex Intelligence SELL"

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": DEVIATION_POINTS,
        "magic": MAGIC_NUMBER,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": get_filling_mode(),
    }

    result = mt5.order_send(request)
    side = "BUY" if is_buy else "SELL"
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        code = result.retcode if result else mt5.last_error()
        msg = result.comment if result else ""
        log.error("%s failed: %s %s", side, code, msg)
    else:
        log.info("%s opened | lot=%.2f price=%s sl=%s tp=%s",
                 side, lot, price, sl, tp)


# ===================== MAIN TRADING LOGIC (OnTick) =====================
def on_tick() -> None:
    term = mt5.terminal_info()
    acc = mt5.account_info()
    if term is None or acc is None:
        return
    if not term.connected or not term.trade_allowed or not acc.trade_allowed:
        return

    if not is_new_bar():
        return

    daily = get_daily_stats()
    if daily is None:
        return
    daily_profit, trades_today = daily

    daily_loss_limit = acc.balance * DAILY_LOSS_LIMIT_PERCENT / 100.0
    if daily_profit <= -daily_loss_limit:
        log.info("Daily loss limit reached. Trading stopped for today.")
        return

    if trades_today >= MAX_TRADES_PER_DAY:
        log.info("Maximum daily trades reached.")
        return

    if has_open_position():
        return

    if not spread_is_acceptable():
        return

    ind = get_indicators()
    if ind is None:
        return
    fast1, fast2, slow1, slow2, rsi1, atr1 = ind

    if atr1 <= 0:
        return

    bullish_cross = fast2 <= slow2 and fast1 > slow1
    bearish_cross = fast2 >= slow2 and fast1 < slow1

    buy_signal = bullish_cross and BUY_RSI_MIN <= rsi1 <= BUY_RSI_MAX
    sell_signal = bearish_cross and SELL_RSI_MIN <= rsi1 <= SELL_RSI_MAX

    if buy_signal:
        open_trade(True, atr1)
    elif sell_signal:
        open_trade(False, atr1)


# ===================== RUN =====================
def main() -> None:
    if not init_ea():
        return
    try:
        while True:
            on_tick()
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("Imesimamishwa na mtumiaji.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
PY-BOT
