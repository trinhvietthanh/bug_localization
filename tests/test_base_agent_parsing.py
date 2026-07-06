"""
Tests for BaseAgent output parsing robustness.

Covers the SWE-bench failure mode where the LLM emits JSON containing
regex snippets with invalid JSON escapes (\\w, \\A, \\Z ...), which made
json.loads fail and dropped a correct answer (django__django-11099).
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base_agent import BaseAgent


class _DummyAgent(BaseAgent):
    def get_system_prompt(self, context):
        return ""

    def get_initial_message(self, context):
        return ""


@pytest.fixture
def agent():
    return _DummyAgent("test")


def test_parse_clean_fenced_json(agent):
    content = '```json\n{"ranked_locations": [{"file_path": "a.py", "confidence": 0.9}]}\n```'
    out = agent._parse_output(content)
    assert out["ranked_locations"][0]["file_path"] == "a.py"


def test_parse_invalid_regex_escapes(agent):
    # Regex snippets inside explanation strings produce invalid JSON escapes
    content = (
        "```json\n"
        "{\n"
        '  "ranked_locations": [\n'
        "    {\n"
        '      "file_path": "django/contrib/auth/validators.py",\n'
        '      "confidence": 0.98,\n'
        '      "explanation": "The regex r\'^[\\w.@+-]+$\' should be r\'\\A[\\w.@+-]+\\Z\'."\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```"
    )
    # Sanity: plain json.loads must fail on this input, otherwise the test is vacuous
    inner = content.replace("```json\n", "").replace("\n```", "")
    with pytest.raises(json.JSONDecodeError):
        json.loads(inner)

    out = agent._parse_output(content)
    assert "ranked_locations" in out, f"parse failed: {out.keys()}"
    assert out["ranked_locations"][0]["file_path"] == "django/contrib/auth/validators.py"


def test_parse_trailing_comma(agent):
    content = '```json\n{"ranked_locations": [{"file_path": "b.py", "confidence": 0.8},]}\n```'
    out = agent._parse_output(content)
    assert out["ranked_locations"][0]["file_path"] == "b.py"


def test_parse_bare_json_with_invalid_escapes(agent):
    content = (
        'Here is my analysis:\n'
        '{"ranked_locations": [{"file_path": "c.py", "explanation": "pattern \\d+ matches"}]}'
    )
    out = agent._parse_output(content)
    assert out["ranked_locations"][0]["file_path"] == "c.py"


def test_parse_valid_unicode_escape_preserved(agent):
    content = '```json\n{"ranked_locations": [{"file_path": "d.py", "explanation": "char \\u00e9"}]}\n```'
    out = agent._parse_output(content)
    assert out["ranked_locations"][0]["explanation"] == "char é"


def test_parse_valid_escaped_backslash_preserved(agent):
    # "\\w" is already valid JSON (literal backslash + w) — repair must not touch it
    content = '```json\n{"ranked_locations": [{"file_path": "e.py", "explanation": "regex \\\\w+"}]}\n```'
    out = agent._parse_output(content)
    assert out["ranked_locations"][0]["explanation"] == "regex \\w+"


def test_parse_mixed_valid_and_invalid_escapes(agent):
    # Same string mixes a valid pair (\\A) and a lone invalid escape (\d)
    content = '{"ranked_locations": [{"file_path": "f.py", "explanation": "use \\\\A then \\d+"}]}'
    out = agent._parse_output(content)
    assert out["ranked_locations"][0]["explanation"] == "use \\A then \\d+"


def test_parse_garbage_returns_raw_response(agent):
    out = agent._parse_output("no json here at all")
    assert out == {"raw_response": "no json here at all"}


def test_real_swebench_11099_explanation_parses():
    """Regression: the actual explanation blob that produced predicted=[] must parse."""
    results_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "results",
        "swebench_50.json",
    )
    if not os.path.exists(results_path):
        pytest.skip("swebench_50.json not available")
    with open(results_path) as f:
        data = json.load(f)
    rec = next(
        (r for r in data["per_instance"] if r["instance_id"] == "django__django-11099"),
        None,
    )
    if rec is None or not rec.get("explanation"):
        pytest.skip("instance not in results file")

    out = _DummyAgent("test")._parse_output(rec["explanation"])
    locs = out.get("ranked_locations", [])
    assert locs, "explanation should now parse into ranked_locations"
    assert locs[0]["file_path"] == "django/contrib/auth/validators.py"
