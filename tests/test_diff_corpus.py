"""The shared diff-fixture corpus, and the checksum that keeps it in step.

`brimyr/coverage/diff.py` and chargate's `sarif/diff.py` are near-byte-identical unified-
diff parsers. They are deliberately NOT extracted into a shared package
(MagmaMoose/brimyr#33): the two tools call their parser for different purposes — coverage
here, findings there — so a divergence is a cosmetic inconsistency between two
independent gates rather than a wrong verdict inside one, and that is a much cheaper
failure than the coupling a package would introduce.

What that trade DOES need is a way to notice the divergence. This is it: a corpus of real
`git diff --unified=0` output, hand-verified expectations, and a checksum. Both repos
carry the same files. Editing a fixture in either one breaks the checksum test until the
hash is updated — which is the moment to copy the change across, rather than discovering
six months later that the two parsers disagree about renames.

The expectations are hand-verified against the diff text and are NOT generated from the
parser. A generated expectation asserts only that the parser agrees with itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from brimyr.coverage.diff import parse_unified_diff

CORPUS = Path(__file__).parent / "fixtures" / "diff_corpus"
EXPECTED = json.loads((CORPUS / "expected.json").read_text(encoding="utf-8"))
CASES = sorted(name for name in EXPECTED if name.endswith(".diff"))


def _corpus_digest() -> str:
    files = sorted(p for p in CORPUS.iterdir() if p.suffix in {".diff", ".json"})
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_corpus_checksum_matches():
    """The anti-drift tripwire.

    If this fails you changed the corpus. That is fine — update CORPUS.sha256, and then
    copy the same change into chargate, because the two repos are supposed to hold an
    identical corpus. Failing loudly here is the entire point of not sharing a package.
    """
    recorded = (CORPUS / "CORPUS.sha256").read_text(encoding="utf-8").strip()
    assert _corpus_digest() == recorded  # nosec B101


@pytest.mark.parametrize("case", CASES)
def test_corpus_parses_as_expected(case: str):
    index = parse_unified_diff((CORPUS / case).read_text(encoding="utf-8"))
    expected_files = EXPECTED[case]["files"]

    assert {f.path for f in index.files} == set(expected_files)  # nosec B101

    for path, want in expected_files.items():
        got = index.get(path)
        assert got is not None, path  # nosec B101
        assert got.status == want["status"], path  # nosec B101
        assert [list(r) for r in got.added_ranges] == want["added_ranges"], path  # nosec B101
        assert got.old_path == want["old_path"], path  # nosec B101


def test_disjoint_hunks_do_not_merge():
    """Called out separately because merging them is a plausible bug with a quiet effect.

    `modify.py` changes lines 2 and 5. If the two hunks were merged into (2, 5), lines 3
    and 4 — untouched by the PR — would enter the denominator, and an author would be
    asked to cover code they did not write.
    """
    index = parse_unified_diff((CORPUS / "mixed.diff").read_text(encoding="utf-8"))
    modify = index.get("modify.py")
    assert modify is not None  # nosec B101
    assert modify.contains_line(2) and modify.contains_line(5)  # nosec B101
    assert not modify.contains_line(3)  # nosec B101
    assert not modify.contains_line(4)  # nosec B101


def test_a_path_with_spaces_keeps_no_trailing_tab():
    """git appends a TAB to the `+++` line when the path contains whitespace.

    A surviving tab makes the path unmatchable against any coverage report, so the file
    silently leaves the denominator — the exact silent-pass failure this project keeps
    running into.
    """
    index = parse_unified_diff((CORPUS / "mixed.diff").read_text(encoding="utf-8"))
    assert index.get("dir with spaces/quoted name.py") is not None  # nosec B101
    assert all(not f.path.endswith(("\t", " ")) for f in index.files)  # nosec B101
