"""Unit tests for the ReportGenerator wrapper (brimyr.html_report)."""

from __future__ import annotations

import subprocess  # nosec B404 - fixtures build fake CompletedProcess objects; nothing runs

from brimyr.html_report import DEFAULT_REPORT_TYPES, build_args, render


def _recorder(returncode=0, stderr=""):
    calls: list[list[str]] = []

    def run(argv, cwd):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    return calls, run


class TestArgs:
    def test_every_report_goes_in_one_semicolon_list(self):
        """ReportGenerator sniffs each file independently — by XML root element, or a
        `TN:`/`SF:` prefix for lcov — so a polyglot run passes Cobertura and lcov
        together and never has to branch on format."""
        args = build_args(["coverage.xml", "web/coverage/lcov.info"], "out")
        assert "-reports:coverage.xml;web/coverage/lcov.info" in args  # nosec B101

    def test_html_is_inlined(self):
        """`HtmlInline`, not `Html`: plain Html references sibling asset files and
        renders unstyled from a file:// URL after downloading the artifact, which is how
        someone concludes the feature is broken."""
        assert "HtmlInline" in DEFAULT_REPORT_TYPES  # nosec B101
        assert "-reporttypes:HtmlInline;TextSummary" in build_args(["a.xml"], "out")  # nosec B101

    def test_markdown_summary_is_not_requested(self):
        """`MarkdownSummaryGithub` is whole-project — the opposite of what this tool
        gates on — and the free build writes a reportgenerator.io/pro upsell row into
        the table. Brimyr renders its own summary."""
        args = build_args(["a.xml"], "out")
        assert not any("Markdown" in a for a in args)  # nosec B101

    def test_optional_flags_are_omitted_when_unset(self):
        args = build_args(["a.xml"], "out")
        assert not any(a.startswith("-title:") for a in args)  # nosec B101
        assert not any(a.startswith("-sourcedirs:") for a in args)  # nosec B101
        assert not any(a.startswith("-classfilters:") for a in args)  # nosec B101


class TestFailureIsolation:
    def test_no_reports_is_a_skip_not_a_crash(self):
        result = render([], "out")
        assert result.ok is False  # nosec B101
        assert "no coverage reports" in result.message  # nosec B101

    def test_missing_binary_is_a_message_not_an_exception(self):
        def missing(argv, cwd):
            raise FileNotFoundError(argv[0])

        result = render(["a.xml"], "out", runner=missing)
        assert result.ok is False  # nosec B101
        assert "dotnet tool install" in result.message  # nosec B101

    def test_a_failed_render_is_reported_as_non_blocking(self):
        _calls, run = _recorder(returncode=1, stderr="bad report")
        result = render(["a.xml"], "out", runner=run)
        assert result.ok is False  # nosec B101
        assert "non-blocking" in result.message  # nosec B101

    def test_an_os_error_does_not_escape(self):
        def broken(argv, cwd):
            raise OSError("exec format error")

        assert render(["a.xml"], "out", runner=broken).ok is False  # nosec B101

    def test_success_reports_the_target_directory(self):
        _calls, run = _recorder()
        result = render(["a.xml"], "out/html", runner=run)
        assert result.ok is True  # nosec B101
        assert str(result.target_dir) == "out/html"  # nosec B101
