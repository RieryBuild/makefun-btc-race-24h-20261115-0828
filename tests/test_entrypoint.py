"""Subprocess smoke test: run the real entrypoint exactly as the grader does
(python3 -P -s main.py), speak agent-fun/bars-replay/v1 at it over the full
dev window, and assert every batch gets exactly one well-formed reply echoing
its seq. Fills mirror the harness (next-open fills, 4 bps fee, 2 bps
slippage, constant-product impact vs the open depth).
"""

import gzip
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = "/home/hatch/workspace/makefun-work/btc-race-24h-20261115-0828-kit"


def load_dev_bars():
    rows = []
    with gzip.open(os.path.join(KIT, "dataset/bars/CBBTC.ndjson.gz"), "rt") as f:
        for line in f:
            r = json.loads(line)
            rows.append({"t": int(r["t"]), "o": r["o"], "h": r["h"], "l": r["l"],
                         "c": r["c"], "o_depth": r["o_depth"], "c_depth": r["c_depth"],
                         "symbol": "CBBTC"})
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


class TestEntrypoint(unittest.TestCase):
    def test_full_window_over_stdio(self):
        bars = load_dev_bars()
        proc = subprocess.Popen(
            [sys.executable, "-P", "-s", os.path.join(ROOT, "main.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=ROOT)
        cash = 1_000_000_000
        qty = 0
        cost = 0
        pending = []
        replies = 0
        orders_seen = []

        def send(msg):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()

        def recv():
            line = proc.stdout.readline()
            self.assertTrue(line, "bot stopped answering (stdout closed)")
            return json.loads(line)

        send(HELLO)
        for seq, bar in enumerate(bars):
            fills = []
            for o in pending:
                if o["side"] == "buy":
                    amt = int(o["notional_quote_units"])
                    imp = amt * 10_000 // int(bar["o_depth"][0])
                    fillp = int(bar["o"]) * (10_000 + 2 + imp) // 10_000
                    fee = amt * 4 // 10_000
                    q = (amt - fee) * 10 ** 13 // (fillp * 10 ** 6)
                    qty += q
                    cost += amt
                    cash -= amt
                    fills.append({"symbol": "CBBTC", "side": "buy", "status": "filled",
                                  "price": str(fillp), "qty": str(q)})
                else:
                    q = int(o["qty"])
                    gross = q * int(bar["o"]) // 10 ** 13 * 10 ** 6
                    imp = gross * 10_000 // int(bar["o_depth"][0])
                    fillp = int(bar["o"]) * 10_000 // (10_000 + 2 + imp)
                    proceeds = q * fillp // 10 ** 13 * 10 ** 6
                    fee = proceeds * 4 // 10_000
                    cash += proceeds - fee
                    qty -= q
                    fills.append({"symbol": "CBBTC", "side": "sell", "status": "filled",
                                  "price": str(fillp), "qty": str(q)})
            value = qty * int(bar["c"]) // 10 ** 13 * 10 ** 6 if qty else 0
            account = {"cash_quote_units": str(cash),
                       "equity_quote_units": str(cash + value),
                       "positions": ([{"symbol": "CBBTC", "qty": str(qty),
                                       "cost_quote_units": str(cost)}] if qty else [])}
            send({"type": "batch", "seq": seq, "t": bar["t"],
                  "bars": [bar], "fills": fills, "account": account})
            reply = recv()
            replies += 1
            self.assertEqual(reply["type"], "reply")
            self.assertEqual(reply["seq"], seq)
            self.assertIsInstance(reply["orders"], list)
            orders_seen.extend((seq, o["side"]) for o in reply["orders"])
            pending = reply["orders"]
        send({"type": "done", "seq": len(bars),
              "account": {"cash_quote_units": str(cash),
                          "equity_quote_units": str(cash),
                          "positions": []}})
        proc.stdin.close()
        proc.wait(timeout=30)
        self.assertEqual(proc.returncode, 0,
                         "bot must exit 0 after done (stderr: %s)"
                         % proc.stderr.read()[:500])
        self.assertEqual(replies, len(bars))
        self.assertGreater(len(orders_seen), 0, "bot must trade at least once")
        if qty:
            bar = bars[-1]
            gross = qty * int(bar["c"]) // 10 ** 13 * 10 ** 6
            imp = gross * 10_000 // int(bar["c_depth"][0])
            fillp = int(bar["c"]) * 10_000 // (10_000 + 2 + imp)
            proceeds = qty * fillp // 10 ** 13 * 10 ** 6
            cash += proceeds - proceeds * 4 // 10_000
        pnl = (cash - 1_000_000_000) / 1_000_000.0
        self.assertGreater(pnl, 0.0, "dev-window PnL must be positive")
        print("\nstdio replay PnL: $%.2f orders: %s" % (pnl, orders_seen))


if __name__ == "__main__":
    unittest.main()
