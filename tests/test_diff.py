"""Unit tests for the unified-diff parser (brimyr.coverage.diff)."""

from __future__ import annotations

from brimyr.coverage.diff import normalize_path, parse_unified_diff

ADDED = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,3 @@
+a
+b
+c
"""

MODIFIED = """\
diff --git a/src/mod.py b/src/mod.py
index 1111111..2222222 100644
--- a/src/mod.py
+++ b/src/mod.py
@@ -10,0 +11,2 @@ def f():
+    x = 1
+    y = 2
@@ -20 +22 @@ def g():
-    old
+    new
"""

RENAMED = """\
diff --git a/old/name.py b/new/name.py
similarity index 95%
rename from old/name.py
rename to new/name.py
index 1111111..2222222 100644
--- a/old/name.py
+++ b/new/name.py
@@ -5 +5 @@
-    a
+    b
"""

DELETED = """\
diff --git a/gone.py b/gone.py
deleted file mode 100644
index 1111111..0000000
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
"""


def test_added_file_ranges():
    idx = parse_unified_diff(ADDED)
    fd = idx.get("src/new.py")
    assert fd is not None
    assert fd.is_new_file
    assert fd.added_ranges == ((1, 3),)
    assert fd.added_lines() == frozenset({1, 2, 3})


def test_modified_hunks_use_new_side():
    idx = parse_unified_diff(MODIFIED)
    fd = idx.get("src/mod.py")
    assert fd is not None
    assert fd.status == "modified"
    # +11,2 -> lines 11,12 ; +22 (single) -> line 22
    assert fd.added_lines() == frozenset({11, 12, 22})
    assert fd.contains_line(12)
    assert not fd.contains_line(13)


def test_rename_keeps_head_path_and_changed_lines():
    idx = parse_unified_diff(RENAMED)
    assert idx.get("old/name.py") is None
    fd = idx.get("new/name.py")
    assert fd is not None
    assert fd.status == "renamed"
    assert fd.old_path == "old/name.py"
    assert fd.added_lines() == frozenset({5})


def test_deleted_file_has_no_added_lines():
    idx = parse_unified_diff(DELETED)
    fd = idx.get("gone.py")
    assert fd is not None
    assert fd.is_deleted
    assert fd.added_lines() == frozenset()


def test_empty_diff_is_falsy():
    idx = parse_unified_diff("")
    assert not idx
    assert len(idx) == 0


def test_normalize_path():
    assert normalize_path("./a/b.py") == "a/b.py"
    assert normalize_path("a\\b.py") == "a/b.py"
    assert normalize_path('"a/b c.py"') == "a/b c.py"
