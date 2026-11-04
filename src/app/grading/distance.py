"""Edit distance with adjacent transpositions (optimal string alignment)."""

from __future__ import annotations


def edit_distance(left: str, right: str) -> int:
    """Return the optimal string alignment distance between two strings.

    Unlike plain Levenshtein, a swap of two adjacent characters counts as one
    edit. This matters for typical typos such as ``teh`` -> ``the``: users
    should not be punished with two insert/delete edits for one slip.

    The implementation is the classic rolling-row version of the DP matrix:
    ``previous[j]`` is the row above, ``current[j]`` the row being built, and
    ``previous_previous`` is needed only for the transposition check.
    """
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous_previous: list[int] | None = None
    # Base row: transforming an empty prefix into ``right[:j]`` costs j inserts.
    previous = list(range(len(right) + 1))

    for i, left_char in enumerate(left, start=1):
        # Base column: transforming ``left[:i]`` into an empty prefix costs i deletes.
        current = [i] + [0] * len(right)
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            # Cheapest of insert, delete, or substitute.
            current[j] = min(
                current[j - 1] + 1,
                previous[j] + 1,
                previous[j - 1] + cost,
            )
            # Transposition: ``ab`` -> ``ba`` is one edit, not two.
            if (
                previous_previous is not None
                and i > 1
                and j > 1
                and left_char == right[j - 2]
                and left[i - 2] == right_char
            ):
                current[j] = min(current[j], previous_previous[j - 2] + 1)
        previous_previous, previous = previous, current

    return previous[-1]
