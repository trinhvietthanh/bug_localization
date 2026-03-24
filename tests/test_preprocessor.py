"""Tests for bug report preprocessing."""

from data.loader import BugInstance
from data.preprocessor import BugReportPreprocessor


def _bug_instance(problem_statement: str) -> BugInstance:
    return BugInstance(
        instance_id="test",
        repo="apache/commons-lang",
        problem_statement=problem_statement,
        base_commit="deadbeef",
        patch="",
        test_patch="",
    )


def test_extracts_java_stack_trace_and_exception():
    text = """
java.lang.NumberFormatException: For input string: "12x"
    at org.apache.commons.lang3.math.NumberUtils.createNumber(NumberUtils.java:481)
    at org.apache.commons.lang3.math.NumberUtils.createInteger(NumberUtils.java:561)
"""
    preprocessor = BugReportPreprocessor()
    report = preprocessor.process(_bug_instance(text))

    assert any("NumberFormatException" in msg for msg in report.error_messages)
    assert any("NumberUtils.java:481" in trace for trace in report.stack_traces)
    assert any(
        "src/main/java/org/apache/commons/lang3/math/NumberUtils.java" in f
        for f in report.mentioned_files
    )
