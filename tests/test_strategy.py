"""Tests for main.py (stdlib only, run with python3 -m unittest).

Covers the RSI series, the two entry setups, the drawdown gate, the exits,
and an end-to-end replay of the real dev window through the real on_batch
with a fill-model driver that mirrors the harness (fills at the next open,
4 bps fee, 2 bps slippage, constant-product impact vs the open depth).
The end-to-end test asserts the dev-window PnL is positive and that two
identical runs produce identical orders (determinism, as the grader runs
the bot twice and requires byte-identical action logs).
"""

import gzip
import importlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KIT = "/home/hatch/workspace/makefun-work/btc-race-24h-20261115-0828-kit"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import makefun_bars  # noqa: E402
import main  # noqa: E402


def fresh_main():
    importlib.reload(main)
    return main


def feed_closes(m, closes):
    for c in closes:
        m.push_close(float(c))


class TestRsi(unittest.TestCase):
    def test_all_up_is_100(self):
        m = fresh_main()
        feed_closes(m, [100 + i for i in range(16)])
        self.assertEqual(m.rsis[-1], 100.0)

    def test_all_down_is_0(self):
        m = fresh_main()
        feed_closes(m, [100 - i for i in range(16)])
        self.assertEqual(m.rsis[-1], 0.0)

    def test_flat_is_100_no_losses(self):
        m = fresh_main()
        feed_closes(m, [100.0] * 20)
        self.assertEqual(m.rsis[-1], 100.0)

    def test_rsi_stays_in_range(self):
        m = fresh_main()
        feed_closes(m, [100, 99, 101, 98, 102, 97, 103, 96, 104, 95,
                         105, 94, 106, 93, 107, 92, 108, 91, 109, 90])
        for r in m.rsis:
            if r is not None:
                self.assertTrue(0.0 <= r <= 100.0)


class TestSignals(unittest.TestCase):
    def _declining_series(self):
        # 240 flat bars, then a steady grind down ~4%, then a green bounce.
        closes = [100.0] * 240
        price = 100.0
        for _ in range(30):
            price *= 0.9985
            closes.append(price)
        closes.append(closes[-1] * 1.003)  # green bounce bar
        return closes

    def test_dip_signal_after_grind(self):
        m = fresh_main()
        feed_closes(m, self._declining_series())
        self.assertLess(m.rsis[-1], 28.0)
        self.assertTrue(m.dip_signal())

    def test_drawdown_gate_blocks_shallow_dip(self):
        m = fresh_main()
        feed_closes(m, [100.0] * 240 + [99.0] * 15 + [99.5])
        # drawdown only ~1%: the gate must refuse the entry
        self.assertGreater(m.drawdown(), -0.02)
        self.assertFalse(m.entry_signal())

    def test_entry_signal_after_deep_drawdown(self):
        m = fresh_main()
        feed_closes(m, self._declining_series())
        self.assertLessEqual(m.drawdown(), -0.02)
        self.assertTrue(m.entry_signal())

    def test_breakout_after_washout(self):
        m = fresh_main()
        closes = [100.0] * 240
        price = 100.0
        for _ in range(30):
            price *= 0.9985
            closes.append(price)
        # base out for 60 bars, then break above the 60-bar high
        for _ in range(60):
            closes.append(closes[-1] * 1.0002)
        closes.append(max(closes[-60:]) * 1.002)
        feed_closes(m, closes)
        self.assertTrue(m.breakout_signal())
        self.assertTrue(m.entry_signal())

    def test_no_breakout_below_high(self):
        m = fresh_main()
        feed_closes(m, [100.0] * 300)
        self.assertFalse(m.breakout_signal())


class TestExits(unittest.TestCase):
    def test_take_profit(self):
        m = fresh_main()
        m.entry_price = 100.0
        m.peak_close = 100.0
        m.entry_seq = 0
        self.assertTrue(m.exit_signal(100.9, 10))

    def test_hard_stop(self):
        m = fresh_main()
        m.entry_price = 100.0
        m.peak_close = 100.0
        m.entry_seq = 0
        self.assertTrue(m.exit_signal(99.3, 10))

    def test_trailing_stop(self):
        m = fresh_main()
        m.entry_price = 100.0
        m.peak_close = 101.0
        m.entry_seq = 0
        self.assertTrue(m.exit_signal(100.4, 10))  # 0.6% below peak

    def test_timeout(self):
        m = fresh_main()
        m.entry_price = 100.0
        m.peak_close = 100.0
        m.entry_seq = 0
        self.assertTrue(m.exit_signal(100.1, 120))
        self.assertFalse(m.exit_signal(100.1, 119))

    def test_hold_inside_band(self):
        m = fresh_main()
        m.entry_price = 100.0
        m.peak_close = 100.5
        m.entry_seq = 0
        self.assertFalse(m.exit_signal(100.2, 10))


