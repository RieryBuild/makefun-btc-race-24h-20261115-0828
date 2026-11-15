"""Dip-reversal + deep-drawdown breakout bot for the BTC 24h race.

Speaks agent-fun/bars-replay/v1 over stdin/stdout (via vendor/makefun_bars.py),
trading Coinbase Wrapped BTC (CBBTC) on 1-minute bars. Long only, one position,
full size, deterministic: float arithmetic only, no clock, no randomness.

Why this shape
--------------
On the frozen dev window BTC fell 3.92% over 24h in a choppy grind down.
Tested on that window (local backtest, same fill model as the harness: fills
at the next open, 4 bps fee, 2 bps slippage, constant-product impact):

- Plain dip-reversal (RSI<28 + green + hook-up) LOSES: most oversold prints
  bounce only +0.1-0.2% in the next 15 bars, less than the ~26 bps round-trip
  cost. Only 2 of 14 signals bounced over +1%.
- Plain momentum / breakout / EMA-cross LOSES badly (-$66 to -$112): the
  grind throws a false breakout every few dozen bars and each whipsaw costs
  ~$5-8.
- The two bounces that DID pay (bars ~423 and ~767, +1.2% / +1.0%) and the
  one real rally (window low bar ~1037, +4.8% over the next 60 bars) all
  happened while price sat at least 2% below its 240-bar high. Gating every
  entry on that deep-drawdown context cuts the whipsaws out: the same
  backtest then nets +$21.39 on the dev window (5 trades), with every exit
  parameter in the neighbourhood (TP 1.0-2.0%, stop 0.6-0.8%) also positive.

So the bot trades only two setups, both gated on a >=2% drawdown from the
240-bar high, and banks bounces fast (TP +0.8%) because on this window
bounces fade within tens of bars:

  Setup A - oversold dip: RSI(14) < 28, this bar closed green, RSI hooking
    up from the previous bar. No RSI-floor or 15-bar-drop filter: on the dev
    window the paying bounces followed sharper drops, and the drawdown gate
    already does the filtering.
  Setup B - breakout after the washout: this close above the previous
    60-bar high. Catches the relief rally once it confirms instead of
    guessing the bottom.

  Exits (whole position): +0.8% take-profit, -0.6% hard stop, 0.5% trailing
  stop from the best close since entry, or 120 bars timeout.
  No new entries in the last 60 scored bars of the 1440-bar window.
  Orders cap slippage at 100 bps past the close decided on.

Backtest (local replica of the fill model) on the dev window: 5 trades,
net +$21.39. Buy-and-hold on the same window: -$39.20.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"
RSI_SPAN = 14
RSI_ENTRY = 28
DRAWDOWN_BARS = 240
DRAWDOWN_MIN = -0.02      # entry only >= 2% below the 240-bar high
BREAKOUT_BARS = 60
TAKE_PROFIT = 0.008
HARD_STOP = 0.006
TRAIL = 0.005
TIMEOUT_BARS = 120
NO_ENTRY_AFTER_BARS = 1380
MAX_SLIPPAGE_BPS = 100

# --- indicator state (closes seen so far, warm-up included) ---
closes = []
rsis = []
_avg_gain = None
_avg_loss = None
_warm_gains = []
_warm_losses = []

# --- trade state, set from fills (the replay's own record) ---
entry_price = None
entry_seq = -1
peak_close = 0.0


def push_close(close):
    """Feed one close into the RSI(14) series (Wilder's smoothing)."""
    global _avg_gain, _avg_loss
    prev = closes[-1] if closes else None
    closes.append(close)
    if prev is None:
        rsis.append(None)
        return
    change = close - prev
    gain = change if change > 0 else 0.0
    loss = -change if change < 0 else 0.0
    if _avg_gain is None:
        _warm_gains.append(gain)
        _warm_losses.append(loss)
        if len(_warm_gains) == RSI_SPAN:
            _avg_gain = sum(_warm_gains) / RSI_SPAN
            _avg_loss = sum(_warm_losses) / RSI_SPAN
            rsis.append(100.0 if _avg_loss == 0 else 100.0 - 100.0 / (1.0 + _avg_gain / _avg_loss))
        else:
            rsis.append(None)
        return
    _avg_gain = (_avg_gain * (RSI_SPAN - 1) + gain) / RSI_SPAN
    _avg_loss = (_avg_loss * (RSI_SPAN - 1) + loss) / RSI_SPAN
    rsis.append(100.0 if _avg_loss == 0 else 100.0 - 100.0 / (1.0 + _avg_gain / _avg_loss))


def drawdown():
    """Current close vs the highest close of the last 240 bars, as a ratio."""
    window = closes[-DRAWDOWN_BARS - 1:]
    return closes[-1] / max(window) - 1.0


def dip_signal():
    """Setup A: oversold dip, green bar, RSI hooking up."""
    n = len(closes)
    if n < RSI_SPAN + 2:
        return False
    rsi = rsis[n - 1]
    prev_rsi = rsis[n - 2]
    if rsi is None or prev_rsi is None:
        return False
    if rsi >= RSI_ENTRY:
        return False
    if closes[n - 1] <= closes[n - 2]:
        return False  # needs a green bar
    if rsi <= prev_rsi:
        return False  # RSI must hook up
    return True


def breakout_signal():
    """Setup B: close above the previous 60-bar high."""
    n = len(closes)
    if n <= BREAKOUT_BARS:
        return False
    return closes[n - 1] > max(closes[n - 1 - BREAKOUT_BARS:n - 1])


def entry_signal():
    """Either setup, but only while >= 2% below the 240-bar high."""
    if drawdown() > DRAWDOWN_MIN:
        return False
    return dip_signal() or breakout_signal()


def exit_signal(close, scored_bars):
    """Whole-position exit: TP, hard stop, trailing stop, or timeout."""
    if entry_price is None:
        return False
    ret = close / entry_price - 1.0
    if ret >= TAKE_PROFIT:
        return True
    if ret <= -HARD_STOP:
        return True
    if close < peak_close * (1.0 - TRAIL):
        return True
    if entry_seq >= 0 and scored_bars - entry_seq >= TIMEOUT_BARS:
        return True
    return False


def on_batch(state, batch):
    global entry_price, entry_seq, peak_close
    fresh = False
    for bar in batch["bars"]:
        if bar["symbol"] != SYMBOL:
            continue
        push_close(int(bar["c"]))
        fresh = True
    # Fills are the record of what the replay did: a filled buy sets the
    # entry price and peak; a filled sell clears them (via positions below).
    for fill in batch["fills"]:
        if fill["symbol"] != SYMBOL or fill["status"] != "filled":
            continue
        if fill["side"] == "buy":
            entry_price = int(fill["price"])
            entry_seq = state.seq - state.warmup_bars
            peak_close = entry_price
    held = state.positions.get(SYMBOL)
    if held is not None and fresh:
        close = closes[-1]
        if close > peak_close:
            peak_close = close
    if held is None:
        entry_price = None
        entry_seq = -1
        peak_close = 0.0
    if state.warming_up or not fresh:
        return []

    scored_bars = state.seq - state.warmup_bars
    close = closes[-1]
    orders = []

    if held is not None:
        if exit_signal(close, scored_bars):
            orders.append({
                "side": "sell",
                "symbol": SYMBOL,
                "qty": held.qty,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            })
        return orders

    # Flat: only the two gated setups, never in the last 60 scored bars.
    if scored_bars >= NO_ENTRY_AFTER_BARS:
        return []
    if not entry_signal():
        return []
    room = state.weight_room(SYMBOL)
    spend = state.cash if state.cash < room else room
    if spend < int(state.hello["fees"]["min_notional_quote_units"]):
        return []
    orders.append({
        "side": "buy",
        "symbol": SYMBOL,
        "notional_quote_units": spend,
        "max_slippage_bps": MAX_SLIPPAGE_BPS,
    })
    return orders


if __name__ == "__main__":
    makefun_bars.run(on_batch)
