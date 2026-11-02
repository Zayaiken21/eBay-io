"""
Transient relay for pages captured in the seller's own browser.

Why this exists
---------------
Sites behind enterprise bot management (Walmart, Home Depot, Amazon, Target)
refuse a server-side fetch. They do not refuse the seller's own browser. So
the browser captures the page HTML and hands it to the app, which runs the
normal extraction pipeline on it.

Streamlit has no route that can receive a POST, so the capture needs a drop
point. Supabase is used purely as that drop point.

Storage policy
--------------
Supabase holds the user database. It is NOT a store for captured pages, so a
capture here is strictly in-flight:

  * the row is deleted in the same call that reads it (claim-and-delete),
  * nothing is written back after the import succeeds,
  * anything older than CAPTURE_TTL_MINUTES is swept on the next read, so an
    abandoned capture cannot linger,
  * only the raw HTML and its source URL are carried, never an extracted
    product, a draft, or anything derived from one.

The extracted product goes wherever drafts already go. This table should be
empty almost all of the time; a row in it means a capture is mid-flight.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
import streamlit as st

# A capture is picked up within seconds. Anything older was abandoned.
CAPTURE_TTL_MINUTES = 15

_TABLE = "import_inbox"


def _supabase_url() -> str:
    return str(st.secrets.get("SUPABASE_URL", "")).rstrip("/")


def _supabase_key() -> str:
    return str(
        st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
        or st.secrets.get("SUPABASE_SECRET_KEY")
        or ""
    )


def _publishable_key() -> str:
    """
    The anon/publishable key — the ONLY key that may appear in a bookmarklet.

    A bookmarklet is readable by anyone who can see the bookmark, so it can
    only ever carry the public key, whose reach is bounded by row-level
    security (insert-only on this table). The service-role key bypasses RLS
    entirely and grants full access to every table, so it must never leave
    the server.
    """
    # Accept the names Supabase has used across dashboard versions, so a key
    # that is present under any of them is found rather than reported missing.
    return str(
        st.secrets.get("SUPABASE_ANON_KEY")
        or st.secrets.get("SUPABASE_PUBLISHABLE_KEY")
        or st.secrets.get("SUPABASE_PUBLIC_KEY")
        or st.secrets.get("SUPABASE_KEY")
        or st.secrets.get("SUPABASE_API_KEY")
        or st.secrets.get("ANON_KEY")
        or ""
    )


def is_configured() -> bool:
    return bool(_supabase_url() and _supabase_key())


def can_build_bookmarklet() -> bool:
    return bool(_supabase_url() and _publishable_key())


def secrets_diagnostic() -> list[str]:
    """
    Which Supabase-looking secret names exist, and what each one holds.

    Reports names and key ROLE only — never a key value. Exists because
    "no anon key" is indistinguishable from "the anon key is saved under a
    name the app does not read" without looking.
    """
    import base64
    import json as _json

    findings = []
    candidates = [
        "SUPABASE_ANON_KEY", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_PUBLIC_KEY",
        "SUPABASE_KEY", "SUPABASE_API_KEY", "ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY", "SUPABASE_URL",
    ]
    for name in candidates:
        try:
            raw = st.secrets.get(name)
        except Exception:
            raw = None
        if not raw:
            continue
        value = str(raw)

        if name == "SUPABASE_URL":
            findings.append(f"`{name}` — set")
            continue

        role = "unknown"
        if value.startswith("sb_publishable_"):
            role = "publishable (safe for the bookmarklet)"
        elif value.startswith("sb_secret_"):
            role = "secret — server only"
        else:
            # Decode the JWT payload to read its role claim. No signature
            # check: this only reports what the key says it is.
            try:
                payload = value.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claim = _json.loads(base64.urlsafe_b64decode(payload)).get("role", "")
                role = {
                    "anon": "anon (safe for the bookmarklet)",
                    "service_role": "service_role — server only, NEVER in a bookmarklet",
                }.get(claim, claim or "unknown")
            except Exception:
                pass
        findings.append(f"`{name}` — {role}")

    return findings or ["No Supabase secrets found at all."]


def _headers(prefer: str | None = None) -> dict[str, str]:
    key = _supabase_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _table_url() -> str:
    return f"{_supabase_url()}/rest/v1/{_TABLE}"


def capture_token(owner: str) -> str:
    """
    Per-user token embedded in the bookmarklet.

    Scopes a capture to one account so a token cannot drop pages into another
    seller's session. Derived once and kept with the user's own record rather
    than regenerated per call, which would invalidate an installed bookmarklet.
    """
    key = f"_capture_token_{owner}"
    token = st.session_state.get(key)
    if not token:
        token = secrets.token_urlsafe(24)
        st.session_state[key] = token
    return token


def _sweep_expired() -> None:
    """Delete anything past its TTL. Called before every read."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(minutes=CAPTURE_TTL_MINUTES)).isoformat()
    try:
        requests.delete(
            f"{_table_url()}?created_at=lt.{cutoff}",
            headers=_headers(prefer="return=minimal"),
            timeout=10,
        )
    except Exception:
        # A failed sweep must not block an import; the next read retries it.
        pass


