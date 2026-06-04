"""Token + cost accounting for a run (#5).

The agent runner records one entry per model turn ``{agent, model,
input_tokens, output_tokens}``. ``summarize`` folds those into per-agent
and total token counts and a USD estimate from a model price table. Pure
function — no SDK, no network — so it is unit-testable in isolation.
"""

from __future__ import annotations


def _price_table(prices) -> dict:
    """``((model, in_per_mtok, out_per_mtok), …)`` -> lookup dict."""
    return {m: (float(pin), float(pout)) for m, pin, pout in prices}


def _cost(model: str, tin: int, tout: int, table: dict) -> float:
    pin, pout = table.get(model, (0.0, 0.0))
    return (tin * pin + tout * pout) / 1_000_000


def summarize(records: list, prices) -> dict:
    """Fold per-turn usage records into a per-agent + total report.

    ``records``: list of dicts with keys agent, model, input_tokens,
    output_tokens. ``prices``: iterable of (model, in$/Mtok, out$/Mtok).
    Unknown models cost 0 (and are listed so the gap is visible).
    """
    table = _price_table(prices)
    by_agent: dict[str, dict] = {}
    total_in = total_out = 0
    total_cost = 0.0
    unknown: set[str] = set()

    for r in records or []:
        agent = r.get("agent", "?")
        model = r.get("model", "?")
        tin = int(r.get("input_tokens", 0) or 0)
        tout = int(r.get("output_tokens", 0) or 0)
        if model not in table:
            unknown.add(model)
        c = _cost(model, tin, tout, table)
        a = by_agent.setdefault(
            agent, {"input_tokens": 0, "output_tokens": 0, "usd": 0.0,
                    "calls": 0})
        a["input_tokens"] += tin
        a["output_tokens"] += tout
        a["usd"] = round(a["usd"] + c, 6)
        a["calls"] += 1
        total_in += tin
        total_out += tout
        total_cost += c

    return {
        "by_agent": by_agent,
        "total": {"input_tokens": total_in, "output_tokens": total_out,
                  "usd": round(total_cost, 6)},
        "unknown_models": sorted(unknown),
    }
