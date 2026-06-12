"""
Base agent class for the multi-agent bug localization system.
Provides common LLM interaction, tool execution, and structured output.
"""

import inspect
import json
import logging
import os
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from config import config

logger = logging.getLogger(__name__)

# Max characters returned from any single tool call that will be sent into the
# LLM message history.  Keeping this small prevents quadratic prompt-token growth
# across many iterations.  ~8 000 chars ≈ ~2 000 tokens.
MAX_TOOL_OUTPUT_CHARS: int = int(os.environ.get("MAX_TOOL_OUTPUT_CHARS", "8000"))

# Maximum number of messages kept in the running history (system prompt + N most recent).
# Prevents quadratic prompt-token growth over many iterations.  Set to 0 to disable.
MAX_MSG_HISTORY: int = int(os.environ.get("MAX_MSG_HISTORY", "20"))

# Maximum total characters across ALL tool results appended in a single iteration.
# With N parallel tool calls each returning MAX_TOOL_OUTPUT_CHARS, the combined
# output can dwarf the effective context window.  Results that push past this
# budget are truncated more aggressively before being appended.
# Default: 2× MAX_TOOL_OUTPUT_CHARS — enough for 2 full-size or 4 half-size results.
MAX_ITER_TOOL_CHARS: int = int(os.environ.get("MAX_ITER_TOOL_CHARS", str(MAX_TOOL_OUTPUT_CHARS * 2)))

# Maximum total characters across all messages sent to the LLM in one call.
# Prevents context-window overflow even when individual messages are large.
# ~200 000 chars ≈ 50 000 tokens (rough 4-char/token estimate).
MAX_CONTEXT_CHARS: int = int(os.environ.get("MAX_CONTEXT_CHARS", "200000"))

# Shared LLM client singleton — avoids creating separate connections per agent
_shared_clients: dict[str, OpenAI] = {}


@dataclass
class AgentContext:
    """Shared context passed between agents."""
    # Bug report info
    instance_id: str = ""
    repo_id: str = ""  # Identifier used for RAG filtering (e.g., "Chart_3")
    problem_statement: str = ""
    repo_path: str = ""

    # Preprocessed info
    error_messages: list = field(default_factory=list)
    stack_traces: list = field(default_factory=list)
    mentioned_files: list = field(default_factory=list)
    mentioned_functions: list = field(default_factory=list)
    keywords: list = field(default_factory=list)

    # Results from previous agents
    fault_hypothesis: str = ""
    candidate_files: list = field(default_factory=list)
    candidate_methods: list = field(default_factory=list)
    repo_skeleton: str = ""
    reflection_feedback: str = ""
    # Agent trace / memory
    agent_traces: list = field(default_factory=list)

    # Repository specifics
    language: str = "python"
    file_extension: str = "*.py"

    # RAG retriever (if available)
    retriever: Any = None

    # Graph RAG retriever (if available)
    graph_retriever: Any = None

    # Optional per-invocation temperature override (avoids mutating global config)
    temperature_override: float | None = None

    # High-priority files extracted directly from stack traces (subset of mentioned_files)
    stack_trace_files: list[str] = field(default_factory=list)

    # Drain3 log parse result — structured templates, assertion failures, search terms
    log_parse_result: Any = None  # tools.log_parser.LogParseResult (lazy to avoid circular import)

    # Source root hint (e.g. "source" for old Ant-layout Chart, "src/main/java" for Maven)
    source_root: str = ""

    # Test-class-derived candidate files (e.g. from "WeekTests" -> "Week.java")
    test_derived_candidates: list[str] = field(default_factory=list)

    # Structured bug info extracted by ComprehensionAgent pre-step (BugCerberus-style)
    structured_bug_info: dict = field(default_factory=dict)

    def add_trace(self, agent_name: str, action: str, result: str):
        """Add an entry to the agent trace."""
        self.agent_traces.append({
            "agent": agent_name,
            "action": action,
            "result": result[:500],  # Truncate long results
        })


