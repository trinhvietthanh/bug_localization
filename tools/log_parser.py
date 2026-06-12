"""
Log parsing module using the Drain3 algorithm.

Extracts structured log templates from bug report text, including:
- Assertion failure patterns (Expected X but was Y)
- Exception/error messages
- Java and Python stack frame signatures
- General log output with templated variable slots

The Drain algorithm groups similar log lines into clusters and replaces
variable parts (numbers, strings, IDs) with <*> placeholders, producing
a stable template that can be searched for in source code.

Agent tool: ``parse_logs`` — agents call this to get structured log data
from a raw bug report or test output snippet.
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Patterns to identify log-like lines in bug report text ───────────────────

_LOG_LEVEL_RE = re.compile(
    r'^\s*(?:\[(?:INFO|WARN(?:ING)?|ERROR|DEBUG|TRACE|FATAL|SEVERE)\]'
    r'|(?:INFO|WARN(?:ING)?|ERROR|DEBUG|TRACE|FATAL|SEVERE)\s*[:\-])',
    re.IGNORECASE,
)
_TIMESTAMP_RE = re.compile(r'^\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}:\d{2}')
_JAVA_EXCEPTION_RE = re.compile(r'^\s*[\w.]+(?:Exception|Error|Failure|Fault)\b')
_JAVA_STACK_FRAME_RE = re.compile(r'^\s+at\s+[\w.$]+\(')
_PYTHON_TRACE_RE = re.compile(
    r'Traceback \(most recent call last\)'
    r'|^\s+File ".+", line \d+'
    r'|^\s+File \'.+\', line \d+'
)
_PYTHON_FRAME_RE = re.compile(
    r'^\s+File "([^"]+)", line (\d+), in (.+)$'
)
_PYTHON_FRAME_SINGLE_RE = re.compile(
    r"^\s+File '([^']+)', line (\d+), in (.+)$"
)
_ASSERTION_RE = re.compile(
    r'[Ee]xpected[:\s<]+(.+?)[>\s]*(?:but\s+(?:was|got)|==)[:\s<]+(.+?)>?\s*$',
    re.IGNORECASE,
)
_JUNIT_ASSERT_RE = re.compile(
    r'(?:AssertionError|AssertionFailedError|ComparisonFailure)[:\s]+(.+)',
)
_FAILED_TEST_RE = re.compile(r'FAIL(?:ED)?[:\s]+(\w[\w.]+)')

_ALL_LOG_PATTERNS = [
    _LOG_LEVEL_RE,
    _TIMESTAMP_RE,
    _JAVA_EXCEPTION_RE,
    _JAVA_STACK_FRAME_RE,
    _PYTHON_TRACE_RE,
    _ASSERTION_RE,
    _JUNIT_ASSERT_RE,
    _FAILED_TEST_RE,
]


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    """A single structured log entry extracted from the bug report."""
    raw_line: str
    template: str               # Drain3 template (e.g. "Expected <*> but was <*>")
    parameters: list[str] = field(default_factory=list)  # Variable slots
    line_type: str = "generic"  # assertion | exception | stack_frame | log | generic


@dataclass
class StackFrame:
    """A single parsed stack frame from a Java or Python traceback."""
    file_path: str          # Source file path (relative where possible)
    line_number: int
    method_name: str        # "ClassName#methodName" (Java) or bare function name (Python)
    language: str = "unknown"  # "java" | "python"


@dataclass
class LogParseResult:
    """Full result of log parsing for one bug report."""
    entries: list[LogEntry] = field(default_factory=list)
    unique_templates: list[str] = field(default_factory=list)
    assertion_failures: list[dict] = field(default_factory=list)
    exception_messages: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    # Structured stack frames extracted from tracebacks
    stack_frames: list[StackFrame] = field(default_factory=list)
    # Deduplicated "ClassName#method" / function seeds for agent candidate priming
    stack_trace_methods: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable summary for injection into agent prompts."""
        lines = []
        if self.assertion_failures:
            lines.append("### Assertion Failures")
            for af in self.assertion_failures[:5]:
                lines.append(
                    f"- Expected: `{af['expected']}`  →  Actual: `{af['actual']}`"
                )
        if self.exception_messages:
            lines.append("### Exception Messages")
            for em in self.exception_messages[:5]:
                lines.append(f"- {em}")
        if self.stack_frames:
            lines.append("### Stack Frames (top of stack first)")
            for sf in self.stack_frames[:8]:
                lines.append(
                    f"- `{sf.method_name}` — {sf.file_path}:{sf.line_number}"
                    f" ({sf.language})"
                )
        if self.unique_templates:
            lines.append("### Log Templates (Drain3)")
            for t in self.unique_templates[:8]:
                lines.append(f"- `{t}`")
        if self.search_terms:
            lines.append(
                "### Derived Search Keywords\n"
                + ", ".join(f"`{t}`" for t in self.search_terms[:15])
            )
        return "\n".join(lines)


