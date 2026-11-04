"""Tests for the diff size limit in `src/agent/diff.py`."""

import pytest

from src.agent.diff import LimitedDiff, limit_diff, number_diff
from src.config import Settings


def make_diff(lines: int) -> str:
    return "".join(f"+line {i:04d}\n" for i in range(lines))  # 11 characters per line


def test_small_diff_is_unchanged():
    diff = make_diff(10)

    assert limit_diff(diff, max_chars=1000) == LimitedDiff(text=diff, truncated=False, original_chars=len(diff))


def test_diff_exactly_at_the_limit_is_unchanged():
    diff = make_diff(10)

    assert limit_diff(diff, max_chars=len(diff)).truncated is False


def test_oversized_diff_is_cut_at_a_line_boundary():
    diff = make_diff(100)

    limited = limit_diff(diff, max_chars=50)

    assert limited.truncated is True
    assert limited.original_chars == len(diff)
    # Four whole lines fit in 50 characters; the fifth would be cut mid-line.
    assert limited.text == make_diff(4)
    assert diff.startswith(limited.text)


def test_single_huge_line_is_cut_at_the_limit():
    # A minified file can be one very long line, with no newline to cut at.
    diff = "+" + "x" * 500

    limited = limit_diff(diff, max_chars=100)

    assert limited.truncated is True
    assert limited.text == diff[:100]


@pytest.mark.parametrize("max_chars", [0, -1])
def test_limit_must_be_positive(max_chars: int):
    with pytest.raises(ValueError):
        limit_diff("+a\n", max_chars=max_chars)


def test_limit_is_configurable(settings: Settings):
    assert settings.MAX_DIFF_CHARS > 0
    assert settings.model_copy(update={"MAX_DIFF_CHARS": 123}).MAX_DIFF_CHARS == 123


# --- Line numbers ------------------------------------------------------------

HEADER = "diff --git a/app/users.py b/app/users.py\nindex 3f2a1b4..9c8d7e6 100644\n--- a/app/users.py\n+++ b/app/users.py\n"


def test_added_and_context_lines_get_their_new_file_line_numbers():
    diff = HEADER + "@@ -8,3 +8,4 @@ def f():\n a\n-b\n+c\n+d\n e\n"

    numbered = number_diff(diff)

    assert numbered.split("\n")[4:] == [
        "@@ -8,3 +8,4 @@ def f():",
        "    8 |  a",
        "      | -b",
        "    9 | +c",
        "   10 | +d",
        "   11 |  e",
        "",
    ]


def test_headers_are_left_as_they_were():
    numbered = number_diff(HEADER + "@@ -1 +1 @@\n-a\n+b\n")

    assert numbered.startswith(HEADER)


def test_each_hunk_restarts_from_its_own_header():
    diff = HEADER + "@@ -1 +1,2 @@\n a\n+b\n@@ -40 +41,2 @@\n x\n+y\n"

    lines = number_diff(diff).split("\n")

    assert "    2 | +b" in lines
    assert "   42 | +y" in lines


def test_each_file_restarts_its_numbers():
    second = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n+z\n"

    lines = number_diff(HEADER + "@@ -5 +5 @@\n+a\n" + second).split("\n")

    assert "    5 | +a" in lines
    assert "    1 | +z" in lines


def test_a_line_that_looks_like_a_header_is_numbered_as_code():
    # Removing a line "-- x" shows as "--- x", and adding "++ x" shows as "+++ x".
    diff = HEADER + "@@ -1,2 +1,2 @@\n--- x\n+++ y\n z\n"

    lines = number_diff(diff).split("\n")

    assert "      | --- x" in lines
    assert "    1 | +++ y" in lines
    assert "    2 |  z" in lines


def test_no_newline_marker_has_no_number():
    diff = HEADER + "@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file\n"

    lines = number_diff(diff).split("\n")

    assert lines.count("      | \\ No newline at end of file") == 2
    assert "    1 | +b" in lines


def test_context_line_stripped_to_nothing_still_counts():
    # Some tools strip the trailing space from an empty context line.
    diff = HEADER + "@@ -1,3 +1,3 @@\n a\n\n+b\n"

    lines = number_diff(diff).split("\n")

    assert "    2 | " in lines
    assert "    3 | +b" in lines


def test_hunk_header_without_counts_means_one_line():
    diff = HEADER + "@@ -7 +7 @@\n-a\n+b\n"

    assert "    7 | +b" in number_diff(diff).split("\n")


def test_a_cut_off_hunk_is_numbered_as_far_as_it_goes():
    diff = HEADER + "@@ -1,50 +1,50 @@\n+a\n+b\n"

    assert number_diff(diff).endswith("    1 | +a\n    2 | +b\n")


def test_text_without_hunks_is_unchanged():
    assert number_diff("not a diff\nat all\n") == "not a diff\nat all\n"
    assert number_diff("") == ""


def test_numbering_is_reversible():
    diff = HEADER + "@@ -8,3 +8,4 @@\n a\n-b\n+c\n+d\n e\n"

    stripped = "\n".join(
        line[8:] if line[5:8] == " | " else line for line in number_diff(diff).split("\n")
    )

    assert stripped == diff
