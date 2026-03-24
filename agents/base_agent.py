"""
Base agent class for the multi-agent bug localization system.
Provides common LLM interaction, tool execution, and structured output.
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import OpenAI

from config import config

logger = logging.getLogger(__name__)

# Shared LLM client singleton — avoids creating separate connections per agent
_shared_clients: dict[str, OpenAI] = {}


@dataclass
class AgentContext:
    """Shared context passed between agents."""
    # Bug report info
    instance_id: str = ""
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
    reflection_round: int = 0

    # Agent trace / memory
    agent_traces: list = field(default_factory=list)
    tool_call_history: list = field(default_factory=list)

    # Repository specifics
    language: str = "python"
    file_extension: str = "*.py"

    # RAG retriever (if available)
    retriever: Any = None

    # Graph RAG retriever (if available)
    graph_retriever: Any = None

    def add_trace(self, agent_name: str, action: str, result: str):
        """Add an entry to the agent trace."""
        self.agent_traces.append({
            "agent": agent_name,
            "action": action,
            "result": result[:500],  # Truncate long results
        })

    def get_trace_summary(self) -> str:
        """Get a summary of all agent traces."""
        lines = []
        for t in self.agent_traces:
            lines.append(f"[{t['agent']}] {t['action']}: {t['result'][:200]}")
        return "\n".join(lines)


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
                response = self._call_llm(messages)
                result.num_llm_calls += 1
            except Exception as e:
                logger.error(f"[{self.name}] LLM call failed: {e}")
                result.success = False
                result.error = str(e)
                return result

            assistant_msg = response.choices[0].message

            # Check if the model wants to call tools
            if assistant_msg.tool_calls:
                messages.append(assistant_msg)

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

                    # Append results in original order to preserve message sequence
                    for tc, tool_name, _ in parsed_calls:
                        tr = tool_results[tc.id]
                        context.add_trace(self.name, f"tool:{tool_name}", str(tr)[:300])
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": str(tr),
                        })

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

    def _call_llm(self, messages: list[dict]) -> Any:
        """Make an LLM API call."""
        kwargs = {
            "model": config.llm.model,
            "messages": messages,
            "temperature": config.llm.temperature,
            "max_tokens": config.llm.max_tokens,
        }

        if self.tool_schemas:
            kwargs["tools"] = self.tool_schemas
            kwargs["tool_choice"] = "auto"

        return self.client.chat.completions.create(**kwargs)

    def _execute_tool(
        self,
        tool_name: str,
        tool_args: dict,
        context: AgentContext,
    ) -> str:
        """Execute a registered tool."""
        if tool_name not in self.tools:
            return f"Error: Unknown tool '{tool_name}'"

        try:
            func = self.tools[tool_name]

            # Inject repo_path if needed
            if "repo_path" in func.__code__.co_varnames and "repo_path" not in tool_args:
                tool_args["repo_path"] = context.repo_path

            # Inject retriever if needed
            if "retriever" in func.__code__.co_varnames and "retriever" not in tool_args:
                tool_args["retriever"] = context.retriever

            # Inject graph_retriever if needed
            if "graph_retriever" in func.__code__.co_varnames and "graph_retriever" not in tool_args:
                tool_args["graph_retriever"] = context.graph_retriever

            result = func(**tool_args)
            return str(result)

        except Exception as e:
            logger.error(f"Tool '{tool_name}' failed: {e}")
            return f"Error executing tool '{tool_name}': {e}"

    def _parse_output(self, content: str) -> dict:
        """Try to parse JSON from the LLM's final response."""
        json_match = re.search(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try parsing the entire content as JSON
        try:
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            pass

        return {"raw_response": content}