def load_dev_bars():
    rows = []
    with gzip.open(os.path.join(KIT, "dataset/bars/CBBTC.ndjson.gz"), "rt") as f:
        for line in f:
            r = json.loads(line)
            rows.append({
                "t": int(r["t"]),
                "o": int(r["o"]),
                "h": int(r["h"]),
                "l": int(r["l"]),
                "c": int(r["c"]),
                "o_depth": [int(d) for d in r["o_depth"]],
                "c_depth": [int(d) for d in r["c_depth"]],
                "symbol": "CBBTC",
            })
    return rows


HELLO = {
    "type": "hello",
    "protocol": "agent-fun/bars-replay/v1",
    "capital": {"quote": "USDC", "quote_scale": 6, "quote_units": "1000000000"},
    "symbols": [{"symbol": "CBBTC", "mint": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
                 "qty_scale": 8, "price_scale": 5}],
    "caps": {"max_open_positions": 1, "max_orders_per_bar": 16, "max_weight_bps": 10000},
    "fees": {"fee_bps": 4, "slippage_bps": 2, "min_notional_quote_units": "1000000"},
    "warmup_bars": 0,
    "seed": "1",
}


def replay_dev_window():
    """Drive the real on_batch over the dev window; returns (pnl_usd, orders)."""
    m = fresh_main()
    bars = load_dev_bars()
    state = makefun_bars.BarsState(HELLO)
    cash = 1_000_000_000  # quote units
    qty = 0
    cost = 0
    pending = []  # orders decided on the previous batch
    all_orders = []
    for seq, bar in enumerate(bars):
        # fills for the previous batch's orders, at this bar's open
        fills = []
        for o in pending:
            if o["side"] == "buy":
                amt = int(o["notional_quote_units"])
                imp = amt * 10_000 // bar["o_depth"][0]
                fillp = bar["o"] * (10_000 + 2 + imp) // 10_000
                fee = amt * 4 // 10_000
                q = (amt - fee) * 10 ** (8 + 5) // (fillp * 10 ** 6)
                qty += q
                cost += amt
                cash -= amt
                fills.append({"symbol": "CBBTC", "side": "buy", "status": "filled",
                              "price": str(fillp), "qty": str(q)})
            else:
                q = int(o["qty"])
                gross = q * bar["o"] // 10 ** (8 + 5) * 10 ** 6
                imp = gross * 10_000 // bar["o_depth"][0]
                fillp = bar["o"] * 10_000 // (10_000 + 2 + imp)
                proceeds = q * fillp // 10 ** (8 + 5) * 10 ** 6
                fee = proceeds * 4 // 10_000
                cash += proceeds - fee
                qty -= q
                fills.append({"symbol": "CBBTC", "side": "sell", "status": "filled",
                              "price": str(fillp), "qty": str(q)})
        value = qty * bar["c"] // 10 ** (8 + 5) * 10 ** 6 if qty else 0
        account = {"cash_quote_units": str(cash),
                   "equity_quote_units": str(cash + value),
                   "positions": ([{"symbol": "CBBTC", "qty": str(qty),
                                   "cost_quote_units": str(cost)}] if qty else [])}
        batch = {"seq": seq, "t": bar["t"], "bars": [bar], "fills": fills,
                 "account": account}
        state.apply_batch(batch)
        orders = state.to_orders(m.on_batch(state, batch))
        state.record_orders(orders)
        all_orders.extend((seq, o["side"], o.get("notional_quote_units") or o.get("qty"))
                          for o in orders)
        pending = orders
    # flatten at the last close
    if qty:
        bar = bars[-1]
        gross = qty * bar["c"] // 10 ** (8 + 5) * 10 ** 6
        imp = gross * 10_000 // bar["c_depth"][0]
        fillp = bar["c"] * 10_000 // (10_000 + 2 + imp)
        proceeds = qty * fillp // 10 ** (8 + 5) * 10 ** 6
        cash += proceeds - proceeds * 4 // 10_000
    return (cash - 1_000_000_000) / 1_000_000.0, all_orders


class TestEndToEnd(unittest.TestCase):
    def test_dev_window_profitable(self):
        pnl, _ = replay_dev_window()
        self.assertGreater(pnl, 0.0, "dev-window PnL must be positive (dev gate)")

    def test_deterministic(self):
        pnl1, orders1 = replay_dev_window()
        pnl2, orders2 = replay_dev_window()
        self.assertEqual(orders1, orders2)
        self.assertEqual(pnl1, pnl2)


if __name__ == "__main__":
    unittest.main()