@dataclass
class AgentResult:
    """Result from an agent's execution."""
    agent_name: str
    success: bool = False
    output: dict = field(default_factory=dict)
    explanation: str = ""
    error: str = ""
    num_llm_calls: int = 0
    num_tool_calls: int = 0
    # Token usage (prompt / completion / total)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class BaseAgent(ABC):
    """Abstract base agent with LLM interaction and tool execution."""

    def __init__(self, name: str, tools: dict[str, callable] = None):
        self.name = name
        self.tools = tools or {}
        self.tool_schemas = []
        self._client = None

    @property
    def client(self) -> OpenAI:
        """Lazy-load shared LLM client. Reuses connections across agents."""
        if self._client is None:
            provider = config.llm.provider.lower()

            if provider not in _shared_clients:
                if provider == "gemini":
                    _shared_clients[provider] = OpenAI(
                        api_key=config.llm.gemini_api_key,
                        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                    )
                    logger.info(f"Using Gemini provider (model={config.llm.model})")
                elif provider == "openai":
                    kwargs = {"api_key": config.llm.api_key}
                    if config.llm.api_base:
                        kwargs["base_url"] = config.llm.api_base
                    _shared_clients[provider] = OpenAI(**kwargs)
                    logger.info(f"Using OpenAI provider (model={config.llm.model})")
                else:
                    kwargs = {"api_key": config.llm.api_key or "no-key"}
                    if config.llm.api_base:
                        kwargs["base_url"] = config.llm.api_base
                    _shared_clients[provider] = OpenAI(**kwargs)
                    logger.info(f"Using custom provider '{provider}' (model={config.llm.model})")

            self._client = _shared_clients[provider]

        return self._client

    @abstractmethod
    def get_system_prompt(self, context: AgentContext) -> str:
        """Return the system prompt for this agent."""
        pass

    @abstractmethod
    def get_initial_message(self, context: AgentContext) -> str:
        """Return the initial user message to start the agent's task."""
        pass

    def register_tool(self, name: str, func: callable, schema: dict):
        """Register a tool the agent can use."""
        self.tools[name] = func
        
        # Prevent duplication in tool_schemas
        self.tool_schemas = [
            s for s in self.tool_schemas 
            if s.get("function", {}).get("name") != schema.get("name", name)
        ]
        
        self.tool_schemas.append({
            "type": "function",
            "function": schema,
        })

    def run(self, context: AgentContext, max_iterations: int = None) -> AgentResult:
        """
        Run the agent with the given context.
        Uses an agentic loop: LLM → Tool Call → LLM → ... → Final Answer.
        """
        if max_iterations is None:
            max_iterations = config.max_agent_iterations

        result = AgentResult(agent_name=self.name)

        messages = [
            {"role": "system", "content": self.get_system_prompt(context)},
            {"role": "user", "content": self.get_initial_message(context)},
        ]

        logger.info(f"[{self.name}] Starting agent loop (max {max_iterations} iterations)")

        for iteration in range(max_iterations):
            logger.info(f"[{self.name}] Iteration {iteration + 1}/{max_iterations}")

            # Call LLM
            try:
                response = self._call_llm(messages, context)
                result.num_llm_calls += 1
                # Accumulate token usage (may be None for some providers)
                usage = getattr(response, "usage", None)
                if usage:
                    result.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
                    result.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
                    result.total_tokens += getattr(usage, "total_tokens", 0) or 0
            except Exception as e:
                logger.error(f"[{self.name}] LLM call failed: {e}")
                result.success = False
                result.error = str(e)
                return result

            assistant_msg = response.choices[0].message

            # Check if the model wants to call tools
            if assistant_msg.tool_calls:
                # Append as plain dict so _trim_messages (and the API on the next
                # call) can handle it uniformly. Only keep fields the API needs.
                messages.append({
                    "role": assistant_msg.role,
                    "content": assistant_msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in assistant_msg.tool_calls
                    ],
                })

                tool_calls = assistant_msg.tool_calls
                num_calls = len(tool_calls)
                result.num_tool_calls += num_calls

                if num_calls == 1:
                    # Single tool call — execute directly (no thread overhead)
                    tc = tool_calls[0]
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}
                    logger.info(f"[{self.name}] Tool call: {tool_name}({tool_args})")
                    tool_result = self._execute_tool(tool_name, tool_args, context)
                    context.add_trace(self.name, f"tool:{tool_name}", str(tool_result)[:300])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tool_name,  # Required by some Gemini/Vertex endpoints
                        "content": str(tool_result),
                    })
                else:
                    # Multiple tool calls — execute in parallel
                    parsed_calls = []
                    for tc in tool_calls:
                        tool_name = tc.function.name
                        try:
                            tool_args = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            tool_args = {}
                        logger.info(f"[{self.name}] Tool call (parallel): {tool_name}({tool_args})")
                        parsed_calls.append((tc, tool_name, tool_args))

                    tool_results = {}
                    with ThreadPoolExecutor(max_workers=min(num_calls, 8)) as executor:
                        future_to_tc = {
                            executor.submit(self._execute_tool, name, args, context): tc
                            for tc, name, args in parsed_calls
                        }
                        for future in as_completed(future_to_tc):
                            tc = future_to_tc[future]
                            tool_results[tc.id] = future.result()

                    # Append results in original order to preserve message sequence.
                    # Apply a per-iteration char budget: if the combined output of all
                    # parallel results would exceed MAX_ITER_TOOL_CHARS, truncate the
                    # later results more aggressively so the window doesn't balloon.
                    iter_chars_used = 0
                    for tc, tool_name, _ in parsed_calls:
                        tr = str(tool_results[tc.id])
                        remaining_budget = MAX_ITER_TOOL_CHARS - iter_chars_used
                        if remaining_budget <= 0:
                            tr = f"[result omitted — per-iteration tool budget ({MAX_ITER_TOOL_CHARS:,} chars) reached]"
                        elif len(tr) > remaining_budget:
                            tr = (
                                tr[:remaining_budget]
                                + f"\n... [truncated — budget {MAX_ITER_TOOL_CHARS:,} chars/iter reached]"
                            )
                        iter_chars_used += len(tr)
                        context.add_trace(self.name, f"tool:{tool_name}", tr[:300])
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": tool_name,  # Required by some Gemini/Vertex endpoints
                            "content": tr,
                        })

                # Sliding-window trim fires HERE — after all tool results for this
                # iteration are appended — so the next LLM call always sees a
                # bounded history regardless of how many parallel tools ran.
                if MAX_MSG_HISTORY > 0:
                    messages = self._trim_messages(messages, MAX_MSG_HISTORY)

            else:
                # No tool calls — this is the final response
                final_content = assistant_msg.content or ""
                logger.info(f"[{self.name}] Final response received")

                # Try to parse structured output
                result.output = self._parse_output(final_content)
                result.explanation = final_content
                result.success = True

                context.add_trace(
                    self.name, "final_answer", final_content[:300]
                )

                return result

        # Exceeded max iterations
        logger.warning(f"[{self.name}] Exceeded max iterations ({max_iterations})")
        result.success = False
        result.error = f"Exceeded maximum iterations ({max_iterations})"
        return result

    @staticmethod
    def _trim_messages(messages: list[dict], max_messages: int) -> list[dict]:
        """
        Trim message history by both message count and character budget.

        Always keeps the system prompt (messages[0]).
        1. Applies message-count limit (sliding window).
        2. Applies MAX_CONTEXT_CHARS budget: removes oldest non-system messages
           until total characters fit within the budget.
        """
        if len(messages) <= 1:
            return messages

        system = messages[0]
        rest = list(messages[1:])

        # Step 1: message count limit
        if len(rest) >= max_messages:
            rest = rest[-(max_messages - 1):]

        # Step 2: character budget (trim oldest messages when over budget)
        if MAX_CONTEXT_CHARS > 0:
            def _msg_chars(m: dict) -> int:
                content = m.get("content") or ""
                if isinstance(content, str):
                    return len(content)
                if isinstance(content, list):
                    return sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
                return 0

            total_chars = _msg_chars(system) + sum(_msg_chars(m) for m in rest)
            while len(rest) > 1 and total_chars > MAX_CONTEXT_CHARS:
                removed = rest.pop(0)
                total_chars -= _msg_chars(removed)

        return [system] + rest

    def _call_llm(self, messages: list[dict], context: AgentContext | None = None) -> Any:
        """Make an LLM API call."""
        temperature = config.llm.temperature
        if context is not None and context.temperature_override is not None:
            temperature = context.temperature_override
        kwargs = {
            "model": config.llm.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": config.llm.max_tokens,
        }

        if self.tool_schemas:
            kwargs["tools"] = self.tool_schemas
            kwargs["tool_choice"] = "auto"

        call_timeout = config.llm_call_timeout if config.llm_call_timeout > 0 else None
        return self.client.chat.completions.create(**kwargs, timeout=call_timeout)

    def _execute_tool(
        self,
        tool_name: str,
        tool_args: dict,
        context: AgentContext,
    ) -> str:
        """Execute a registered tool and cap its output to MAX_TOOL_OUTPUT_CHARS."""
        if tool_name not in self.tools:
            return f"Error: Unknown tool '{tool_name}'"

        try:
            func = self.tools[tool_name]

            # Inject context-provided dependencies based on declared function parameters.
            # Using inspect.signature ensures we only match actual parameters, not local
            # variables that happen to share a name (co_varnames includes both).
            sig_params = inspect.signature(func).parameters
            if "repo_path" in sig_params and "repo_path" not in tool_args:
                tool_args["repo_path"] = context.repo_path

            if "retriever" in sig_params and "retriever" not in tool_args:
                tool_args["retriever"] = context.retriever

            if "graph_retriever" in sig_params and "graph_retriever" not in tool_args:
                tool_args["graph_retriever"] = context.graph_retriever

            if "repo_filter" in sig_params and "repo_filter" not in tool_args:
                tool_args["repo_filter"] = context.repo_id

            result = str(func(**tool_args))

            # Truncate output to prevent quadratic prompt-token growth across iterations
            if len(result) > MAX_TOOL_OUTPUT_CHARS:
                result = (
                    result[:MAX_TOOL_OUTPUT_CHARS]
                    + f"\n... [truncated — {len(result):,} chars total, showing first {MAX_TOOL_OUTPUT_CHARS:,}]"
                )
            return result

        except Exception as e:
            logger.error(f"Tool '{tool_name}' failed: {e}")
            return f"Error executing tool '{tool_name}': {e}"

    def _parse_output(self, content: str) -> dict:
        """Try to parse JSON from the LLM's final response."""
        # 1. Try fenced JSON block
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 2. Try any fenced code block (model may omit 'json' label)
        code_match = re.search(r'```\s*(\{.*?\})\s*```', content, re.DOTALL)
        if code_match:
            try:
                return json.loads(code_match.group(1))
            except json.JSONDecodeError:
                pass

        # 3. Try finding a bare JSON object in the text
        brace_match = re.search(r'\{[\s\S]*"ranked_locations"[\s\S]*\}', content)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        # 4. Try parsing the entire content as JSON
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            pass

        return {"raw_response": content}
