import json
import os
import threading
import time
import urllib.parse
import urllib.request
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
        self.subscribed_symbol = None
        self.blocked_until = 0.0
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
        if self.blocked_until > time.time():
            out["retry_after_seconds"] = round(
                max(0.0, self.blocked_until - time.time()), 1
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
                "connected": out.get("connected", False),
                "authenticated": out.get("authenticated", False),
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

    def _send_symbol_switch(self, wsapp, old_symbol, new_symbol):
        if wsapp is None or not new_symbol:
            return
        try:
            if old_symbol and old_symbol != new_symbol:
                wsapp.send(
                    json.dumps(
                        {
                            "action": "unsubscribe",
                            "trades": [old_symbol],
                            "quotes": [old_symbol],
                            "bars": [old_symbol],
                        }
                    )
                )
            wsapp.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "trades": [new_symbol],
                        "quotes": [new_symbol],
                        "bars": [new_symbol],
                    }
                )
            )
            with self.lock:
                self.subscribed_symbol = new_symbol
                self.state["status"] = "subscribing"
        except Exception as exc:
            with self.lock:
                self.state["error"] = str(exc)[:220]
                self.state["status"] = "error"

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

        ws_to_switch = None
        old_symbol = None
        old_thread = None
        old_ws = None

        with self.lock:
            thread_alive = self.thread is not None and self.thread.is_alive()

            # Reuse the existing socket when only the ticker changes. Alpaca
            # commonly permits only one connection per endpoint/account.
            if thread_alive and self.desired_feed == feed:
                if self.desired_symbol != symbol:
                    old_symbol = self.subscribed_symbol or self.desired_symbol
                    connected = bool(self.state.get("connected"))
                    authenticated = bool(self.state.get("authenticated"))
                    self.desired_symbol = symbol

                    previous_state = self.state
                    self.state = self._blank_state()
                    self.state.update(
                        {
                            "status": "switching" if authenticated else previous_state.get("status", "connecting"),
                            "symbol": symbol,
                            "feed": feed,
                            "connected": connected,
                            "authenticated": authenticated,
                            "error": None,
                        }
                    )
                    self._seed(metrics)
                    if authenticated and self.ws is not None:
                        ws_to_switch = self.ws
                elif metrics and self.state.get("seeded_at") is None:
                    self._seed(metrics)

                current = self._public_state()
            else:
                current = None

            if current is None:
                # If Alpaca just rejected us for a connection-limit error,
                # don't hammer the endpoint on every Streamlit refresh.
                if self.blocked_until > time.time() and self.desired_feed in {None, feed}:
                    self.desired_symbol = symbol
                    self.desired_feed = feed
                    self.state.update(
                        {
                            "status": "connection_limit",
                            "symbol": symbol,
                            "feed": feed,
                            "connected": False,
                            "authenticated": False,
                        }
                    )
                    self._seed(metrics)
                    return self._public_state()

                # Feed changes require a different endpoint, so close the old
                # socket and wait briefly for its thread to release the Alpaca
                # session before opening the replacement.
                if thread_alive:
                    self.generation += 1
                    old_thread = self.thread
                    old_ws = self.ws

                self.desired_symbol = symbol
                self.desired_feed = feed

        if ws_to_switch is not None:
            self._send_symbol_switch(ws_to_switch, old_symbol, symbol)
            return self.get(symbol)

        if current is not None:
            return current

        if old_ws is not None:
            try:
                old_ws.close()
            except Exception:
                pass
        if old_thread is not None:
            try:
                old_thread.join(timeout=2.0)
            except Exception:
                pass

        with self.lock:
            self.generation += 1
            generation = self.generation
            self.subscribed_symbol = None
            self.state = self._blank_state()
            self.state.update(
                {
                    "status": "connecting",
                    "symbol": symbol,
                    "feed": feed,
                }
            )
            self._seed(metrics)
            self.thread = threading.Thread(
                target=self._run_loop,
                args=(generation, feed),
                daemon=True,
                name=f"alpaca-stream-{feed}",
            )
            self.thread.start()
            return self._public_state()

    def _still_current(self, generation, feed):
        with self.lock:
            return generation == self.generation and feed == self.desired_feed

    def _run_loop(self, generation, feed):
        delay = 1.0
        while self._still_current(generation, feed):
            url = f"wss://stream.data.alpaca.markets/v2/{feed}"

            def on_open(wsapp):
                with self.lock:
                    if not self._still_current(generation, feed):
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
                self._handle_message(generation, feed, wsapp, raw)

            def on_error(_wsapp, error):
                with self.lock:
                    if self._still_current(generation, feed):
                        self.state["error"] = str(error)[:220]
                        if self.state.get("status") != "connection_limit":
                            self.state["status"] = "error"

            def on_close(_wsapp, _code, _msg):
                with self.lock:
                    if self._still_current(generation, feed):
                        self.state["connected"] = False
                        self.state["authenticated"] = False
                        if self.state.get("status") not in {"error", "connection_limit"}:
                            self.state["status"] = "reconnecting"

            app = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            with self.lock:
                if not self._still_current(generation, feed):
                    return
                self.ws = app

            try:
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                with self.lock:
                    if self._still_current(generation, feed):
                        self.state["error"] = str(exc)[:220]
                        if self.state.get("status") != "connection_limit":
                            self.state["status"] = "error"

            with self.lock:
                if not self._still_current(generation, feed):
                    return
                if self.state.get("status") == "connection_limit":
                    self.thread = None
                    self.ws = None
                    return

            time.sleep(delay)
            delay = min(15.0, delay * 1.8)

    def _handle_message(self, generation, feed, wsapp, raw):
        if not self._still_current(generation, feed):
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
                        target = self.desired_symbol
                    else:
                        target = None
                if target:
                    self._send_symbol_switch(wsapp, None, target)
                continue

            if kind == "error":
                code = msg.get("code")
                message = str(msg.get("msg") or msg)
                if code == 406 or "connection limit exceeded" in message.lower():
                    with self.lock:
                        self.blocked_until = time.time() + 60.0
                        self.state.update(
                            {
                                "status": "connection_limit",
                                "error": message[:220],
                                "connected": False,
                                "authenticated": False,
                                "last_message_at": now_ts,
                            }
                        )
                    try:
                        wsapp.close()
                    except Exception:
                        pass
                    return

                with self.lock:
                    self.state.update(
                        {
                            "status": "error",
                            "error": message[:220],
                            "last_message_at": now_ts,
                        }
                    )
                continue

            if kind == "subscription":
                with self.lock:
                    self.subscribed_symbol = self.desired_symbol
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

            msg_symbol = str(msg.get("S") or "").upper()
            with self.lock:
                target_symbol = self.desired_symbol
            if msg_symbol != target_symbol:
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


