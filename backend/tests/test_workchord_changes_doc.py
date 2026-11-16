"""The WorkChord changes page keeps the facts the connector depends on.

Every change names the WorkChord files and contracts it touched, every gap
cites WorkChord code at the stated commit and says what it unblocks in agdash,
and the observation intake is described as advisory with no effect on dispatch.

Set ``WORKCHORD_REPO`` to a WorkChord checkout to also check that every cited
file exists at that commit and is long enough for the cited lines.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[2] / "docs" / "workchord-changes.md"
GAP_COMMIT = "8efbcef87"
SECTION = re.compile(r"^## (\d)\. (.+)$", re.MULTILINE)
CITATION = re.compile(
    r"`((?:backend|agent-skills|frontend)/[\w./-]+\.(?:py|md|tsx?)):(\d+)(?:-(\d+))?`"
)
SHORT_CITATION = re.compile(r"`:(\d+)(?:-(\d+))?`")
CONTRACT = re.compile(r"`(?:GET|POST|PUT|DELETE)[^`]*/api/[^`]+`")
TOUCHED_PATH = re.compile(r"`(?:backend|frontend|agent-skills|scripts|config|docs)/[^`]+`")


def sections() -> dict[int, str]:
    text = DOC.read_text(encoding="utf-8")
    found = list(SECTION.finditer(text))
    return {
        int(match.group(1)): text[
            match.end() : found[index + 1].start() if index + 1 < len(found) else None
        ]
        for index, match in enumerate(found)
    }


def part(body: str, label: str) -> str:
    start = body.index(f"**{label}.**")
    following = [body.find("\n**", start + 1), len(body)]
    return body[start : min(position for position in following if position != -1)]


def gap_citations(gap: str) -> list[tuple[str, int]]:
    """(path, last cited line) for every citation, resolving `:N` to the path before it."""
    cited: list[tuple[str, int]] = []
    last_path = None
    for match in re.finditer(f"{CITATION.pattern}|{SHORT_CITATION.pattern}", gap):
        if match.group(1):
            last_path = match.group(1)
            cited.append((last_path, int(match.group(3) or match.group(2))))
        elif last_path is not None:
            cited.append((last_path, int(match.group(5) or match.group(4))))
    return cited


def test_the_page_lists_all_eight_changes_with_their_merge_commits():
    body = DOC.read_text(encoding="utf-8")
    assert f"commit `{GAP_COMMIT}`" in body
    assert sorted(sections()) == list(range(1, 9))
    rows = re.findall(
        r"^\| (\d) \| .+ \| \[#\d+\]\(https://github\.com/[^)]+/pull/\d+\) \| `[0-9a-f]{9}` \|",
        body,
        re.MULTILINE,
    )
    assert sorted(int(row) for row in rows) == list(range(1, 9))


@pytest.mark.parametrize("number", range(1, 9))
def test_each_change_names_the_files_and_contracts_it_touches(number):
    body = sections()[number]
    touches = part(body, "Touches")
    if "same files as change" not in touches:
        assert TOUCHED_PATH.search(touches), f"change {number} names no WorkChord file"
    assert CONTRACT.search(touches), f"change {number} names no contract"


@pytest.mark.parametrize("number", range(1, 9))
def test_each_gap_cites_code_and_says_what_it_unblocks(number):
    body = sections()[number]
    assert gap_citations(part(body, "Gap")), f"change {number} cites no WorkChord code"
    assert len(part(body, "Unblocks").split()) > 4, f"change {number} does not say what it unblocks"


def test_the_observation_intake_is_advisory_and_never_affects_dispatch():
    body = sections()[4]
    assert "**The intake is advisory: it has no effect on dispatch.**" in body
    for guarantee in (
        "writes only its own row",
        "No routing, readiness, assignment or work decision reads",
        "Agent keys cannot read observations",
        "keep `availability_unknown`",
    ):
        assert guarantee in body, guarantee


@pytest.mark.skipif(
    not os.environ.get("WORKCHORD_REPO"), reason="needs a WorkChord checkout in WORKCHORD_REPO"
)
def test_cited_code_exists_at_the_stated_commit():
    repository = os.environ["WORKCHORD_REPO"]
    for number, body in sections().items():
        for path, line in gap_citations(part(body, "Gap")):
            source = subprocess.run(
                ["git", "-C", repository, "show", f"{GAP_COMMIT}:{path}"],
                capture_output=True,
                text=True,
                check=False,
            )
            assert source.returncode == 0, f"change {number}: {path} is missing at {GAP_COMMIT}"
            assert line <= len(source.stdout.splitlines()), (
                f"change {number}: {path}:{line} is past the end"
            )