def claim_capture(owner: str) -> dict[str, Any] | None:
    """
    Returns the newest pending capture for this owner and deletes it.

    Read and delete are a single claim: the row is removed whether or not the
    extraction that follows succeeds, so Supabase never ends up holding
    captured page content. If extraction fails the seller re-captures, which
    costs one tap.
    """
    if not is_configured():
        return None

    _sweep_expired()

    try:
        resp = requests.get(
            f"{_table_url()}?owner=eq.{owner}&order=created_at.desc&limit=1",
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        rows = resp.json()
        if not rows:
            return None

        row = rows[0]
    except Exception:
        return None

    # Delete immediately — the payload now lives only in this process.
    try:
        requests.delete(
            f"{_table_url()}?id=eq.{row.get('id')}",
            headers=_headers(prefer="return=minimal"),
            timeout=10,
        )
    except Exception:
        pass

    return {
        "html": row.get("html") or "",
        "source_url": row.get("source_url") or "",
        "created_at": row.get("created_at") or "",
    }


def pending_count(owner: str) -> int:
    """How many captures are waiting, without consuming them."""
    if not is_configured():
        return 0
    try:
        resp = requests.get(
            f"{_table_url()}?owner=eq.{owner}&select=id",
            headers=_headers(prefer="count=exact"),
            timeout=10,
        )
        if resp.status_code != 200:
            return 0
        return len(resp.json() or [])
    except Exception:
        return 0


def build_bookmarklet(owner: str, token: str) -> str:
    """
    The one-tap capture snippet.

    Sends the rendered DOM rather than the original source: on a client-side
    page the JSON-LD and data islands the parser needs are only present after
    scripts have run. This is the page the seller is already looking at, in a
    session they opened themselves — no automation of the site is involved.
    """
    url = _supabase_url()
    key = _publishable_key()

    # Hard stop. Earlier this fell back to _supabase_key(), which returns the
    # service-role key — embedding full, RLS-bypassing database access in a
    # bookmark. Refuse to emit anything rather than ever do that again.
    if not key:
        raise RuntimeError(
            "No SUPABASE_ANON_KEY in secrets. The bookmarklet can only carry "
            "the publishable key; the service-role key must never be put in "
            "one, since it bypasses row-level security."
        )
    if "service_role" in key:
        raise RuntimeError(
            "SUPABASE_ANON_KEY holds a service-role key. Use the anon / "
            "publishable key instead."
        )

    return (
        "javascript:(function(){"
        "try{"
        "var h=document.documentElement.outerHTML;"
        f"fetch('{url}/rest/v1/{_TABLE}',{{"
        "method:'POST',"
        "headers:{"
        f"'apikey':'{key}',"
        f"'Authorization':'Bearer {key}',"
        "'Content-Type':'application/json',"
        "'Prefer':'return=minimal'},"
        "body:JSON.stringify({"
        f"owner:'{owner}',token:'{token}',"
        "source_url:location.href,html:h})"
        "}).then(function(r){"
        "alert(r.ok?'Sent to eBay io - open the Products tab to import.'"
        ":'Capture failed: '+r.status);"
        "}).catch(function(e){alert('Capture failed: '+e);});"
        "}catch(e){alert('Capture failed: '+e);}"
        "})();"
    )


SCHEMA_SQL = f"""
-- Transient relay for browser-captured pages. NOT storage: rows are deleted
-- the moment the app reads them, and anything older than
-- {CAPTURE_TTL_MINUTES} minutes is swept on the next read.
create table if not exists public.{_TABLE} (
  id          uuid primary key default gen_random_uuid(),
  owner       text        not null,
  token       text        not null,
  source_url  text,
  html        text        not null,
  created_at  timestamptz not null default now()
);

create index if not exists {_TABLE}_owner_created_idx
  on public.{_TABLE} (owner, created_at desc);

alter table public.{_TABLE} enable row level security;

-- The bookmarklet may only insert. It can never read another seller's
-- capture, list the table, or delete from it. Reads and deletes happen in the
-- app with the service-role key.
create policy {_TABLE}_insert_only
  on public.{_TABLE}
  for insert
  to anon
  with check (true);

-- Belt and braces: drop stale rows server-side even if the app never runs.
-- Requires pg_cron; skip if unavailable.
-- select cron.schedule('sweep_{_TABLE}', '*/15 * * * *',
--   $$delete from public.{_TABLE}
--     where created_at < now() - interval '{CAPTURE_TTL_MINUTES} minutes'$$);
"""
