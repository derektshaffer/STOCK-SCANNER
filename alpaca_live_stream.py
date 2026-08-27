import json
import os
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

try:
    import websocket
except Exception:
    websocket = None


API_KEY = os.environ.get("ALPACA_API_KEY", "").strip()
API_SECRET = os.environ.get("ALPACA_SECRET_KEY", "").strip()
ET = ZoneInfo("America/New_York")


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _is_regular_bar(timestamp):
    dt = _parse_dt(timestamp)
    if dt is None:
        return False
    et = dt.astimezone(ET)
    minute = et.hour * 60 + et.minute
    return et.weekday() < 5 and 570 <= minute < 960


class _LiveStream:
    def __init__(self):
        self.lock = threading.RLock()
        self.thread = None
        self.ws = None
        self.generation = 0
        self.desired_symbol = None
        self.desired_feed = None
        self.state = self._blank_state()

    def _blank_state(self):
        return {
            "status": "idle",
            "symbol": None,
            "feed": None,
            "connected": False,
            "authenticated": False,
            "error": None,
            "last_message_at": None,
            "last_trade": None,
            "last_quote": None,
            "last_bar": None,
            "session_volume": None,
            "session_pv": None,
            "session_vwap": None,
            "day_high": None,
            "day_low": None,
            "seeded_at": None,
            "seed_cutoff": None,
            "bars_seen": set(),
        }

    def _public_state(self):
        out = dict(self.state)
        out.pop("session_pv", None)
        out.pop("bars_seen", None)
        last = out.get("last_message_at")
        out["message_age_seconds"] = (
            round(max(0.0, time.time() - last), 2) if last else None
        )
        return out

    def get(self, symbol=None):
        with self.lock:
            out = self._public_state()
        if symbol and str(out.get("symbol") or "").upper() != str(symbol).upper():
            return {
                "status": "switching",
                "symbol": str(symbol).upper(),
                "feed": out.get("feed"),
                "connected": False,
                "authenticated": False,
            }
        return out

    def _seed(self, metrics):
        if not isinstance(metrics, dict):
            return
        symbol = str(metrics.get("symbol") or "").upper().strip()
        if not symbol or symbol != self.state.get("symbol"):
            return

        volume = _num(metrics.get("session_volume"))
        if volume is None:
            volume = _num(metrics.get("volume"))
        vwap = _num(metrics.get("vwap"))
        self.state["session_volume"] = volume
        self.state["session_pv"] = (volume * vwap) if volume and vwap else None
        self.state["session_vwap"] = vwap
        self.state["day_high"] = _num(metrics.get("day_high"))
        self.state["day_low"] = _num(metrics.get("day_low"))
        self.state["seeded_at"] = time.time()

        as_of = _parse_dt(metrics.get("as_of"))
        self.state["seed_cutoff"] = as_of.timestamp() if as_of else time.time()
        self.state["bars_seen"] = set()

    def ensure(self, symbol, feed, metrics=None):
        symbol = str(symbol or "").upper().strip()
        feed = str(feed or "iex").lower().strip()
        if feed not in {"sip", "iex"}:
            feed = "iex"

        if not symbol:
            return self.get()
        if websocket is None:
            with self.lock:
                self.state.update(
                    {
                        "status": "disabled",
                        "symbol": symbol,
                        "feed": feed,
                        "error": "websocket-client is not installed",
                    }
                )
            return self.get()
        if not API_KEY or not API_SECRET:
            with self.lock:
                self.state.update(
                    {
                        "status": "disabled",
                        "symbol": symbol,
                        "feed": feed,
                        "error": "missing Alpaca credentials",
                    }
                )
            return self.get()

        with self.lock:
            same = (
                self.desired_symbol == symbol
                and self.desired_feed == feed
                and self.thread is not None
                and self.thread.is_alive()
            )
            if same:
                if metrics and self.state.get("seeded_at") is None:
                    self._seed(metrics)
                return self._public_state()

            self.generation += 1
            generation = self.generation
            old_ws = self.ws
            self.desired_symbol = symbol
            self.desired_feed = feed
            self.state = self._blank_state()
            self.state.update(
                {
                    "status": "connecting",
                    "symbol": symbol,
                    "feed": feed,
                }
            )
            self._seed(metrics)

            if old_ws is not None:
                try:
                    old_ws.close()
                except Exception:
                    pass

            self.thread = threading.Thread(
                target=self._run_loop,
                args=(generation, symbol, feed),
                daemon=True,
                name=f"alpaca-stream-{symbol}-{feed}",
            )
            self.thread.start()
            return self._public_state()

    def _still_current(self, generation, symbol, feed):
        with self.lock:
            return (
                generation == self.generation
                and symbol == self.desired_symbol
                and feed == self.desired_feed
            )

    def _run_loop(self, generation, symbol, feed):
        delay = 1.0
        while self._still_current(generation, symbol, feed):
            url = f"wss://stream.data.alpaca.markets/v2/{feed}"

            def on_open(wsapp):
                with self.lock:
                    if not self._still_current(generation, symbol, feed):
                        return
                    self.state.update(
                        {
                            "status": "authenticating",
                            "connected": True,
                            "error": None,
                        }
                    )
                wsapp.send(
                    json.dumps(
                        {
                            "action": "auth",
                            "key": API_KEY,
                            "secret": API_SECRET,
                        }
                    )
                )

            def on_message(wsapp, raw):
                self._handle_message(generation, symbol, feed, wsapp, raw)

            def on_error(_wsapp, error):
                with self.lock:
                    if self._still_current(generation, symbol, feed):
                        self.state["error"] = str(error)[:220]
                        self.state["status"] = "error"

            def on_close(_wsapp, _code, _msg):
                with self.lock:
                    if self._still_current(generation, symbol, feed):
                        self.state["connected"] = False
                        self.state["authenticated"] = False
                        if self.state.get("status") != "error":
                            self.state["status"] = "reconnecting"

            app = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            with self.lock:
                if not self._still_current(generation, symbol, feed):
                    return
                self.ws = app
            try:
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                with self.lock:
                    if self._still_current(generation, symbol, feed):
                        self.state["error"] = str(exc)[:220]
                        self.state["status"] = "error"

            if not self._still_current(generation, symbol, feed):
                return
            time.sleep(delay)
            delay = min(15.0, delay * 1.8)

    def _handle_message(self, generation, symbol, feed, wsapp, raw):
        if not self._still_current(generation, symbol, feed):
            return
        try:
            payload = json.loads(raw)
        except Exception:
            return
        messages = payload if isinstance(payload, list) else [payload]

        for msg in messages:
            if not isinstance(msg, dict):
                continue
            kind = msg.get("T")
            now_ts = time.time()

            if kind == "success":
                text = str(msg.get("msg") or "")
                with self.lock:
                    self.state["last_message_at"] = now_ts
                    if text == "authenticated":
                        self.state.update(
                            {
                                "authenticated": True,
                                "status": "subscribing",
                                "error": None,
                            }
                        )
                        wsapp.send(
                            json.dumps(
                                {
                                    "action": "subscribe",
                                    "trades": [symbol],
                                    "quotes": [symbol],
                                    "bars": [symbol],
                                }
                            )
                        )
                continue

            if kind == "error":
                with self.lock:
                    self.state.update(
                        {
                            "status": "error",
                            "error": str(msg.get("msg") or msg)[:220],
                            "last_message_at": now_ts,
                        }
                    )
                continue

            if kind == "subscription":
                with self.lock:
                    self.state.update(
                        {
                            "status": "streaming",
                            "authenticated": True,
                            "connected": True,
                            "error": None,
                            "last_message_at": now_ts,
                        }
                    )
                continue

            if str(msg.get("S") or "").upper() != symbol:
                continue

            with self.lock:
                self.state["last_message_at"] = now_ts
                if kind == "t":
                    price = _num(msg.get("p"))
                    self.state["last_trade"] = {
                        "price": price,
                        "size": _num(msg.get("s")),
                        "timestamp": msg.get("t"),
                        "exchange": msg.get("x"),
                    }
                    if price is not None:
                        high = _num(self.state.get("day_high"))
                        low = _num(self.state.get("day_low"))
                        self.state["day_high"] = price if high is None else max(high, price)
                        self.state["day_low"] = price if low is None else min(low, price)

                elif kind == "q":
                    self.state["last_quote"] = {
                        "bid": _num(msg.get("bp")),
                        "ask": _num(msg.get("ap")),
                        "bid_size": _num(msg.get("bs")),
                        "ask_size": _num(msg.get("as")),
                        "timestamp": msg.get("t"),
                    }

                elif kind == "b":
                    self.state["last_bar"] = dict(msg)
                    self._apply_bar(msg)

    def _apply_bar(self, bar):
        timestamp = bar.get("t")
        dt = _parse_dt(timestamp)
        if dt is None or not _is_regular_bar(timestamp):
            return

        cutoff = self.state.get("seed_cutoff")
        if cutoff and dt.timestamp() <= float(cutoff):
            return

        key = dt.isoformat()
        seen = self.state.setdefault("bars_seen", set())
        if key in seen:
            return
        seen.add(key)

        volume = _num(bar.get("v")) or 0.0
        high = _num(bar.get("h"))
        low = _num(bar.get("l"))
        close = _num(bar.get("c"))
        typical_values = [v for v in (high, low, close) if v is not None]
        typical = sum(typical_values) / len(typical_values) if typical_values else None

        session_volume = _num(self.state.get("session_volume")) or 0.0
        session_pv = _num(self.state.get("session_pv")) or 0.0
        session_volume += volume
        if typical is not None:
            session_pv += typical * volume

        self.state["session_volume"] = session_volume
        self.state["session_pv"] = session_pv
        self.state["session_vwap"] = (
            session_pv / session_volume if session_volume else None
        )

        old_high = _num(self.state.get("day_high"))
        old_low = _num(self.state.get("day_low"))
        if high is not None:
            self.state["day_high"] = high if old_high is None else max(old_high, high)
        if low is not None:
            self.state["day_low"] = low if old_low is None else min(old_low, low)