_REST_FALLBACK = {
    "symbol": None,
    "feed": None,
    "last_poll": 0.0,
    "payload": None,
    "error": None,
}


def _rest_snapshot(symbol, feed):
    now_ts = time.time()
    if (
        _REST_FALLBACK.get("symbol") == symbol
        and _REST_FALLBACK.get("feed") == feed
        and now_ts - float(_REST_FALLBACK.get("last_poll") or 0) < 5.0
    ):
        return _REST_FALLBACK.get("payload"), _REST_FALLBACK.get("error")

    params = urllib.parse.urlencode({"feed": feed})
    url = (
        "https://data.alpaca.markets/v2/stocks/"
        + urllib.parse.quote(symbol, safe="")
        + "/snapshot?"
        + params
    )
    req = urllib.request.Request(
        url,
        headers={
            "APCA-API-KEY-ID": API_KEY,
            "APCA-API-SECRET-KEY": API_SECRET,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        error = None
    except Exception as exc:
        payload = _REST_FALLBACK.get("payload")
        error = str(exc)[:180]

    _REST_FALLBACK.update(
        {
            "symbol": symbol,
            "feed": feed,
            "last_poll": now_ts,
            "payload": payload,
            "error": error,
        }
    )
    return payload, error


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

    # If Alpaca's single WebSocket connection is occupied elsewhere, keep
    # price/quote fresh with a low-frequency REST snapshot instead of leaving
    # the live tape dead.
    if state.get("status") == "connection_limit" and symbol:
        feed = str(state.get("feed") or metrics.get("live_feed") or "iex").lower()
        snap, rest_error = _rest_snapshot(symbol, feed)
        if isinstance(snap, dict):
            snap_trade = snap.get("latestTrade") or {}
            snap_quote = snap.get("latestQuote") or {}
            if snap_trade:
                trade = {
                    "price": _num(snap_trade.get("p")),
                    "size": _num(snap_trade.get("s")),
                    "timestamp": snap_trade.get("t"),
                    "exchange": snap_trade.get("x"),
                }
            if snap_quote:
                quote = {
                    "bid": _num(snap_quote.get("bp")),
                    "ask": _num(snap_quote.get("ap")),
                    "bid_size": _num(snap_quote.get("bs")),
                    "ask_size": _num(snap_quote.get("as")),
                    "timestamp": snap_quote.get("t"),
                }
            state = {
                **state,
                "status": "rest_fallback",
                "fallback_reason": "Alpaca WebSocket connection limit",
                "rest_error": rest_error,
                "message_age_seconds": round(
                    max(0.0, time.time() - float(_REST_FALLBACK.get("last_poll") or 0)),
                    2,
                ),
            }

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
    if bid and ask and ask >= bid:
        midpoint = (ask + bid) / 2.0
        if midpoint > 0:
            spread_pct = (ask - bid) / midpoint * 100.0

    now_ts = time.time()
    trade_dt = _parse_dt(trade.get("timestamp") or metrics.get("latest_trade_time"))
    quote_dt = _parse_dt(quote.get("timestamp") or metrics.get("latest_quote_time"))
    trade_age_seconds = (
        max(0.0, now_ts - trade_dt.timestamp()) if trade_dt is not None else None
    )
    quote_age_seconds = (
        max(0.0, now_ts - quote_dt.timestamp()) if quote_dt is not None else None
    )

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
        "trade_age_seconds": (
            round(trade_age_seconds, 2) if trade_age_seconds is not None else None
        ),
        "quote_age_seconds": (
            round(quote_age_seconds, 2) if quote_age_seconds is not None else None
        ),
        "vwap": round(vwap, 4) if vwap is not None else None,
        "vwap_position": vwap_position,
        "session_volume": round(volume) if volume is not None else None,
        "breakout_state": breakout,
        "entry_low": entry_low,
        "entry_high": entry_high,
    }
