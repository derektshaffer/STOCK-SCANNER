"""Request-time Analyzer configuration. Never logs or persists credentials."""
import hashlib
import os


def alpaca_credentials(environ=None):
    env = os.environ if environ is None else environ
    return tuple(str(env.get(k) or '').strip() for k in ('ALPACA_API_KEY', 'ALPACA_SECRET_KEY'))


def historical_feed(default='sip', *, environ=None):
    env = os.environ if environ is None else environ
    feed = str(env.get('ALPACA_HISTORICAL_FEED') or default).strip().lower()
    return feed if feed in ('sip', 'iex') else 'sip'


def history_identity():
    # Opaque cache isolation across credential rotation and feed changes.
    key, secret = alpaca_credentials()
    value = '\0'.join(('alpaca', key, secret, historical_feed(), os.environ.get('ALPACA_LIVE_FEED', 'iex')))
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def preload_alpaca_pair(secrets, environ=None):
    """An explicitly configured pair replaces both halves, including blanks."""
    env = os.environ if environ is None else environ
    names = ('ALPACA_API_KEY', 'ALPACA_SECRET_KEY')
    if any(k in secrets for k in names):
        for k in names:
            env[k] = str(secrets.get(k) or '').strip()


def configured_alpaca_pair(secrets, environ=None):
    names = ('ALPACA_API_KEY', 'ALPACA_SECRET_KEY')
    return alpaca_credentials(secrets if any(k in secrets for k in names) else environ)
