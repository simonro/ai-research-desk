"""A LangChain chat model that runs on the Max plan, for TradingAgents.

TradingAgents is used unmodified. ta_runner swaps this in for its Anthropic client (it builds
both of its models through one factory function), so every agent in the graph talks to Claude
through `claude -p` (desk/maxplan.py) instead of the metered API.

The hard part is tools. TradingAgents' analysts call data tools mid-conversation (prices,
indicators, news, filings) through LangChain's `bind_tools`, and `claude -p` runs with its own
tools switched off. So tool use is carried in structured output instead:

  - with tools bound, the model answers with JSON validated against a schema: either
    {"action": "tool_calls", "tool_calls": [{"name", "arguments"}]} to ask for data, or
    {"action": "final", "content": "..."} with its report. The reply becomes an AIMessage
    carrying real LangChain tool_calls, so TradingAgents' ToolNode runs the tools exactly as
    it would for the API, and their results come back as ToolMessages in the next call;
  - when one tool is forced (how `with_structured_output` works: the Research Manager, Trader
    and Portfolio Manager), that tool's own parameter schema is the output schema, so the
    decision object is validated by Claude Code before it ever reaches TradingAgents.

Every call is stateless: the whole conversation so far is rendered into the prompt.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

from desk import maxplan

BASE_SYSTEM = ("You are one agent in a multi-agent equity research team. Follow the instructions "
               "in the conversation exactly. Write plainly.")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


def render(messages: Sequence[BaseMessage]) -> tuple[str, str]:
    """(system prompt, transcript) from a LangChain message list."""
    system = [BASE_SYSTEM]
    parts: list[str] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            system.append(_text(m.content))
        elif isinstance(m, HumanMessage):
            parts.append(f"=== USER ===\n{_text(m.content)}")
        elif isinstance(m, ToolMessage):
            parts.append(f"=== TOOL RESULT ({getattr(m, 'name', '') or 'tool'}, id {m.tool_call_id}) ===\n{_text(m.content)}")
        elif isinstance(m, AIMessage):
            body = _text(m.content)
            calls = "\n".join(f"- {c['name']}({json.dumps(c.get('args') or {})}) [id {c.get('id')}]"
                              for c in (m.tool_calls or []))
            parts.append("=== YOU (earlier turn) ===\n" + "\n".join(x for x in (body, calls and "Tool calls:\n" + calls) if x))
        else:
            parts.append(f"=== {m.type.upper()} ===\n{_text(m.content)}")
    return "\n\n".join(system), "\n\n".join(parts)


def _tool_line(t: dict) -> str:
    f = t["function"]
    return f"- {f['name']}: {f.get('description', '').strip()}\n  parameters: {json.dumps(f.get('parameters') or {})}"


class MaxChat(BaseChatModel):
    model: str = "claude-sonnet-5"
    effort: str | None = None
    timeout: int = maxplan.DEFAULT_TIMEOUT

    @property
    def _llm_type(self) -> str:
        return "claude-max-headless"

    @property
    def _identifying_params(self) -> dict:
        return {"model": self.model, "effort": self.effort}

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any):
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], tool_choice=tool_choice, **kwargs)

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        tools: list[dict] = kwargs.get("tools") or []
        choice = kwargs.get("tool_choice")
        system, transcript = render(messages)
        names = [t["function"]["name"] for t in tools]
        forced = None
        if tools:
            if isinstance(choice, str) and choice in names:
                forced = choice
            elif isinstance(choice, dict):
                forced = (choice.get("function") or {}).get("name") or choice.get("name")
            elif choice in ("any", "required", True) and len(tools) == 1:
                forced = names[0]

        if forced:
            tool = next(t for t in tools if t["function"]["name"] == forced)
            params = tool["function"].get("parameters") or {"type": "object"}
            prompt = (f"{transcript}\n\n=== YOUR TASK NOW ===\nRespond by filling in `{forced}`: "
                      f"{tool['function'].get('description', '').strip()} Your reply is validated "
                      f"against its parameter schema.")
            reply = maxplan.ask(prompt, system, self.model, self.effort, params, self.timeout)
            msg = AIMessage(content="", tool_calls=[{"name": forced, "args": reply.data or {},
                                                     "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}])
        elif tools:
            schema = {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["tool_calls", "final"]},
                    "tool_calls": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string", "enum": names}, "arguments": {"type": "object"}},
                        "required": ["name", "arguments"]}},
                    "content": {"type": "string"},
                },
                "required": ["action"],
            }
            prompt = (f"{transcript}\n\n=== TOOLS YOU CAN CALL ===\n" + "\n".join(_tool_line(t) for t in tools)
                      + "\n\n=== HOW TO ANSWER ===\nIf you still need data, set action to \"tool_calls\" and "
                        "list every call you want now (several at once is fine); their results come back "
                        "in the next turn. When you have what you need, set action to \"final\" and put "
                        "your complete answer, in full, in \"content\". Never invent tool results.")
            reply = maxplan.ask(prompt, system, self.model, self.effort, schema, self.timeout)
            data = reply.data or {}
            calls = [c for c in (data.get("tool_calls") or []) if c.get("name") in names]
            if data.get("action") == "tool_calls" and calls:
                msg = AIMessage(content="", tool_calls=[{"name": c["name"], "args": c.get("arguments") or {},
                                                         "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
                                                        for c in calls])
            else:
                msg = AIMessage(content=data.get("content") or reply.text)
        else:
            reply = maxplan.ask(f"{transcript}\n\n=== YOUR TURN ===\nReply now.", system, self.model,
                                self.effort, None, self.timeout)
            msg = AIMessage(content=reply.text)

        msg.response_metadata = {"model_name": reply.model, "billing": "max",
                                 "notional_usd": reply.notional_usd, "seconds": reply.seconds}
        msg.usage_metadata = {"input_tokens": reply.usage.get("input", 0) + reply.usage.get("cache_read", 0)
                              + reply.usage.get("cache_write", 0),
                              "output_tokens": reply.usage.get("output", 0),
                              "total_tokens": sum(reply.usage.values())}
        return ChatResult(generations=[ChatGeneration(message=msg)])


class MaxClient:
    """Stands in for TradingAgents' BaseLLMClient: only get_llm() is used by the graph."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs: Any) -> None:
        self.model = model
        self.effort = kwargs.get("effort")

    def get_llm(self) -> MaxChat:
        return MaxChat(model=self.model, effort=self.effort)
