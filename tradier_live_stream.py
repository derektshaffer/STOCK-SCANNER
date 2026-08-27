import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    import websocket
except Exception:
    websocket = None


TRADIER_TOKEN = (
    os.environ.get("TRADIER_ACCESS_TOKEN", "").strip()
    or os.environ.get("TRADIER_TOKEN", "").strip()
)
SESSION_URL = "https://api.tradier.com/v1/markets/events/session"
WS_URL = "wss://ws.tradier.com/v1/markets/events"


def _num(value):
    try:
        return float(value)
    except Exception:
        return None


def _epoch_ms(value):
    try:
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        return raw
    except Exception:
        return None


def _parse_iso(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timestamp()
    except Exception:
        return None


def _create_session():
    if not TRADIER_TOKEN:
        raise RuntimeError("missing Tradier access token")
    req = urllib.request.Request(
        SESSION_URL,
        data=b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {TRADIER_TOKEN}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "stock-analyzer-tradier-stream/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    def find_sessionid(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if str(key).lower() in {"sessionid", "session_id"} and value:
                    return str(value)
            for value in obj.values():
                found = find_sessionid(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = find_sessionid(value)
                if found:
                    return found
        return None

    sessionid = find_sessionid(payload)
    if not sessionid:
        raise RuntimeError("Tradier created a stream session but returned no sessionid")
    return sessionid


class _TradierStream:
    def __init__(self):
        self.lock = threading.RLock()
        self.thread = None
        self.ws = None
        self.generation = 0
        self.symbol = None
        self.subscribed_symbol = None
        self.sessionid = None
        self.blocked_until = 0.0
        self.state = self._blank()

    def _blank(self):
        return {
            "status": "idle",
            "provider": "tradier",
            "feed": "TRADIER CONSOLIDATED",
            "symbol": None,
            "connected": False,
            "authenticated": False,
            "error": None,
            "last_message_at": None,
            "last_trade": None,
            "last_quote": None,
            "session_volume": None,
            "session_pv": None,
            "session_vwap": None,
            "seed_cutoff": None,
            "day_high": None,
            "day_low": None,
        }

    def _public(self):
        out = dict(self.state)
        out.pop("session_pv", None)
        last = out.get("last_message_at")
        out["message_age_seconds"] = (
            round(max(0.0, time.time() - last), 2) if last else None
        )
        return out

    def _seed(self, metrics):
        if not isinstance(metrics, dict):
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
        self.state["seed_cutoff"] = _parse_iso(metrics.get("as_of")) or time.time()

    def get(self, symbol=None):
        with self.lock:
            out = self._public()
        if symbol and str(out.get("symbol") or "").upper() != str(symbol).upper():
            out["status"] = "switching"
            out["symbol"] = str(symbol).upper()
        return out

    def _subscription_payload(self, symbol):
        return json.dumps(
            {
                "symbols": [symbol],
                "filter": ["trade", "quote", "summary", "timesale"],
                "sessionid": self.sessionid,
                "linebreak": True,
                "validOnly": True,
            }
        )

    def _switch_symbol(self, symbol):
        ws = self.ws
        if ws is None or not self.sessionid:
            return
        try:
            ws.send(self._subscription_payload(symbol))
            with self.lock:
                self.subscribed_symbol = symbol
                self.state["status"] = "subscribing"
        except Exception as exc:
            with self.lock:
                self.state["status"] = "error"
                self.state["error"] = str(exc)[:220]

    def ensure(self, symbol, metrics=None):
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            return self.get()
        if websocket is None:
            with self.lock:
                self.state.update(
                    {
                        "status": "disabled",
                        "symbol": symbol,
                        "error": "websocket-client is not installed",
                    }
                )
            return self.get(symbol)
        if not TRADIER_TOKEN:
            with self.lock:
                self.state.update(
                    {
                        "status": "disabled",
                        "symbol": symbol,
                        "error": "missing Tradier access token",
                    }
                )
            return self.get(symbol)

        should_switch = False
        with self.lock:
            alive = self.thread is not None and self.thread.is_alive()
            if alive:
                if self.symbol != symbol:
                    self.symbol = symbol
                    connected = bool(self.state.get("connected"))
                    old = self.state
                    self.state = self._blank()
                    self.state.update(
                        {
                            "status": "switching" if connected else old.get("status", "connecting"),
                            "symbol": symbol,
                            "connected": connected,
                            "authenticated": connected,
                            "error": None,
                        }
                    )
                    self._seed(metrics)
                    should_switch = connected and self.ws is not None and self.sessionid is not None
                elif metrics and self.state.get("seed_cutoff") is None:
                    self._seed(metrics)
                current = self._public()
            else:
                current = None

        if should_switch:
            self._switch_symbol(symbol)
            return self.get(symbol)
        if current is not None:
            return current

        with self.lock:
            if self.blocked_until > time.time():
                self.state.update(
                    {
                        "status": "session_limit",
                        "symbol": symbol,
                        "connected": False,
                        "error": "Tradier market stream session already in use",
                    }
                )
                self._seed(metrics)
                return self._public()

            self.generation += 1
            generation = self.generation
            self.symbol = symbol
            self.subscribed_symbol = None
            self.sessionid = None
            self.state = self._blank()
            self.state.update({"status": "connecting", "symbol": symbol})
            self._seed(metrics)
            self.thread = threading.Thread(
                target=self._run,
                args=(generation,),
                daemon=True,
                name="tradier-market-stream",
            )
            self.thread.start()
            return self._public()

    def _current(self, generation):
        with self.lock:
            return generation == self.generation

    def _run(self, generation):
        try:
            sessionid = _create_session()
        except Exception as exc:
            message = str(exc)
            with self.lock:
                if self._current(generation):
                    if "session" in message.lower() and "use" in message.lower():
                        self.blocked_until = time.time() + 60
                        self.state["status"] = "session_limit"
                    else:
                        self.state["status"] = "error"
                    self.state["error"] = message[:220]
                    self.thread = None
            return

        with self.lock:
            if not self._current(generation):
                return
            self.sessionid = sessionid
            self.state["status"] = "connecting"

        def on_open(wsapp):
            with self.lock:
                if not self._current(generation):
                    return
                self.state.update(
                    {
                        "status": "subscribing",
                        "connected": True,
                        "authenticated": True,
                        "error": None,
                    }
                )
                symbol = self.symbol
            if symbol:
                self._switch_symbol(symbol)

        def on_message(_wsapp, raw):
            self._handle_message(generation, raw)

        def on_error(_wsapp, error):
            with self.lock:
                if self._current(generation):
                    self.state["status"] = "error"
                    self.state["error"] = str(error)[:220]

        def on_close(_wsapp, _code, _msg):
            with self.lock:
                if self._current(generation):
                    self.state["connected"] = False
                    self.state["authenticated"] = False
                    if self.state.get("status") != "error":
                        self.state["status"] = "reconnecting"

        app = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        with self.lock:
            if not self._current(generation):
                return
            self.ws = app

        try:
            app.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as exc:
            with self.lock:
                if self._current(generation):
                    self.state["status"] = "error"
                    self.state["error"] = str(exc)[:220]

        with self.lock:
            if self._current(generation):
                self.thread = None
                self.ws = None

    def _handle_message(self, generation, raw):
        if not self._current(generation):
            return

        chunks = []
        if isinstance(raw, str):
            chunks = [part.strip() for part in raw.splitlines() if part.strip()]
        else:
            chunks = [raw]

        for chunk in chunks:
            try:
                msg = json.loads(chunk) if isinstance(chunk, str) else chunk
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("error"):
                with self.lock:
                    self.state["status"] = "error"
                    self.state["error"] = str(msg.get("error"))[:220]
                continue

            symbol = str(msg.get("symbol") or "").upper().strip()
            with self.lock:
                target = self.symbol
            if symbol and symbol != target:
                continue

            kind = str(msg.get("type") or "").lower()
            now_ts = time.time()

            with self.lock:
                self.state["last_message_at"] = now_ts
                self.state["status"] = "streaming"
                self.state["connected"] = True
                self.state["authenticated"] = True
                self.state["error"] = None

                if kind in {"trade", "tradex"}:
                    price = _num(msg.get("price"))
                    if price is None:
                        price = _num(msg.get("last"))
                    self.state["last_trade"] = {
                        "price": price,
                        "size": _num(msg.get("size")),
                        "timestamp": msg.get("date"),
                        "exchange": msg.get("exch"),
                    }
                    cvol = _num(msg.get("cvol"))
                    if cvol is not None:
                        self.state["session_volume"] = cvol
                    self._accumulate_trade(msg, price)

                elif kind == "quote":
                    self.state["last_quote"] = {
                        "bid": _num(msg.get("bid")),
                        "ask": _num(msg.get("ask")),
                        "bid_size": _num(msg.get("bidsz")),
                        "ask_size": _num(msg.get("asksz")),
                        "timestamp": msg.get("askdate") or msg.get("biddate"),
                    }

                elif kind == "timesale":
                    price = _num(msg.get("last"))
                    if price is None:
                        price = _num(msg.get("price"))
                    self.state["last_trade"] = {
                        "price": price,
                        "size": _num(msg.get("size")),
                        "timestamp": msg.get("date"),
                        "exchange": msg.get("exch"),
                    }
                    self._accumulate_trade(msg, price)

                elif kind == "summary":
                    high = _num(msg.get("high"))
                    low = _num(msg.get("low"))
                    if high is not None:
                        self.state["day_high"] = high
                    if low is not None:
                        self.state["day_low"] = low

    def _accumulate_trade(self, msg, price):
        price = _num(price)
        size = _num(msg.get("size"))
        ts = _epoch_ms(msg.get("date"))
        cutoff = _num(self.state.get("seed_cutoff"))
        if price is None or size is None or size <= 0:
            return
        if ts is not None and cutoff is not None and ts <= cutoff:
            return

        volume = _num(self.state.get("session_volume")) or 0.0
        pv = _num(self.state.get("session_pv")) or 0.0

        # When cvol is present, session_volume is already authoritative. Avoid
        # double-adding size; only add size for events without cumulative volume.
        if _num(msg.get("cvol")) is None:
            volume += size
            self.state["session_volume"] = volume
        pv += price * size
        self.state["session_pv"] = pv
        if volume > 0:
            self.state["session_vwap"] = pv / volume

        high = _num(self.state.get("day_high"))
        low = _num(self.state.get("day_low"))
        self.state["day_high"] = price if high is None else max(high, price)
        self.state["day_low"] = price if low is None else min(low, price)


_STREAM = _TradierStream()


def ensure_live_stream(symbol, metrics=None):
    return _STREAM.ensure(symbol, metrics=metrics)


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
        mid = (bid + ask) / 2.0
        if mid > 0:
            spread_pct = (ask - bid) / mid * 100.0

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
