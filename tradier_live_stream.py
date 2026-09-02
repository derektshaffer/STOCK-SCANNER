import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from live_price_quality import (
    MAX_LIVE_PRICE_AGE_SECONDS,
    select_freshest_live_price,
)

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


def _is_session_limit_error(value, code=None):
    text = str(value or "").lower()
    return bool(
        code == 1007
        or "too many session" in text
        or "session already in use" in text
        or ("session" in text and "limit" in text)
    )


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
            "last_trade_at": None,
            "last_quote_at": None,
            "last_trade": None,
            "last_quote": None,
            "session_volume": None,
            "vwap_volume": None,
            "session_pv": None,
            "session_vwap": None,
            "seed_cutoff": None,
            "seed_provider": None,
            "day_high": None,
            "day_low": None,
        }

    def _public(self):
        out = dict(self.state)
        out.pop("session_pv", None)
        out.pop("vwap_volume", None)
        now_ts = time.time()
        last = out.get("last_message_at")
        trade_at = out.get("last_trade_at")
        quote_at = out.get("last_quote_at")
        out["message_age_seconds"] = (
            round(max(0.0, now_ts - last), 2) if last else None
        )
        out["trade_age_seconds"] = (
            round(max(0.0, now_ts - trade_at), 2) if trade_at else None
        )
        out["quote_age_seconds"] = (
            round(max(0.0, now_ts - quote_at), 2) if quote_at else None
        )
        return out

    def _seed(self, metrics):
        """Seed live calculations only from Tradier-origin Analyzer metrics.

        If the deep Analyzer temporarily falls back to Alpaca, the Tradier
        socket starts unseeded rather than silently mixing providers.
        """
        if not isinstance(metrics, dict):
            return
        provider = str(
            metrics.get("market_provider")
            or metrics.get("live_provider")
            or ""
        ).lower()
        feed = str(metrics.get("live_feed") or "").upper()
        if provider != "tradier" and "TRADIER" not in feed:
            return

        volume = _num(metrics.get("session_volume"))
        if volume is None:
            volume = _num(metrics.get("volume"))
        vwap = _num(metrics.get("vwap"))

        self.state["session_volume"] = volume
        self.state["vwap_volume"] = volume
        self.state["session_pv"] = (
            volume * vwap if volume is not None and vwap is not None else None
        )
        self.state["session_vwap"] = vwap
        self.state["day_high"] = _num(metrics.get("day_high"))
        self.state["day_low"] = _num(metrics.get("day_low"))
        self.state["seed_cutoff"] = _parse_iso(metrics.get("as_of")) or time.time()
        self.state["seed_provider"] = "tradier"

        trade_at = _parse_iso(metrics.get("latest_trade_time"))
        quote_at = _parse_iso(metrics.get("latest_quote_time"))
        if trade_at:
            self.state["last_trade_at"] = trade_at
        if quote_at:
            self.state["last_quote_at"] = quote_at

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
                    if _is_session_limit_error(message):
                        self.blocked_until = time.time() + 120
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
            message = str(error or "")
            with self.lock:
                if self._current(generation):
                    if _is_session_limit_error(message):
                        self.blocked_until = time.time() + 120
                        self.state["status"] = "session_limit"
                    else:
                        self.state["status"] = "error"
                    self.state["error"] = message[:220]

        def on_close(_wsapp, _code, _msg):
            message = str(_msg or "")
            with self.lock:
                if self._current(generation):
                    self.state["connected"] = False
                    self.state["authenticated"] = False
                    if _is_session_limit_error(message, _code):
                        # Tradier may close an otherwise-created websocket with
                        # code 1007 / "too many sessions requested". Treat that
                        # as a provider session-limit state and cool down rather
                        # than immediately opening another socket on the next
                        # Streamlit rerun.
                        self.blocked_until = time.time() + 120
                        self.state["status"] = "session_limit"
                        self.state["error"] = (
                            f"Connection closed (code {_code}): {message}"
                        )[:220]
                    elif self.state.get("status") not in {"error", "session_limit"}:
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
                message = str(msg.get("error"))
                with self.lock:
                    if _is_session_limit_error(message):
                        self.blocked_until = time.time() + 120
                        self.state["status"] = "session_limit"
                    else:
                        self.state["status"] = "error"
                    self.state["error"] = message[:220]
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
                    trade_at = _epoch_ms(msg.get("date")) or now_ts
                    self.state["last_trade_at"] = trade_at
                    self.state["last_trade"] = {
                        "price": price,
                        "size": _num(msg.get("size")),
                        "timestamp": msg.get("date"),
                        "exchange": msg.get("exch"),
                    }
                    self._accumulate_trade(msg, price)

                elif kind == "quote":
                    quote_times = [
                        _epoch_ms(msg.get("askdate")),
                        _epoch_ms(msg.get("biddate")),
                        _epoch_ms(msg.get("date")),
                    ]
                    quote_times = [value for value in quote_times if value is not None]
                    self.state["last_quote_at"] = max(quote_times) if quote_times else now_ts
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
                    trade_at = _epoch_ms(msg.get("date")) or now_ts
                    self.state["last_trade_at"] = trade_at
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

        # Session volume may use Tradier's authoritative cumulative volume.
        # VWAP uses its own denominator so a cvol jump cannot dilute PV with
        # trades the socket never actually accumulated.
        session_volume = _num(self.state.get("session_volume")) or 0.0
        cvol = _num(msg.get("cvol"))
        if cvol is not None:
            self.state["session_volume"] = max(session_volume, cvol)
        else:
            self.state["session_volume"] = session_volume + size

        vwap_volume = _num(self.state.get("vwap_volume")) or 0.0
        pv = _num(self.state.get("session_pv")) or 0.0
        vwap_volume += size
        pv += price * size
        self.state["vwap_volume"] = vwap_volume
        self.state["session_pv"] = pv
        if vwap_volume > 0:
            self.state["session_vwap"] = pv / vwap_volume

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

    metrics_provider = str(
        metrics.get("market_provider")
        or metrics.get("live_provider")
        or ""
    ).lower()
    metrics_are_tradier = (
        metrics_provider == "tradier"
        or "TRADIER" in str(metrics.get("live_feed") or "").upper()
    )

    state_symbol = str(state.get("symbol") or "").upper().strip()
    now_utc = datetime.now(timezone.utc)
    stream_bid = _num(quote.get("bid"))
    stream_ask = _num(quote.get("ask"))
    stream_mid = (
        (stream_bid + stream_ask) / 2.0
        if stream_bid is not None
        and stream_ask is not None
        and stream_bid > 0
        and stream_ask >= stream_bid
        else None
    )
    metric_bid = None
    metric_ask = None
    candidates = [
        {
            "symbol": state_symbol,
            "price": trade.get("price"),
            "timestamp": trade.get("timestamp"),
            "source": "tradier_stream_trade",
            "kind": "trade",
        },
        {
            "symbol": state_symbol,
            "price": stream_mid,
            "timestamp": quote.get("timestamp"),
            "source": "tradier_stream_quote_midpoint",
            "kind": "quote_midpoint",
        },
    ]
    if metrics_are_tradier:
        metric_price_source = str(metrics.get("live_price_source") or "")
        metric_price_kind = (
            "quote_midpoint" if "quote" in metric_price_source.lower()
            else "bar" if "bar" in metric_price_source.lower()
            else "trade"
        )
        candidates.append({
            "symbol":metrics.get("symbol"),
            "price":metrics.get("price"),
            "timestamp":metrics.get("live_price_timestamp") or metrics.get("latest_trade_time"),
            "source":metric_price_source or "tradier_analyzer_snapshot",
            "kind":metric_price_kind,
        })
        metric_bid = _num(metrics.get("bid"))
        metric_ask = _num(metrics.get("ask"))
        candidates.append({
            "symbol": metrics.get("symbol"),
            "price": (
                (metric_bid + metric_ask) / 2.0
                if metric_bid is not None
                and metric_ask is not None
                and metric_bid > 0
                and metric_ask >= metric_bid
                else None
            ),
            "timestamp": metrics.get("latest_quote_time"),
            "source": "tradier_analyzer_quote_midpoint",
            "kind": "quote_midpoint",
        })
    selected_price, price_rejections = select_freshest_live_price(
        symbol,
        candidates,
        now=now_utc,
        max_age_seconds=MAX_LIVE_PRICE_AGE_SECONDS,
    )
    price = (selected_price or {}).get("price")

    trade_selected, _ = select_freshest_live_price(
        symbol,
        [row for row in candidates if row.get("kind") in {"trade", "bar"}],
        now=now_utc,
        max_age_seconds=MAX_LIVE_PRICE_AGE_SECONDS,
    )
    quote_selected, _ = select_freshest_live_price(
        symbol,
        [row for row in candidates if row.get("kind") == "quote_midpoint"],
        now=now_utc,
        max_age_seconds=MAX_LIVE_PRICE_AGE_SECONDS,
    )
    if quote_selected and quote_selected.get("source") == "tradier_stream_quote_midpoint":
        bid, ask = stream_bid, stream_ask
    elif quote_selected and quote_selected.get("source") == "tradier_analyzer_quote_midpoint":
        bid, ask = metric_bid, metric_ask
    else:
        bid, ask = None, None

    spread_pct = None
    if bid and ask and ask > 0:
        mid = (bid + ask) / 2.0
        if mid > 0:
            spread_pct = (ask - bid) / mid * 100.0

    vwap = _num(state.get("session_vwap"))
    if vwap is None and metrics_are_tradier:
        vwap = _num(metrics.get("vwap"))

    volume = _num(state.get("session_volume"))
    if volume is None and metrics_are_tradier:
        volume = _num(metrics.get("session_volume"))
    if volume is None and metrics_are_tradier:
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
        "live_price_available": selected_price is not None,
        "live_price_source": (selected_price or {}).get("source"),
        "live_price_timestamp": (selected_price or {}).get("timestamp"),
        "live_price_age_seconds": (selected_price or {}).get("age_seconds"),
        "live_price_rejections": price_rejections[:6],
        "trade_age_seconds": (trade_selected or {}).get("age_seconds"),
        "quote_age_seconds": (quote_selected or {}).get("age_seconds"),
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