_STREAM = _LiveStream()


def ensure_live_stream(symbol, feed, metrics=None):
    return _STREAM.ensure(symbol, feed, metrics=metrics)


def get_live_state(symbol=None):
    return _STREAM.get(symbol=symbol)


def get_live_overlay(metrics):
    metrics = metrics or {}
    symbol = str(metrics.get("symbol") or "").upper().strip()
    state = get_live_state(symbol)
    trade = state.get("last_trade") or {}
    quote = state.get("last_quote") or {}

    price = _num(trade.get("price"))
    if price is None:
        price = _num(metrics.get("price"))
    bid = _num(quote.get("bid"))
    if bid is None:
        bid = _num(metrics.get("bid"))
    ask = _num(quote.get("ask"))
    if ask is None:
        ask = _num(metrics.get("ask"))

    spread_pct = None
    if bid and ask and ask > 0:
        spread_pct = (ask - bid) / ask * 100.0

    vwap = _num(state.get("session_vwap"))
    if vwap is None:
        vwap = _num(metrics.get("vwap"))

    volume = _num(state.get("session_volume"))
    if volume is None:
        volume = _num(metrics.get("session_volume"))
    if volume is None:
        volume = _num(metrics.get("volume"))

    plan = metrics.get("trade_plan") or {}
    selected = plan.get("selected") or {}
    entry_low = _num(selected.get("entry_low"))
    entry_high = _num(selected.get("entry_high"))
    plan_status = str(plan.get("status") or "")

    if plan_status == "NO TRADE":
        breakout = "NO TRADE"
    elif price is None or entry_low is None or entry_high is None:
        breakout = "NO TRIGGER"
    elif price >= entry_high:
        breakout = "ABOVE TRIGGER"
    elif price >= entry_low:
        breakout = "IN ENTRY ZONE"
    else:
        breakout = "BELOW TRIGGER"

    vwap_position = (
        "ABOVE" if price is not None and vwap is not None and price >= vwap
        else "BELOW" if price is not None and vwap is not None
        else "N/A"
    )

    return {
        **state,
        "price": price,
        "bid": bid,
        "ask": ask,
        "spread_pct": round(spread_pct, 3) if spread_pct is not None else None,
        "vwap": round(vwap, 4) if vwap is not None else None,
        "vwap_position": vwap_position,
        "session_volume": round(volume) if volume is not None else None,
        "breakout_state": breakout,
        "entry_low": entry_low,
        "entry_high": entry_high,
    }
