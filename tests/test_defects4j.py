"""
Test Defects4J integration with the agent pipeline.
Creates a mock repo structure similar to what Defects4J provides,
then runs the full pipeline with Gemini.
"""

import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from data.defects4j_loader import Defects4JLoader, to_bug_instance


def create_mock_lang1_repo(base_dir: Path) -> Path:
    """Create a minimal mock of the commons-lang repo for Lang_1."""
    # Create the package structure
    pkg = base_dir / "src" / "main" / "java" / "org" / "apache" / "commons" / "lang3" / "math"
    pkg.mkdir(parents=True)

    # Create NumberUtils.java with a simulated bug
    (pkg / "NumberUtils.java").write_text('''package org.apache.commons.lang3.math;

import java.math.BigDecimal;
import java.math.BigInteger;

/**
 * Provides extra functionality for Java Number classes.
 */
public class NumberUtils {

    /**
     * Convert a String to a Long.
     * Returns null if the String is null.
     */
    public static Long createLong(final String str) {
        if (str == null) {
            return null;
        }
        // BUG: Using valueOf instead of decode - doesn't handle hex
        return Long.valueOf(str);
    }

    /**
     * Convert a String to an Integer.
     * Handles hex (0x) and octal (0) prefixes.
     */
    public static Integer createInteger(final String str) {
        if (str == null) {
            return null;
        }
        return Integer.decode(str);
    }

    /**
     * Turns a string value into a java.lang.Number.
     */
    public static Number createNumber(final String str) {
        if (str == null) {
            return null;
        }
        if (str.isEmpty()) {
            throw new NumberFormatException("A blank string is not a valid number");
        }

        final String[] hex_prefixes = {"0x", "0X", "-0x", "-0X"};
        int pfxLen = 0;
        for (final String pfx : hex_prefixes) {
            if (str.startsWith(pfx)) {
                pfxLen = pfx.length();
                break;
            }
        }

        if (pfxLen > 0) {
            // BUG: Only creates Integer for hex, doesn't handle Long hex
            return createInteger(str);
        }

        // ... other numeric parsing
        return new BigDecimal(str);
    }

    /**
     * Checks whether the String a valid Java number.
     */
    public static boolean isNumber(final String str) {
        if (str == null || str.isEmpty()) {
            return false;
        }
        // simplified
        try {
            createNumber(str);
            return true;
        } catch (Exception e) {
            return false;
        }
    }
}
''')

    # Create some other files for distraction
    str_pkg = base_dir / "src" / "main" / "java" / "org" / "apache" / "commons" / "lang3"
    (str_pkg / "StringUtils.java").write_text('''package org.apache.commons.lang3;

/**
 * Operations on Strings.
 */
public class StringUtils {
    public static boolean isEmpty(final CharSequence cs) {
        return cs == null || cs.length() == 0;
    }

    public static boolean isBlank(final CharSequence cs) {
        int strLen;
        if (cs == null || (strLen = cs.length()) == 0) {
            return true;
        }
        for (int i = 0; i < strLen; i++) {
            if (!Character.isWhitespace(cs.charAt(i))) {
                return false;
            }
        }
        return true;
    }
}
''')

    (str_pkg / "SystemUtils.java").write_text('''package org.apache.commons.lang3;

/**
 * Helpers for java.lang.System.
 */
public class SystemUtils {
    public static final String JAVA_VERSION = System.getProperty("java.version");

    public static boolean isJavaVersionAtLeast(float version) {
        return Float.parseFloat(JAVA_VERSION) >= version;
    }
}
''')

    # Create test directory too
    test_pkg = base_dir / "src" / "test" / "java" / "org" / "apache" / "commons" / "lang3" / "math"
    test_pkg.mkdir(parents=True)
    (test_pkg / "NumberUtilsTest.java").write_text('''package org.apache.commons.lang3.math;

import static org.junit.Assert.*;
import org.junit.Test;

public class NumberUtilsTest {
    @Test
    public void testCreateLong() {
        assertEquals(Long.valueOf(12345), NumberUtils.createLong("12345"));
    }

    @Test
    public void testCreateLongHex() {
        // This test would FAIL because createLong uses valueOf, not decode
        assertEquals(Long.valueOf(0xFF), NumberUtils.createLong("0xFF"));
    }
}
''')

    return base_dir