# ─── Core parser ──────────────────────────────────────────────────────────────

class LogParser:
    """
    Parses log output embedded in bug reports using Drain3.

    Falls back gracefully to regex-based templating when drain3 is
    unavailable (import error) or when a line fails to parse.
    """

    def __init__(self):
        self._miner = None
        self._drain3_ok = False
        self._init_drain3()

    def _init_drain3(self):
        try:
            from drain3 import TemplateMiner
            from drain3.template_miner_config import TemplateMinerConfig

            cfg = TemplateMinerConfig()
            # Tune for short/medium log lines typical in Java/Python tracebacks
            cfg.drain_depth = 4
            cfg.drain_sim_th = 0.4          # similarity threshold
            cfg.drain_max_children = 100
            cfg.parametrize_numeric_tokens = True
            # Disable file persistence — we build a fresh miner per bug report
            cfg.snapshot_interval_minutes = 0

            self._miner = TemplateMiner(config=cfg)
            self._drain3_ok = True
            logger.debug("LogParser: drain3 TemplateMiner initialised")
        except Exception as exc:
            logger.debug(f"LogParser: drain3 unavailable ({exc}), using regex fallback")

    # ── Public API ─────────────────────────────────────────────────────────────

    def parse(self, text: str) -> LogParseResult:
        """
        Parse all log-like lines from *text* and return structured result.

        Creates a fresh TemplateMiner per call so templates from one bug
        report don't bleed into another.
        """
        # Reset miner state per bug report
        if self._drain3_ok:
            self._reset_miner()

        result = LogParseResult()
        lines = text.splitlines()
        log_lines = self._extract_log_lines(lines)

        seen_templates: set[str] = set()
        for raw in log_lines:
            entry = self._parse_line(raw)
            if entry is None:
                continue
            result.entries.append(entry)
            if entry.template not in seen_templates:
                seen_templates.add(entry.template)
                result.unique_templates.append(entry.template)
            if entry.line_type == "assertion":
                af = self._extract_assertion(raw)
                if af:
                    result.assertion_failures.append(af)
            elif entry.line_type == "exception":
                result.exception_messages.append(raw.strip())

        # Extract structured stack frames (Java + Python)
        result.stack_frames = self._extract_stack_frames(lines)
        result.stack_trace_methods = self._dedupe_methods(result.stack_frames)
        result.search_terms = self._derive_search_terms(result)
        return result

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _reset_miner(self):
        """Re-create TemplateMiner to clear accumulated cluster state."""
        try:
            from drain3 import TemplateMiner
            from drain3.template_miner_config import TemplateMinerConfig

            cfg = TemplateMinerConfig()
            cfg.drain_depth = 4
            cfg.drain_sim_th = 0.4
            cfg.drain_max_children = 100
            cfg.parametrize_numeric_tokens = True
            cfg.snapshot_interval_minutes = 0
            self._miner = TemplateMiner(config=cfg)
        except Exception:
            pass

    def _extract_log_lines(self, lines: list[str]) -> list[str]:
        """Return lines that look like log / trace output."""
        result: list[str] = []
        in_block = False
        for line in lines:
            is_log = any(p.search(line) for p in _ALL_LOG_PATTERNS)
            if is_log:
                result.append(line)
                in_block = True
            elif in_block and line.strip():
                # Include continuation lines inside a traceback/log block
                result.append(line)
            else:
                in_block = False
        return result

    def _classify(self, line: str) -> str:
        stripped = line.strip()
        if _ASSERTION_RE.search(stripped) or _JUNIT_ASSERT_RE.search(stripped):
            return "assertion"
        if _JAVA_STACK_FRAME_RE.match(line):
            return "stack_frame"
        if _JAVA_EXCEPTION_RE.match(stripped):
            return "exception"
        if _LOG_LEVEL_RE.match(stripped) or _TIMESTAMP_RE.match(stripped):
            return "log"
        return "generic"

    def _parse_line(self, line: str) -> Optional[LogEntry]:
        stripped = line.strip()
        if not stripped:
            return None
        line_type = self._classify(line)

        if self._drain3_ok and self._miner is not None:
            try:
                res = self._miner.add_log_message(stripped)
                if res and res.cluster:
                    tmpl = res.cluster.get_template()
                    params = [str(p) for p in (res.parameters or [])]
                    return LogEntry(
                        raw_line=stripped,
                        template=tmpl,
                        parameters=params,
                        line_type=line_type,
                    )
            except Exception as exc:
                logger.debug(f"drain3 parse failed for line: {exc}")

        # Regex fallback
        return LogEntry(
            raw_line=stripped,
            template=self._regex_template(stripped),
            parameters=[],
            line_type=line_type,
        )

    @staticmethod
    def _regex_template(line: str) -> str:
        """Simple parameterisation: replace numbers, hex, quoted strings."""
        t = re.sub(r'0x[0-9a-fA-F]+', '<*>', line)
        t = re.sub(r'\b\d+(\.\d+)?\b', '<*>', t)
        t = re.sub(r'"[^"]{0,80}"', '"<*>"', t)
        t = re.sub(r"'[^']{0,80}'", "'<*>'", t)
        return t.strip()

    @staticmethod
    def _extract_assertion(line: str) -> Optional[dict]:
        m = _ASSERTION_RE.search(line)
        if m:
            return {
                "expected": m.group(1).strip().strip('<>'),
                "actual": m.group(2).strip().strip('<>'),
                "raw": line.strip(),
            }
        # JUnit ComparisonFailure: "expected:<X> but was:<Y>"
        alt = re.search(r'expected:<(.+?)> but was:<(.+?)>', line)
        if alt:
            return {
                "expected": alt.group(1).strip(),
                "actual": alt.group(2).strip(),
                "raw": line.strip(),
            }
        return None

    # ── Stack frame extraction ─────────────────────────────────────────────────

    _JAVA_FRAME_FULL_RE = re.compile(
        r'^\s*at\s+([\w.$]+)\.([\w$<>]+)\(([^)]*\.java):(\d+)\)\s*$'
    )

    def _extract_stack_frames(self, lines: list[str]) -> list[StackFrame]:
        """
        Extract structured StackFrame objects from Java and Python tracebacks.

        Java format:  ``  at org.example.Foo.bar(Foo.java:42)``
        Python format:``    File "path/to/foo.py", line 42, in bar``
        """
        frames: list[StackFrame] = []
        seen: set[tuple] = set()
        in_python_tb = False

        for line in lines:
            # ── Java stack frame ──
            m = self._JAVA_FRAME_FULL_RE.match(line)
            if m:
                fqclass, method, java_file, lineno = m.groups()
                class_simple = fqclass.split(".")[-1]
                pkg_path = fqclass.replace(".", "/").replace("$", "/")
                file_path = f"{pkg_path.rsplit('/', 1)[0]}/{java_file}" if "/" in pkg_path else java_file
                key = (file_path, int(lineno), method)
                if key not in seen:
                    seen.add(key)
                    frames.append(StackFrame(
                        file_path=file_path,
                        line_number=int(lineno),
                        method_name=f"{class_simple}#{method}",
                        language="java",
                    ))
                continue

            # ── Python traceback header ──
            if re.match(r'^Traceback \(most recent call last\)', line.strip()):
                in_python_tb = True
                continue

            # ── Python frame line ──
            pm = _PYTHON_FRAME_RE.match(line) or _PYTHON_FRAME_SINGLE_RE.match(line)
            if pm:
                in_python_tb = True  # also set in case header was missed
                file_path, lineno, func = pm.group(1), pm.group(2), pm.group(3).strip()
                key = (file_path, int(lineno), func)
                if key not in seen:
                    seen.add(key)
                    frames.append(StackFrame(
                        file_path=file_path,
                        line_number=int(lineno),
                        method_name=func,
                        language="python",
                    ))
                continue

            # ── Reset Python tb context on blank or non-tb line ──
            if in_python_tb and not line.strip():
                in_python_tb = False

        return frames

    @staticmethod
    def _dedupe_methods(frames: list[StackFrame]) -> list[str]:
        """Return deduplicated method names preserving stack-top-first order."""
        seen: set[str] = set()
        methods: list[str] = []
        for f in frames:
            if f.method_name and f.method_name not in seen:
                seen.add(f.method_name)
                methods.append(f.method_name)
        return methods

    @staticmethod
    def _derive_search_terms(result: "LogParseResult") -> list[str]:
        """Extract identifier-like tokens from templates as search keywords."""
        stopwords = {
            'expected', 'actual', 'but', 'was', 'got', 'the', 'at', 'in',
            'to', 'and', 'for', 'with', 'from', 'that', 'this', 'is', 'are',
            'should', 'not', 'true', 'false', 'null', 'none', 'java', 'lang',
        }
        terms: dict[str, int] = {}
        for tmpl in result.unique_templates:
            tokens = re.findall(r'\b[A-Za-z][A-Za-z0-9_]{2,}\b', tmpl)
            for tok in tokens:
                if tok.lower() not in stopwords:
                    terms[tok] = terms.get(tok, 0) + 1
        # Also pull expected/actual values as search terms (up to 30 chars)
        for af in result.assertion_failures:
            for val in (af.get("expected", ""), af.get("actual", "")):
                val = val.strip()
                if 2 < len(val) <= 30 and re.match(r'[A-Za-z_]', val):
                    terms[val] = terms.get(val, 0) + 2  # higher weight

        sorted_terms = sorted(terms.items(), key=lambda kv: kv[1], reverse=True)
        return [t for t, _ in sorted_terms[:20]]


