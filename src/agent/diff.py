"""Keeps a diff within the size the model can review.

A local model on a CPU reads a prompt slowly, and Ollama runs it with a fixed
context window. A diff that's too long would make a review take too long, or
push the start of the prompt out of the window without any error. So the diff
is cut to `MAX_DIFF_CHARS` before the graph runs, and `truncated` records that
the review only covers the first part.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LimitedDiff:
    text: str
    truncated: bool
    original_chars: int


def limit_diff(diff: str, max_chars: int) -> LimitedDiff:
    """Return the diff, cut to at most `max_chars` characters if it's longer.

    The cut falls at the end of the last whole line that fits, so the model
    doesn't see half a line. A single line longer than the limit, such as a
    minified file, is cut at the limit itself.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(diff) <= max_chars:
        return LimitedDiff(text=diff, truncated=False, original_chars=len(diff))

    head = diff[:max_chars]
    last_newline = head.rfind("\n")
    text = head[: last_newline + 1] if last_newline >= 0 else head
    return LimitedDiff(text=text, truncated=True, original_chars=len(diff))


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_GUTTER_WIDTH = 5


def number_diff(diff: str) -> str:
    """Put each line's number in the new version of the file in front of it.

    A model can't count lines reliably: left to read `@@ -8,6 +8,7 @@` and
    count down, it gave `line: null` in every try. Counting is exact work, so
    the code does it. Added and unchanged lines get their new-file number,
    removed lines and "\\ No newline" markers get a blank gutter, and file
    headers and hunk headers are left as they were:

        @@ -8,3 +8,4 @@
            8 |  def find_user(username):
              | -    old line
            9 | +    new line

    A hunk header says how many old and new lines it holds, and that is what
    ends the hunk. The count matters because a removed line reading `-- x`
    looks like a `---` file header, and only the count tells them apart.
    """
    lines = diff.split("\n")
    # A diff that ends with a newline splits into a final empty piece that
    # isn't a line, so it's kept aside and put back unnumbered.
    trailing = lines.pop() if lines and lines[-1] == "" else None

    out: list[str] = []
    old_left = new_left = 0
    new_number = 0
    in_or_after_hunk = False
    for line in lines:
        if old_left <= 0 and new_left <= 0:
            if in_or_after_hunk and line.startswith("\\"):
                # "\ No newline at end of file" comes after the last counted
                # line of a hunk, so the counts alone have already ended it.
                out.append(f"{'':>{_GUTTER_WIDTH}} | {line}")
                continue
            in_or_after_hunk = False
            match = _HUNK_HEADER.match(line)
            if match:
                old_left = int(match.group(1) or 1)
                new_number = int(match.group(2))
                new_left = int(match.group(3) or 1)
                in_or_after_hunk = True
            out.append(line)
            continue

        if line.startswith("\\"):
            out.append(f"{'':>{_GUTTER_WIDTH}} | {line}")
        elif line.startswith("-"):
            old_left -= 1
            out.append(f"{'':>{_GUTTER_WIDTH}} | {line}")
        else:
            # "+", or " ", or an empty line that lost its space.
            if line.startswith("+"):
                new_left -= 1
            else:
                old_left -= 1
                new_left -= 1
            out.append(f"{new_number:>{_GUTTER_WIDTH}} | {line}")
            new_number += 1

    if trailing is not None:
        out.append(trailing)
    return "\n".join(out)
