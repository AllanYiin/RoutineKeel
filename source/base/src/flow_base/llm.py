"""Bounded text-only review with durable reservations and no automatic retries."""
from __future__ import annotations

import difflib
import json
import math
import os
import time
import urllib.request
import uuid
from decimal import Decimal, ROUND_CEILING

from .contracts import LLM
from .runtime import FlowError, canonical, digest


def money(tokens, rate):
    # rate is USD / million tokens; result is micro-USD.
    return int((Decimal(tokens) * Decimal(str(rate))).to_integral_value(rounding=ROUND_CEILING))


class Ledger:
    def __init__(self, runtime, job, settings: LLM):
        self.runtime, self.job, self.settings = runtime, job, settings
        runtime.initialize()

    def lookup(self, key):
        with self.runtime.connect() as db:
            row = db.execute("SELECT * FROM requests WHERE job=? AND cache_key=? ORDER BY created DESC LIMIT 1", (self.job, key)).fetchone()
        return dict(row) if row else None

    def summary(self):
        with self.runtime.connect() as db:
            row = db.execute("SELECT COUNT(*) calls, COALESCE(SUM(COALESCE(charged,reserved)),0) committed, SUM(CASE WHEN status='reserved' THEN 1 ELSE 0 END) uncertain FROM requests WHERE job=?", (self.job,)).fetchone()
        return {"calls": row["calls"], "committed_usd": row["committed"] / 1000000, "uncertain_requests": row["uncertain"] or 0,
                "budget_usd": self.settings.budget_usd, "budget_kind": "conservative_estimate_with_reservations"}

    def reserve(self, key, input_bound):
        settings = self.settings
        reserve = money(input_bound, settings.input_usd_per_million) + money(settings.max_output_tokens, settings.output_usd_per_million)
        budget = int(Decimal(str(settings.budget_usd)) * 1000000)
        with self.runtime.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            used, calls = db.execute("SELECT COALESCE(SUM(COALESCE(charged,reserved)),0),COUNT(*) FROM requests WHERE job=?", (self.job,)).fetchone()
            if used + reserve > budget or calls >= settings.max_calls:
                raise FlowError("BUDGET_EXHAUSTED", "LLM budget/call limit reached; original subtitles are preserved")
            request_id = str(uuid.uuid4())
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)", (request_id, self.job, key, reserve, None, "reserved", None, time.time(),))
        from .presentation import ACTIVE
        reporter=ACTIVE.get()
        if reporter:reporter.token_receipt(request_id,pending=True)
        return request_id

    def settle(self, request_id, response):
        usage = response.get("usage") or {}
        ins, outs = usage.get("prompt_tokens"), usage.get("completion_tokens")
        charged = None
        if isinstance(ins, int) and isinstance(outs, int) and ins >= 0 and outs >= 0:
            charged = money(ins, self.settings.input_usd_per_million) + money(outs, self.settings.output_usd_per_million)
        with self.runtime.connect() as db:
            db.execute("UPDATE requests SET charged=?,status='received',response=? WHERE id=?",
                       (charged, canonical(response), request_id))
        from .presentation import ACTIVE
        reporter=ACTIVE.get()
        if reporter:reporter.token_receipt(request_id,usage)
        return charged


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an API key to a different destination.
        return None


def http_call(settings, payload):
    key = os.environ.get(settings.api_key_env)
    if not key:
        raise FlowError("API_KEY_MISSING", "Set the API-key environment variable", variable=settings.api_key_env)
    request = urllib.request.Request(settings.endpoint, data=canonical(payload).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=settings.timeout_seconds) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise FlowError("LLM_RESPONSE_TOO_LARGE", "Provider response exceeded limit")
    return json.loads(raw)


