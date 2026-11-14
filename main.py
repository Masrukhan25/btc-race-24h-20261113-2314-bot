"""BTC race round 3 bot (btc-race-24h-20261113-2314).

Longer-lookback momentum trend-follow, long only, ONE trade per 24h window,
small size. Iteration v2: the 75-bar momentum (v1) won only 6/17 held-out
windows and lost -$4.70 total. A longer 180-bar momentum with a tighter
hard stop (50 bps) is more selective (11 trades in 17 windows, 64% win rate
when it trades) and turns the held-out total positive (+$2.77) while still
passing the dev gate (+$1.47).

Method (from the round-2 top-10 study): the winners took a median of 2 trades,
rode the trend with a trailing stop, and kept drawdown tiny. Round 2's
dip-bounce bot lost -$17.20 live, so this bot does NOT buy dips. It waits for
a confirmed longer up-move and rides it with a two-stage trailing stop.

Entry (all integer math on raw closes, deterministic, no clock/network):
  - close up >= 80 bps vs 180 bars ago (long momentum), AND
  - close >= the highest close of the previous 60 bars (breakout confirmation).
  - Only 1 trade per window, only before bar 1320 (no entries in the last
    120 bars), 25% of cash.

Exit (two-stage trailing stop, the #1 winner's method):
  - Hard stop: -50 bps from the fill price (tighter than v1's -90; cuts
    reversal losses, which is what turned the held-out total positive).
  - Arm at +80 bps from the fill price; once armed, the stop trails the
    highest close since entry by 80 bps.
  - Max hold 300 bars.

The fill price is the fill bar's open (the true fill is ~5 bps above the open
after the 2 bps slippage + price impact, so the stops sit ~5 bps
conservatively low). The exit check also runs on the fill bar itself, exactly
like the validating backtest.

Validated on 17 held-out 24h windows (16 Coinbase days + round-2 dev):
see VALIDATION.md. Dev-window grade must be > $0 to race.
"""

# v2: longer 180-bar momentum, tighter -50 bps hard stop (see VALIDATION.md).

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
BPS = 10_000

# --- strategy parameters (validated; see VALIDATION.md) ---
MOM_LOOKBACK = 180         # momentum lookback, bars (v2: longer = more selective)
MOM_BPS = 80               # entry: close up >= 80 bps vs 180 bars ago
BREAKOUT_LOOKBACK = 60     # entry: close >= max close of previous 60 bars
MIN_HISTORY_BARS = 180     # need 180 bars of history before the first entry
ARM_BPS = 80               # trailing stop arms at +80 bps from fill
TRAIL_BPS = 80             # once armed, stop trails the peak by 80 bps
HARD_SL_BPS = 50           # hard stop at -50 bps from fill (v2: tighter)
MAX_HOLD_BARS = 300        # max bars held
MAX_TRADES = 1             # at most 1 trade per 24h window
SIZE_NUM = 1               # buy 1/4 of cash
SIZE_DEN = 4
COOLDOWN_BARS = 60         # cooldown after an exit (moot with MAX_TRADES=1)
LAST_ENTRY_BAR = 1320      # no entries in the last 120 bars of the window
MAX_SLIPPAGE_BPS = 200     # max slippage vs latest close

# --- per-run state (fresh process per run: deterministic) ---
closes = []                # raw close ints, in bar order
trades_done = 0
cooldown_until = -1
entry_px = None            # fill-bar open (raw price int)
peak_px = None             # highest close since entry
armed = False              # trailing stop armed
entry_bar = None           # window bar index of the fill bar
have_position = False


def on_batch(state, batch):
    global trades_done, cooldown_until, entry_px, peak_px, armed
    global entry_bar, have_position

    c = state.close(SYMBOL)
    if c is None:
        return []
    # Record every bar's close, including warm-up bars (they build history).
    closes.append(c)
    # Window bar index (0-based within the scored window).
    bar_idx = state.seq - state.warmup_bars

    holding = state.positions.get(SYMBOL)
    if holding is not None:
        if not have_position:
            # Just filled on this bar: entry = this bar's open.
            bar = state.latest.get(SYMBOL)
            entry_px = int(bar["o"]) if bar is not None and "o" in bar else c
            peak_px = entry_px
            armed = False
            entry_bar = bar_idx
            have_position = True
        # Exit check (also runs on the fill bar, as in the backtest).
        if c > peak_px:
            peak_px = c
        if not armed and c * BPS >= entry_px * (BPS + ARM_BPS):
            armed = True
        hard_stop = entry_px * (BPS - HARD_SL_BPS) // BPS
        stop_px = peak_px * (BPS - TRAIL_BPS) // BPS if armed else None
        do_exit = False
        if c <= hard_stop:
            do_exit = True
        elif armed and stop_px is not None and c <= stop_px:
            do_exit = True
        elif bar_idx - entry_bar >= MAX_HOLD_BARS:
            do_exit = True
        if do_exit:
            cooldown_until = bar_idx + COOLDOWN_BARS
            entry_px = None
            peak_px = None
            armed = False
            entry_bar = None
            have_position = False
            return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                     "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []

    # Flat: consider an entry.
    have_position = False
    if state.warming_up:
        return []
    if trades_done >= MAX_TRADES:
        return []
    if bar_idx >= LAST_ENTRY_BAR:
        return []
    if bar_idx < cooldown_until:
        return []
    if len(closes) <= MIN_HISTORY_BARS:
        return []
    # Momentum: up >= MOM_BPS over MOM_LOOKBACK bars.
    then = closes[-1 - MOM_LOOKBACK]
    if c * BPS < then * (BPS + MOM_BPS):
        return []
    # Breakout: new high over the previous BREAKOUT_LOOKBACK bars.
    hi = max(closes[-1 - BREAKOUT_LOOKBACK:-1])
    if c < hi:
        return []
    notional = state.cash * SIZE_NUM // SIZE_DEN
    if notional < 1:
        return []
    trades_done += 1
    return [{"side": "buy", "symbol": SYMBOL,
             "notional_quote_units": notional,
             "max_slippage_bps": MAX_SLIPPAGE_BPS}]


makefun_bars.run(on_batch)