# ─── Module-level singleton ────────────────────────────────────────────────────

_parser: Optional[LogParser] = None


def get_log_parser() -> LogParser:
    """Return the module-level LogParser singleton."""
    global _parser
    if _parser is None:
        _parser = LogParser()
    return _parser


def parse_bug_report_logs(text: str) -> LogParseResult:
    """Convenience wrapper: parse log lines from bug report text."""
    return get_log_parser().parse(text)


# ─── Agent tool interface ──────────────────────────────────────────────────────

TOOL_DESCRIPTION = {
    "name": "parse_logs",
    "description": (
        "Parse log/traceback text and return structured information: "
        "assertion failures (expected vs actual values), exception messages, "
        "Java and Python stack frames, Drain3 log templates, and derived "
        "search keywords. Use this when a bug report or test output contains "
        "stack traces, assertion errors, or structured log lines."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "Raw log/traceback text to parse. Can be a full bug report, "
                    "test failure output, or any snippet containing stack traces "
                    "or log lines."
                ),
            }
        },
        "required": ["text"],
    },
}


def parse_logs_tool(text: str) -> dict:
    """
    Agent-callable tool function.

    Returns a JSON-serialisable dict with:
    - ``assertion_failures``: list of {expected, actual, raw}
    - ``exception_messages``: list of exception strings
    - ``stack_frames``: list of {file_path, line_number, method_name, language}
    - ``stack_trace_methods``: ordered list of "Class#method" / function names
    - ``unique_templates``: Drain3 log templates
    - ``search_terms``: derived keyword list
    - ``summary``: human-readable markdown summary
    """
    result = get_log_parser().parse(text)
    return {
        "assertion_failures": result.assertion_failures,
        "exception_messages": result.exception_messages,
        "stack_frames": [
            {
                "file_path": f.file_path,
                "line_number": f.line_number,
                "method_name": f.method_name,
                "language": f.language,
            }
            for f in result.stack_frames
        ],
        "stack_trace_methods": result.stack_trace_methods,
        "unique_templates": result.unique_templates,
        "search_terms": result.search_terms,
        "summary": result.summary(),
    }