def test_defects4j_loader():
    """Test the Defects4J dataset loader independently."""
    print("=" * 70)
    print("🧪 TEST 1: Defects4J Loader")
    print("=" * 70)

    loader = Defects4JLoader(projects=["Lang"])
    bugs = loader.load(limit=5)

    print(f"Loaded {len(bugs)} bugs from Lang project")

    for bug in bugs:
        print(f"  {bug.instance_id}: files={bug.buggy_files}")

    assert len(bugs) > 0, "Should load at least 1 bug"

    # Test single instance
    lang1 = loader.load_instance("Lang_1")
    assert lang1 is not None, "Should find Lang_1"
    assert "NumberUtils" in lang1.bug_report, "Bug report should mention NumberUtils"
    assert any("NumberUtils.java" in f for f in lang1.buggy_files), \
        "Buggy files should include NumberUtils.java"

    print("✅ Defects4J loader works correctly!\n")


def test_defects4j_pipeline():
    """Test the full pipeline on a mock Defects4J repo."""
    print("=" * 70)
    print("🧪 TEST 2: Full Pipeline with Defects4J Bug (Lang_1)")
    print("=" * 70)
    print(f"Provider: {config.llm.provider}")
    print(f"Model:    {config.llm.model}")
    print(f"API Key:  {'✅ SET' if config.llm.gemini_api_key else '❌ NOT SET'}")
    print()

    if not config.llm.gemini_api_key or config.llm.gemini_api_key == "your-gemini-api-key-here":
        print("⚠️  Skipping pipeline test — no real API key configured")
        return

    # Load real bug from D4J
    loader = Defects4JLoader(projects=["Lang"])
    d4j_bug = loader.load_instance("Lang_1")
    assert d4j_bug is not None

    # Create mock repo
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_path = create_mock_lang1_repo(Path(tmp_dir))
        print(f"📁 Mock repo at: {repo_path}")
        print(f"🐛 Bug report: {d4j_bug.bug_report[:100]}...")
        print(f"📝 Ground truth: {d4j_bug.buggy_files}")

        # Convert to BugInstance
        bug_instance = to_bug_instance(d4j_bug)

        # Run pipeline
        print("\n🚀 Running multi-agent pipeline...\n")
        from agents.orchestrator import Orchestrator

        orchestrator = Orchestrator(retriever=None)
        result = orchestrator.localize(
            bug_instance,
            repo_path=str(repo_path),
            verbose=True,
        )

        # Evaluate results
        print("\n" + "=" * 70)
        print("📊 RESULTS")
        print("=" * 70)
        print(f"Predicted: {result.ranked_files}")
        print(f"Expected:  {d4j_bug.buggy_files}")
        print(f"Time:      {result.total_time:.1f}s")

        # Check if NumberUtils.java is found
        found = any(
            "NumberUtils.java" in f
            for f in result.ranked_files
        )

        from evaluation.metrics import top_n_accuracy, reciprocal_rank
        for n in [1, 3, 5]:
            hit = top_n_accuracy(result.ranked_files, d4j_bug.buggy_files, n)
            print(f"  Top-{n}: {'✅ HIT' if hit else '❌ MISS'}")

        rr = reciprocal_rank(result.ranked_files, d4j_bug.buggy_files)
        print(f"  RR: {rr:.3f}")

        if found:
            print("\n🎉 SUCCESS: NumberUtils.java found in predictions!")
        else:
            print("\n⚠️  NumberUtils.java not found (may still be close)")


if __name__ == "__main__":
    test_defects4j_loader()
    test_defects4j_pipeline()
