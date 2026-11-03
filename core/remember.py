"""
remember.py — keep a signed-in user signed in across reloads and app restarts.

Why this exists
---------------
Streamlit's session state lives in server memory keyed to a websocket. Close
the tab, background the PWA long enough for the socket to drop, or let the
container recycle, and the state is gone — so the user was being returned to
the login screen constantly even though they had just signed in.

How it works
------------
A short-lived token carrying the user's identity is signed with HMAC-SHA256
using OAUTH_STATE_SECRET (the same secret the eBay OAuth state uses) and kept
in the browser's localStorage. On load, a small component copies it into the
URL and reloads once; the server verifies the signature, restores the session,
and immediately strips the token back out of the URL.

The token is refreshed on every script run, so its expiry is an *inactivity*
window: an active user never sees it lapse, and an idle one is signed out
IDLE_SECONDS after their last interaction.

Security notes
--------------
- The token is a bearer credential. It is signed, not encrypted, so it must
  never carry anything secret — identity fields only, no password, no eBay
  token. Those stay server-side, keyed by username.
- It passes through the URL for exactly one request and is removed before the
  page renders, which keeps it out of the address bar and out of anything the
  user is likely to copy and paste. It can still land in a proxy or server
  access log, which is why the window is five minutes rather than days.
- Tampering fails the HMAC check; an expired token fails the time check. Both
  land the user on the login screen, which is the safe direction to fail.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import streamlit as st
import streamlit.components.v1 as components

STORAGE_KEY = "ebayio_session"
PARAM = "s"

# Sign-out after this long without any interaction. The user asked for five
# minutes: long enough to answer a phone call or switch apps without losing
# the page, short enough that a shared or logged URL goes stale quickly.
IDLE_SECONDS = 300

# Identity only. Anything added here is visible to the user (base64 is not
# encryption) and is trusted by the server once the signature checks out, so
# this list stays deliberately short.
_FIELDS = ("role", "client_name", "username", "owner_name")


def _secret() -> bytes | None:
    try:
        value = st.secrets["OAUTH_STATE_SECRET"]
    except Exception:
        return None
    return str(value).encode("utf-8") if value else None


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: dict) -> str | None:
    secret = _secret()
    if not secret:
        return None
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify(token: str) -> dict | None:
    secret = _secret()
    if not secret or not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    # compare_digest, not ==, so a wrong signature cannot be guessed a byte
    # at a time by timing the comparison.
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if float(payload.get("exp", 0)) < time.time():
        return None
    return payload


def issue_token() -> str | None:
    """A fresh token for the currently signed-in user."""
    if not st.session_state.get("authenticated"):
        return None
    payload = {k: st.session_state.get(k) for k in _FIELDS}
    payload["exp"] = time.time() + IDLE_SECONDS
    return _sign(payload)


def restore_session() -> bool:
    """
    Verify a token arriving in the URL and sign the user back in.

    Returns True if a session was restored. Called before the login gate.
    """
    if st.session_state.get("authenticated"):
        return False

    try:
        token = st.query_params.get(PARAM)
    except Exception:
        return False
    if not token:
        return False

    payload = _verify(token)

    # The token has done its one job. Remove it from the URL whether or not it
    # checked out, so a stale or tampered value is not retried on every rerun
    # and does not sit in the address bar. localStorage remains the store.
    try:
        del st.query_params[PARAM]
    except Exception:
        pass

    if not payload:
        return False

    for key in _FIELDS:
        if payload.get(key) is not None:
            st.session_state[key] = payload[key]
    st.session_state["authenticated"] = True
    st.session_state.setdefault("active_page", "Dashboard")
    return True


def sync_browser(clear: bool = False) -> None:
    """
    Push the current token to localStorage, or pull one into the URL.

    Renders a zero-height component. Three cases:

    - clear=True — wipe the stored token (sign-out).
    - signed in — store a token refreshed to now + IDLE_SECONDS. Keeps the
      inactivity window rolling for as long as the user is doing things.
    - signed out — if a stored token exists, put it in the URL and reload
      once so the server can verify it.
    """
    # logout() cannot clear the token itself — its st.rerun() discards whatever
    # that run rendered — so it leaves a flag for the next run to honour.
    if st.session_state.pop("_forget_session", False):
        clear = True

    token = None if clear else issue_token()

    # Without a secret there is nothing to sign and nothing to verify, so the
    # feature stays off rather than failing open.
    if not clear and token is None and st.session_state.get("authenticated"):
        return

    payload = json.dumps(
        {
            "key": STORAGE_KEY,
            "param": PARAM,
            "token": token,
            "clear": bool(clear),
            "authed": bool(st.session_state.get("authenticated")),
        }
    )

    components.html(
        f"""
<script>
(function () {{
  var cfg = {payload};
  // Components run in an iframe. It is same-origin with the app, so the
  // parent's localStorage and URL are reachable; if a browser ever refuses,
  // the catch leaves the app working exactly as it did before.
  var win;
  try {{ win = window.parent; win.location.href; }} catch (e) {{ return; }}
  var store;
  try {{ store = win.localStorage; }} catch (e) {{ return; }}

  if (cfg.clear) {{
    try {{ store.removeItem(cfg.key); }} catch (e) {{}}
    return;
  }}

  if (cfg.authed && cfg.token) {{
    try {{ store.setItem(cfg.key, cfg.token); }} catch (e) {{}}
    return;
  }}

  // Not signed in. If the browser is holding a token, hand it to the server.
  var stored = null;
  try {{ stored = store.getItem(cfg.key); }} catch (e) {{}}
  if (!stored) return;

  // One attempt per token per tab. Without this guard an expired or
  // rejected token would reload the page forever, since the server strips
  // the parameter and the browser would put it straight back.
  var mark = 'ebayio_restore_tried';
  try {{
    if (win.sessionStorage.getItem(mark) === stored) return;
    win.sessionStorage.setItem(mark, stored);
  }} catch (e) {{ return; }}

  try {{
    var url = new URL(win.location.href);
    url.searchParams.set(cfg.param, stored);
    win.location.replace(url.toString());
  }} catch (e) {{}}
}})();
</script>
""",
        height=0,
    )


def forget() -> None:
    """Drop the stored token. Called on explicit sign-out."""
    sync_browser(clear=True)
