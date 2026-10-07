"""The desk's own Claude calls: structured JSON out, reports cached, cost tracked."""

from __future__ import annotations

import json

import anthropic

from desk import DeskError, maxplan, usage_meter
from desk.config import MODEL

# Opus 5 can decline a request on a safety classifier; server-side fallbacks
# re-run a declined request on Anthropic's recommended substitute instead of
# returning the refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# One system prompt for every desk call. Caching is a prefix match over
# system -> messages, so a role-specific system prompt would re-bill the ~20k
# tokens of cached reports on every change of role. Roles and rules go in the
# user turn, after the cached reports. The output schema is part of the prefix
# too, so each call type (horizon rating, debate turn, memo) writes the cache
# once per run and every later call of that type reads it.
# Count-neutral on purpose: a run selects two or three teams, and naming them here per run
# would make the cached prefix differ between runs for no gain.
DESK_SYSTEM = """You work on an investment desk that combines the reports of the research teams
selected for this run (TradingAgents, ai-hedge-fund and Edge Desk; a run may use any two or all
three) for a swing-to-long-term investor (weeks to years) who acts on the outcome with real
money. The teams share models and some data, so their agreement is not independent
confirmation. Use only the material provided: the reports included below, any debate
transcript, and the shared computed data. Do not use outside knowledge and do not invent
numbers. Write plainly, with no em dashes or en dashes. Respond with JSON only, in the schema
requested."""


def cached_text(text: str) -> dict:
    """A content block marked as the end of the cached prefix."""
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


class DeskLLM:
    """Max plan by default (desk/maxplan.py); DESK_LLM=api puts it back on the metered key."""

    def __init__(self, model: str = MODEL, client: anthropic.Anthropic | None = None) -> None:
        self.model = model
        self.max = maxplan.enabled() and client is None
        self._client = None if self.max else (client or anthropic.Anthropic())
        self.calls: list[dict] = []

    def json(self, blocks: list[dict], schema: dict, max_tokens: int = 16000,
             system: str = DESK_SYSTEM, model: str | None = None, effort: str | None = None) -> dict:
        if self.max:
            return self._json_max(blocks, schema, system, model or self.model, effort)
        # The per-call model and effort from desk/config.py CALLS, as on the Max path; this used to
        # send the default model for every call and drop the effort (audit R2-19).
        wanted = maxplan.resolve(model or self.model)
        output = {"format": {"type": "json_schema", "schema": schema}}
        if effort:
            output["effort"] = effort
        try:
            response = self._client.beta.messages.create(
                model=wanted,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": blocks}],
                output_config=output,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as exc:
            raise DeskError(f"Claude call failed: {exc}") from exc

        usage = response.usage
        self.calls.append({
            "model": response.model, "requested": wanted, "effort": effort,
            "input": usage.input_tokens or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "output": usage.output_tokens or 0,
        })
        if response.stop_reason == "refusal":
            raise DeskError("Claude declined this request, and no fallback model accepted it.")
        if response.stop_reason == "max_tokens":
            raise DeskError("Claude's answer was cut off at max_tokens.")
        body = next((b.text for b in response.content if b.type == "text"), None)
        if body is None:
            raise DeskError("Claude returned no text block.")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise DeskError(f"Claude returned invalid JSON: {exc}") from exc

    def _json_max(self, blocks: list[dict], schema: dict, system: str, model: str,
                  effort: str | None) -> dict:
        # `claude -p` sends the prompt as one message, which the cache can never match across
        # calls; the system prompt is cached. So the blocks marked for caching (the ~27k tokens
        # of reports, identical on every desk call) ride in the system prompt, and only the
        # role and question go in the message. Measured: a repeat call reads the reports from
        # the cache instead of re-sending them, about 10x less of the Max allowance.
        cached = [b["text"] for b in blocks if b.get("type") == "text" and b.get("cache_control")]
        fresh = [b["text"] for b in blocks if b.get("type") == "text" and not b.get("cache_control")]
        full_system = "\n\n".join([system, *(f"MATERIAL:\n{c}" for c in cached)])
        try:
            reply = maxplan.ask("\n\n".join(fresh), full_system, model=model, effort=effort, schema=schema)
        except maxplan.UsageLimit as exc:
            raise DeskError(f"The {'ChatGPT' if maxplan.plan() == 'chatgpt' else 'Max'} plan stopped this run "
                            f"(usage limit or login); try again once it resets or after logging in. ({exc})") from exc
        except maxplan.MaxCallError as exc:
            raise DeskError(f"Claude call failed: {exc}") from exc
        self.calls.append({"model": reply.model, "billing": "chatgpt" if maxplan.plan() == "chatgpt" else "max",
                           "notional_usd": reply.notional_usd,
                           **reply.usage})
        if not isinstance(reply.data, dict):
            raise DeskError("Claude returned no JSON object.")
        return reply.data

    def cost(self) -> float:
        """Dollars actually billed. Max-plan calls cost nothing; see notional() for list price."""
        if self.max:
            return 0.0
        return usage_meter.cost(self.calls)

    def notional(self) -> float:
        return round(sum(c.get("notional_usd") or 0 for c in self.calls), 4)
