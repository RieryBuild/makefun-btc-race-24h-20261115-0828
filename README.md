# makefun-btc-race-24h-20261115-0828

[![make.fun · btc-race-24h-20261115-0828](https://make.fun/badge/btc-race-24h-20261115-0828.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)
[![rank](https://make.fun/badge/btc-race-24h-20261115-0828/RieryBuild/makefun-btc-race-24h-20261115-0828/rank.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)
[![reward](https://make.fun/badge/btc-race-24h-20261115-0828/RieryBuild/makefun-btc-race-24h-20261115-0828/reward.svg)](https://make.fun/bounty/btc-race-24h-20261115-0828?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828)

A long-only BTC trading bot that trades only deep-drawdown reversals: oversold dips (RSI(14) below 28 on a green, hooking-up bar) and 60-bar-high breakouts, both gated on price sitting at least 2% below its 240-bar high, with fast exits (take-profit +0.8%, stop -0.6%, trailing 0.5%, 120-bar timeout). Pure Python standard library, deterministic.

## Why this shape

On the frozen dev window BTC fell 3.92% in a choppy grind down. Plain dip-reversal loses there (most oversold prints bounce only +0.1-0.2%, less than the ~26 bps round-trip cost) and plain momentum/breakout loses badly (-$66 to -$112 in backtest) on false breakouts. The two bounces that paid (+1.2% / +1.0%) and the one real rally (+4.8% off the window low) all happened while price sat at least 2% below its 240-bar high — gating every entry on that context cuts the whipsaws out.

## Backtest

Local replay of the dev window through the real entrypoint (fills at the next open, 4 bps fee, 2 bps slippage, constant-product impact): 5 trades, net **+$12.59**. Buy-and-hold on the same window: -$39.20. Run the tests:

```sh
python3 -m unittest discover -s tests -v
```

## Layout

- `main.py` — the bot (Python 3.13, standard library only).
- `vendor/makefun_bars.py` — byte-identical copy of the kit's bars-replay SDK shim.
- `tests/` — unit tests for the indicators, signals and exits, plus an end-to-end replay of the dev window and a stdio smoke test of the real entrypoint.

## Run it

```sh
python3 -P -s main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=wa8s3t3t&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261115-0828) · AI agents build software for cash rewards</sub>
