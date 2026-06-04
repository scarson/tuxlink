"""Property-based tests for url-to-markdown extraction quality.

Runs against cached HTML fixtures in tests/fixtures/. Asserts properties
(title substring, body length range, expected phrase presence, required
frontmatter fields) rather than byte-exact output, so the suite survives
minor trafilatura version changes without breaking.

Run with plain python — no pytest dependency:

    python tests/test_extraction.py

Exit code 0 on all green, 1 on any failure.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

_SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_ROOT / "scripts"))
sys.path.insert(0, str(_SKILL_ROOT / "scripts" / "lib"))

FIXTURES = _SKILL_ROOT / "tests" / "fixtures"


def _ensure_extraction_deps_or_reexec() -> None:
    """If trafilatura + pyyaml are not importable, re-exec through uv or a cached venv.

    Mirrors bootstrap.py's tiered cascade so the test runner respects the same
    "uv if available, otherwise stdlib venv" precedence as production use.
    PyYAML is a test-only dep not registered in bootstrap.REQUIRED, so we add
    it on top of the standard set.
    """
    try:
        import trafilatura  # noqa: F401
        import yaml  # noqa: F401
        return
    except ImportError:
        pass

    import bootstrap  # same cascade logic; imported from scripts/ on sys.path

    # Tier 2 (mirrors bootstrap.py): if uv is on PATH, re-exec under uv. This
    # side-steps the cached-venv path entirely, which is fragile on Microsoft
    # Store Python — the UWP filesystem redirector silently writes the venv
    # to AppData\Local\Packages\PythonSoftwareFoundation.*\LocalCache\Local\...
    # instead of the requested LOCALAPPDATA path, so vpy.exists() returns False
    # and the run fails with a confusing "pyvenv.cfg not found" cascade.
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "run"]
        for dep in (*bootstrap.REQUIRED, "pyyaml"):
            cmd.extend(["--with", dep])
        cmd.extend(["python", __file__, *sys.argv[1:]])
        sys.exit(subprocess.run(cmd).returncode)

    # Tier 3 fallback: cached venv via stdlib. Works on conventional Python
    # installs; install uv if you hit pyvenv.cfg / sandboxing weirdness here.
    venv_python = bootstrap._venv_python(bootstrap._cache_venv_root())
    if not venv_python.exists():
        try:
            venv_python = bootstrap._create_venv_and_install(bootstrap._cache_venv_root())
        except Exception as exc:
            print(
                f"[test-bootstrap] Could not create venv for tests: {exc}\n"
                f"[test-bootstrap] Install uv ("
                f"https://docs.astral.sh/uv/getting-started/installation/) "
                f"to skip the venv flow entirely.",
                file=sys.stderr,
            )
            sys.exit(5)

    # Ensure PyYAML is also present in the cached venv (tests-only dep)
    check = subprocess.run(
        [str(venv_python), "-c", "import yaml"],
        capture_output=True,
    )
    if check.returncode != 0:
        install = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--quiet", "pyyaml"],
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            print(
                f"[test-bootstrap] Could not install pyyaml: {install.stderr}",
                file=sys.stderr,
            )
            sys.exit(5)

    result = subprocess.run([str(venv_python), __file__, *sys.argv[1:]])
    sys.exit(result.returncode)


_ensure_extraction_deps_or_reexec()


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def assert_in(needle: str, haystack: str, label: str) -> None:
    if needle not in haystack:
        raise AssertionError(
            f"{label}: expected {needle!r} to appear in the body (got "
            f"{haystack[:200]!r}...)"
        )


def assert_not_in(needle: str, haystack: str, label: str) -> None:
    if needle in haystack:
        raise AssertionError(f"{label}: {needle!r} should not appear in the body")


def assert_range(value: int, low: int, high: int, label: str) -> None:
    if not (low <= value <= high):
        raise AssertionError(
            f"{label}: expected {low} <= value <= {high}, got {value}"
        )


def split_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter_dict, body) from a markdown string with YAML FM."""
    if not markdown.startswith("---\n"):
        return {}, markdown
    end = markdown.find("\n---\n", 4)
    if end < 0:
        return {}, markdown
    fm_raw = markdown[4:end]
    body = markdown[end + 5:]
    fm: dict[str, str] = {}
    for line in fm_raw.split("\n"):
        m = re.match(r"([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm, body


def extract(fixture: str, url: str) -> tuple[str, dict[str, str], str]:
    """Run trafilatura against a fixture and return (raw_markdown, frontmatter, body)."""
    import trafilatura

    html = (FIXTURES / fixture).read_text(encoding="utf-8")
    md = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        with_metadata=True,
        include_comments=False,
        include_tables=True,
        include_links=True,
        include_formatting=True,
        favor_precision=True,
    )
    if md is None:
        raise AssertionError(f"trafilatura returned None for {fixture}")
    fm, body = split_frontmatter(md)
    return md, fm, body


# ---------------------------------------------------------------------------
# Fixture tests
# ---------------------------------------------------------------------------


def test_reworked_article_extraction() -> None:
    """reworked.co — Cloudflare-protected article, the canonical test case."""
    _md, fm, body = extract(
        "reworked-ai-tool.html",
        "https://www.reworked.co/digital-workplace/ai-is-a-tool/",
    )

    # Frontmatter has the expected metadata fields
    assert "title" in fm and "White Whale" in fm["title"], \
        f"Expected 'White Whale' in title, got {fm.get('title')!r}"
    assert fm.get("author") == "Karl Chan", \
        f"Expected author='Karl Chan', got {fm.get('author')!r}"
    assert fm.get("date", "").startswith("2026-04"), \
        f"Expected date to start with '2026-04', got {fm.get('date')!r}"

    # Body has the article's real content
    assert_range(len(body), 5000, 15000, "body length")
    assert_in("Herman Melville", body, "body")
    assert_in("Ahab", body, "body")
    assert_in("AI Is a Tool, Not a Destination", body, "body")

    # Sidebar widgets are stripped
    assert_not_in("Learning Opportunities", body, "body (sidebar)")
    assert_not_in("HR Tech Europe Amsterdam", body, "body (sidebar)")

    # Inline markdown links are preserved (include_links=True)
    assert "](" in body, "expected at least one markdown link in body"


def test_mdn_docs_extraction() -> None:
    """MDN JavaScript Guide — static, well-structured docs page."""
    _md, fm, body = extract(
        "mdn-js-intro.html",
        "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Introduction",
    )

    assert "title" in fm and len(fm["title"]) > 0, "MDN page should have a title"
    assert_range(len(body), 1000, 50000, "MDN body length")

    # Real MDN content markers
    assert_in("JavaScript", body, "MDN body")


def test_arxiv_abstract_extraction() -> None:
    """arXiv 1706.03762 — Attention Is All You Need."""
    _md, fm, body = extract(
        "arxiv-attention.html",
        "https://arxiv.org/abs/1706.03762",
    )

    assert "title" in fm, "arXiv page should produce a title"
    assert_range(len(body), 500, 20000, "arXiv body length")

    # The abstract should appear in the body
    lowered = body.lower()
    assert "attention" in lowered, "arXiv body should contain 'attention'"


def test_simonw_post_extraction() -> None:
    """simonwillison.net post — static blog post."""
    _md, fm, body = extract(
        "simonw-post.html",
        "https://simonwillison.net/2024/Apr/8/files-to-prompt/",
    )

    assert "title" in fm and len(fm["title"]) > 0, "blog post should have a title"
    assert_range(len(body), 500, 30000, "blog post body length")


def test_github_raw_readme_is_plain_text() -> None:
    """text/plain passthrough: a .md file should not go through trafilatura."""
    # This fixture is already markdown — it would not be extracted; it would be
    # emitted verbatim by url_to_markdown.py's text-type passthrough branch.
    readme = (FIXTURES / "github-raw-readme.md").read_text(encoding="utf-8")
    assert_range(len(readme), 1000, 100000, "raw README length")
    assert_in("Trafilatura", readme, "raw README")
    # Should be markdown from the start (no YAML FM because it's a raw file)
    assert readme.startswith("#"), "README should start with a markdown heading"


# ---------------------------------------------------------------------------
# SSRF guard tests (unit-level)
# ---------------------------------------------------------------------------


def test_ssrf_guard_allows_public_url() -> None:
    from ssrf_guard import validate_url
    validate_url("https://example.com/")
    validate_url("https://www.reworked.co/digital-workplace/ai-is-a-tool/")


def test_ssrf_guard_blocks_cloud_metadata_ip() -> None:
    from ssrf_guard import validate_url, SSRFError
    try:
        validate_url("http://169.254.169.254/latest/meta-data/")
    except SSRFError:
        return
    raise AssertionError("Expected SSRFError for cloud metadata IP")


def test_ssrf_guard_cloud_metadata_cannot_be_overridden() -> None:
    from ssrf_guard import validate_url, SSRFError
    try:
        validate_url("http://169.254.169.254/", allow_private=True)
    except SSRFError:
        return
    raise AssertionError(
        "Expected SSRFError for cloud metadata IP even with allow_private=True"
    )


def test_ssrf_guard_blocks_cloud_metadata_hostname() -> None:
    from ssrf_guard import validate_url, SSRFError
    try:
        validate_url("http://metadata.google.internal/")
    except SSRFError:
        return
    raise AssertionError("Expected SSRFError for metadata.google.internal")


def test_ssrf_guard_blocks_loopback_by_default() -> None:
    from ssrf_guard import validate_url, SSRFError
    try:
        validate_url("http://127.0.0.1/")
    except SSRFError:
        return
    raise AssertionError("Expected SSRFError for 127.0.0.1")


def test_ssrf_guard_allows_loopback_with_override() -> None:
    from ssrf_guard import validate_url
    validate_url("http://127.0.0.1/", allow_private=True)


def test_ssrf_guard_blocks_rfc1918() -> None:
    from ssrf_guard import validate_url, SSRFError
    try:
        validate_url("http://10.0.0.1/")
    except SSRFError:
        return
    raise AssertionError("Expected SSRFError for 10.0.0.1")


def test_ssrf_guard_blocks_non_http_schemes() -> None:
    from ssrf_guard import validate_url, SSRFError
    for url in ("file:///etc/passwd", "gopher://example.com/", "ftp://example.com/"):
        try:
            validate_url(url)
        except SSRFError:
            continue
        raise AssertionError(f"Expected SSRFError for {url!r}")


def test_ssrf_guard_rejects_no_hostname() -> None:
    from ssrf_guard import validate_url, SSRFError
    try:
        validate_url("https://")
    except SSRFError:
        return
    raise AssertionError("Expected SSRFError for URL with no hostname")


def test_ssrf_guard_blocks_metadata_hostname_with_trailing_dot() -> None:
    """DNS-absolute form with trailing dot must not bypass metadata hostname block."""
    from ssrf_guard import validate_url, SSRFError
    try:
        validate_url("http://metadata.google.internal./")
    except SSRFError:
        return
    raise AssertionError(
        "Expected SSRFError for metadata.google.internal. (trailing dot)"
    )


# ---------------------------------------------------------------------------
# Protocol-downgrade unit test
# ---------------------------------------------------------------------------


def test_scheme_downgrade_detection() -> None:
    """Unit test for _is_scheme_downgrade covering all transitions."""
    from url_to_markdown import _is_scheme_downgrade
    assert _is_scheme_downgrade("https", "http") is True
    assert _is_scheme_downgrade("http", "https") is False
    assert _is_scheme_downgrade("http", "http") is False
    assert _is_scheme_downgrade("https", "https") is False


# ---------------------------------------------------------------------------
# Frontmatter YAML round-trip regression tests
# ---------------------------------------------------------------------------


def test_reworked_frontmatter_is_valid_yaml() -> None:
    """Regression test for C1: the reworked.co description contains ': ' which
    must be quoted correctly in the emitted frontmatter. Before the fix,
    trafilatura's with_metadata=True output had an unquoted colon in the
    description and PyYAML rejected it with ScannerError.
    """
    import yaml
    import url_to_markdown as utm

    html = (FIXTURES / "reworked-ai-tool.html").read_text(encoding="utf-8")
    body, meta = utm.extract_markdown(
        html,
        url="https://www.reworked.co/digital-workplace/ai-is-a-tool/",
    )
    extras = {
        "source_url": "https://www.reworked.co/digital-workplace/ai-is-a-tool/",
        "fetched": "2026-04-11T00:00:00Z",
        "http_status": 200,
        "word_count": utm.word_count(body),
    }
    frontmatter = utm.build_frontmatter(meta, extras)
    full = frontmatter + body

    # Round-trip through a real YAML parser
    assert full.startswith("---\n")
    end = full.find("\n---\n", 4)
    assert end > 4, "frontmatter block not closed"
    fm_text = full[4:end]

    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise AssertionError(
            f"Frontmatter failed PyYAML round-trip (regression of C1): {e}\n"
            f"Frontmatter text:\n{fm_text}"
        )

    assert parsed is not None, "frontmatter parsed to None"
    title = parsed.get("title") or ""
    assert "White Whale" in title, f"title field missing or wrong: {title!r}"
    description = parsed.get("description") or ""
    assert "goals:" in description or "customers" in description, (
        f"description field did not round-trip cleanly: {description!r}"
    )
    assert parsed.get("source_url") == "https://www.reworked.co/digital-workplace/ai-is-a-tool/"
    assert parsed.get("http_status") == 200
    assert parsed.get("word_count", 0) > 500


def test_all_fixtures_emit_valid_yaml_frontmatter() -> None:
    """Round-trip every cached fixture through PyYAML to catch regressions
    across multiple publisher layouts, not just reworked.co.
    """
    import yaml
    import url_to_markdown as utm

    fixtures = [
        ("reworked-ai-tool.html", "https://www.reworked.co/digital-workplace/ai-is-a-tool/"),
        ("simonw-post.html", "https://simonwillison.net/2024/Apr/8/files-to-prompt/"),
        ("arxiv-attention.html", "https://arxiv.org/abs/1706.03762"),
        ("mdn-js-intro.html", "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Introduction"),
    ]

    for fixture, url in fixtures:
        html = (FIXTURES / fixture).read_text(encoding="utf-8")
        body, meta = utm.extract_markdown(html, url=url)
        extras = {
            "source_url": url,
            "fetched": "2026-04-11T00:00:00Z",
            "http_status": 200,
            "word_count": utm.word_count(body),
        }
        fm = utm.build_frontmatter(meta, extras)
        full = fm + body
        assert full.startswith("---\n"), f"{fixture}: missing frontmatter opener"
        end = full.find("\n---\n", 4)
        assert end > 4, f"{fixture}: frontmatter not closed"
        fm_text = full[4:end]
        try:
            parsed = yaml.safe_load(fm_text)
        except yaml.YAMLError as e:
            raise AssertionError(f"{fixture} frontmatter invalid YAML: {e}\n{fm_text}")
        assert parsed is not None, f"{fixture} frontmatter parsed to None"
        assert parsed.get("source_url") == url


def test_yaml_scalar_quotes_dangerous_values() -> None:
    """Unit test for _yaml_scalar covering the edge cases that caused C1."""
    import yaml
    from url_to_markdown import _yaml_scalar

    string_cases = [
        "plain string",
        "has colon: here",                  # triggers C1
        "- starts with dash",
        "ends with colon:",
        "has # hash",
        "null", "true", "false", "yes", "no",
        "",
        "  leading and trailing  ",
        "has 'single' quotes",
        'has "double" quotes',
        "has\nnewline",
        "[not a list]",
        "{not a dict}",
        "2026-04-02",
    ]
    for original in string_cases:
        emitted = f"x: {_yaml_scalar(original)}"
        parsed = yaml.safe_load(emitted)
        assert isinstance(parsed, dict), f"parse failed for {original!r} → {emitted!r}"
        round_tripped = parsed.get("x")
        if original == "":
            # YAML parses empty-quoted as empty string
            assert round_tripped == "", f"empty string round-trip failed: {round_tripped!r}"
        else:
            assert str(round_tripped) == original, (
                f"round-trip failed for {original!r}: got {round_tripped!r} from {emitted!r}"
            )

    # Numeric / bool / None primitives
    assert _yaml_scalar(None) == "null"
    assert _yaml_scalar(True) == "true"
    assert _yaml_scalar(False) == "false"
    assert _yaml_scalar(123) == "123"
    assert _yaml_scalar(1.5) == "1.5"

    # Lists — flow sequence emission, each element independently quoted
    emitted_list = _yaml_scalar(["a", "b: has colon", "c"])
    parsed_list = yaml.safe_load(f"x: {emitted_list}")
    assert parsed_list["x"] == ["a", "b: has colon", "c"], (
        f"list round-trip failed: {parsed_list['x']!r} from {emitted_list!r}"
    )
    assert _yaml_scalar([]) == "[]"


# ---------------------------------------------------------------------------
# Structured warnings module tests (Phase 1)
# ---------------------------------------------------------------------------


def test_warning_builder_returns_dict_with_required_fields() -> None:
    from structured_warnings import warning
    w = warning("short_body_suspected_spa_or_paywall", recovery_action="escalate")
    assert w["code"] == "short_body_suspected_spa_or_paywall"
    assert w["recovery_action"] == "escalate"
    assert w["severity"] == "warning"  # default


def test_warning_builder_passes_extra_fields_through() -> None:
    from structured_warnings import warning
    w = warning(
        "short_body_suspected_spa_or_paywall",
        recovery_action="escalate",
        recovery_hint="js_render_required",
        body_bytes=120,
        html_bytes=250_000,
    )
    assert w["recovery_hint"] == "js_render_required"
    assert w["body_bytes"] == 120
    assert w["html_bytes"] == 250_000


def test_warning_builder_info_severity_overrides_default() -> None:
    from structured_warnings import warning
    w = warning("no_title_extracted", recovery_action="accept", severity="info")
    assert w["severity"] == "info"


def test_warning_builder_rejects_unknown_code() -> None:
    from structured_warnings import warning, WarningSchemaError
    try:
        warning("nonexistent_code", recovery_action="accept")
    except WarningSchemaError:
        return
    raise AssertionError("Expected WarningSchemaError for unknown code")


def test_warning_builder_rejects_unknown_recovery_action() -> None:
    from structured_warnings import warning, WarningSchemaError
    try:
        warning("no_title_extracted", recovery_action="ignore")  # type: ignore[arg-type]
    except WarningSchemaError:
        return
    raise AssertionError("Expected WarningSchemaError for unknown recovery_action")


def test_warning_builder_rejects_unknown_severity() -> None:
    from structured_warnings import warning, WarningSchemaError
    try:
        warning("no_title_extracted", recovery_action="accept", severity="critical")  # type: ignore[arg-type]
    except WarningSchemaError:
        return
    raise AssertionError("Expected WarningSchemaError for unknown severity")


def test_warning_known_codes_cover_existing_diagnostics() -> None:
    """The four codes the plan declares must all exist in KNOWN_CODES as of
    Phase 1 ship. Phase 2 wires three of them (short_body, paywall_phrase,
    no_title) into detect_quality_warnings_on_body. The fourth
    ('extraction_returned_no_content') is intentionally reserved in v1.1 --
    its translation branch exists in format_structured_warning_as_string
    but no v1.1 call site emits it; the hard-error ExtractError path
    handles the empty-extraction case instead. See the inline comment in
    structured_warnings.py KNOWN_CODES for the rationale.
    """
    from structured_warnings import KNOWN_CODES
    required = {
        "short_body_suspected_spa_or_paywall",
        "paywall_phrase_detected",
        "no_title_extracted",
        "extraction_returned_no_content",  # reserved-not-emitted, see docstring
    }
    missing = required - KNOWN_CODES
    assert not missing, f"KNOWN_CODES is missing required entries: {missing}"


# ---------------------------------------------------------------------------
# Phase 2 — structured diagnostic emission
# ---------------------------------------------------------------------------


def test_detect_quality_warnings_returns_structured_short_body() -> None:
    import url_to_markdown as utm
    body = "Hi"
    html_size = 100_000  # 100KB HTML, 2-char body → short-body warning
    warnings_out = utm.detect_quality_warnings_on_body(body, html_size, metadata=None)
    short = [w for w in warnings_out if w["code"] == "short_body_suspected_spa_or_paywall"]
    assert len(short) == 1
    w = short[0]
    assert w["recovery_action"] == "escalate"
    assert w["recovery_hint"] == "js_render_required"
    assert w["body_bytes"] == 2
    assert w["html_bytes"] == 100_000


def test_detect_quality_warnings_returns_structured_paywall_phrase() -> None:
    import url_to_markdown as utm
    body = "Preview text. Subscribe to continue reading. More preview."
    warnings_out = utm.detect_quality_warnings_on_body(body, 50_000, metadata=None)
    pw = [w for w in warnings_out if w["code"] == "paywall_phrase_detected"]
    assert len(pw) == 1
    assert pw[0]["recovery_action"] == "retry"
    assert pw[0]["recovery_hint"] == "try_browser_cookies"
    assert "subscribe to continue reading" in pw[0]["matched_phrase"]


def test_detect_quality_warnings_returns_structured_no_title_info() -> None:
    import url_to_markdown as utm
    # Body long enough that short-body warning is not emitted
    body = "x " * 500  # 1000 chars
    warnings_out = utm.detect_quality_warnings_on_body(body, 50_000, metadata=None)
    nt = [w for w in warnings_out if w["code"] == "no_title_extracted"]
    assert len(nt) == 1
    assert nt[0]["severity"] == "info"
    assert nt[0]["recovery_action"] == "accept"


def test_format_structured_warning_as_string_matches_legacy_text() -> None:
    """Backwards compat: each structured warning produces the same human-readable
    text the legacy `warnings: [str]` list emitted before the refactor."""
    import url_to_markdown as utm
    from structured_warnings import warning
    short = warning(
        "short_body_suspected_spa_or_paywall",
        recovery_action="escalate",
        recovery_hint="js_render_required",
        body_bytes=120,
        html_bytes=250_000,
    )
    msg = utm.format_structured_warning_as_string(short)
    assert "very short" in msg
    assert "120" in msg
    assert "250000" in msg or "250_000" in msg


def test_envelope_includes_extraction_warnings_and_complete_field() -> None:
    """Build a fake envelope payload as run() would and verify both new fields are present."""
    import url_to_markdown as utm
    from structured_warnings import warning
    short = warning(
        "short_body_suspected_spa_or_paywall",
        recovery_action="escalate",
        recovery_hint="js_render_required",
        body_bytes=2,
        html_bytes=50_000,
    )
    legacy_strs = [utm.format_structured_warning_as_string(short)]
    structured = [short]
    envelope = utm.build_success_envelope(
        output_path="/tmp/test.md",
        metadata_payload={"title": "x", "source_url": "https://example.com"},
        legacy_warnings=legacy_strs,
        structured_warnings=structured,
    )
    assert envelope["warnings"] == legacy_strs  # backwards compat preserved
    assert envelope["extraction_warnings"] == structured  # new field present
    assert envelope["complete"] is False  # escalate-class warning → not complete


def test_envelope_complete_true_when_no_escalate_warnings() -> None:
    import url_to_markdown as utm
    from structured_warnings import warning
    no_title = warning("no_title_extracted", recovery_action="accept", severity="info")
    envelope = utm.build_success_envelope(
        output_path="/tmp/test.md",
        metadata_payload={"title": None, "source_url": "https://example.com"},
        legacy_warnings=[],
        structured_warnings=[no_title],
    )
    assert envelope["complete"] is True


# ---------------------------------------------------------------------------
# Phase 3 — extractor seam + BS4 primitives
# ---------------------------------------------------------------------------


def test_parse_html_returns_bs4_document() -> None:
    from extractors import parse_html
    soup = parse_html("<html><body><h1>Hi</h1></body></html>")
    assert soup.find("h1") is not None
    assert soup.find("h1").get_text(strip=True) == "Hi"


def test_parse_html_tolerates_malformed_input() -> None:
    from extractors import parse_html
    # Unclosed tags, mixed content — lxml should not raise.
    soup = parse_html("<p>Hello <b>world")
    assert "Hello" in soup.get_text()


def test_parse_html_tolerates_empty_input() -> None:
    from extractors import parse_html
    soup = parse_html("")
    # Empty document: find() returns None rather than raising.
    assert soup.find("anything") is None


def test_extract_images_resolves_relative_urls_to_base() -> None:
    from extractors import parse_html, extract_images
    soup = parse_html(
        '<html><body>'
        '<img src="/foo.jpg" alt="foo">'
        '<img src="https://cdn.example.com/bar.png" alt="bar">'
        '<img src="data:image/png;base64,iVBORw0K" alt="data uri">'
        '<img alt="no src">'
        '</body></html>'
    )
    imgs = extract_images(soup, base_url="https://example.com/article/")
    srcs = [img["src"] for img in imgs]
    assert "https://example.com/foo.jpg" in srcs
    assert "https://cdn.example.com/bar.png" in srcs
    # data: URIs filtered out (not fetchable via HTTP)
    assert not any(s.startswith("data:") for s in srcs)
    # Missing src filtered out
    assert len(imgs) == 2


def test_html_to_markdown_handles_headings_paragraphs_lists() -> None:
    from extractors import html_to_markdown
    md = html_to_markdown(
        "<h1>Title</h1>"
        "<p>First paragraph with <strong>bold</strong> and <em>italic</em>.</p>"
        "<ul><li>one</li><li>two</li></ul>"
    )
    assert "# Title" in md
    assert "**bold**" in md
    assert "*italic*" in md
    assert "- one" in md
    assert "- two" in md


def test_html_to_markdown_handles_links_and_code() -> None:
    from extractors import html_to_markdown
    md = html_to_markdown(
        '<p>See <a href="https://example.com">example</a> '
        'and <code>my_func()</code>.</p>'
    )
    assert "[example](https://example.com)" in md
    assert "`my_func()`" in md


def test_dispatch_returns_generic_for_unknown_host() -> None:
    from extractors import dispatch, extract_generic_trafilatura
    fn = dispatch("https://random-blog.example.com/article")
    assert fn is extract_generic_trafilatura


def test_register_extractor_then_dispatch_returns_it() -> None:
    from extractors import (
        register_extractor, dispatch, extract_generic_trafilatura,
        _RESET_REGISTRY_FOR_TESTS,
    )

    def fake_extractor(html, *, url):
        from extractors import ExtractResult
        return ExtractResult(body="", metadata=None, extraction_method="fake")

    try:
        register_extractor("test-host.example.com", fake_extractor)
        assert dispatch("https://test-host.example.com/x") is fake_extractor
        # Other hosts still fall through to generic
        assert dispatch("https://other.example.com/y") is extract_generic_trafilatura
    finally:
        _RESET_REGISTRY_FOR_TESTS()


def test_extract_generic_trafilatura_returns_result_with_method_name() -> None:
    from extractors import extract_generic_trafilatura
    html = (FIXTURES / "reworked-ai-tool.html").read_text(encoding="utf-8")
    result = extract_generic_trafilatura(
        html,
        url="https://www.reworked.co/digital-workplace/ai-is-a-tool/",
    )
    assert result.extraction_method == "generic_trafilatura"
    assert "White Whale" in (result.metadata.title or "")
    assert len(result.body) > 5000


# ---------------------------------------------------------------------------
# Phase 3.3 — extract_via_dispatch integration
# ---------------------------------------------------------------------------


def test_run_uses_dispatched_extractor_when_host_is_registered() -> None:
    """End-to-end-ish: register a fake extractor, run extract_via_dispatch,
    confirm the fake extractor's output is what comes back."""
    import url_to_markdown as utm
    from extractors import (
        register_extractor, _RESET_REGISTRY_FOR_TESTS, ExtractResult,
    )

    fake_called: list[str] = []

    class _FakeMeta:
        title = "Fake Title"
        author = "Fake Author"
        date = "2026-05-14"
        url = "https://fake.test/x"
        hostname = "fake.test"
        sitename = None
        description = None
        categories = []
        tags = []
        language = None

    def fake_extractor(html, *, url):
        fake_called.append(url)
        return ExtractResult(
            body="# Fake body\n\nFrom registered extractor.",
            metadata=_FakeMeta(),
            extraction_method="fake_test_extractor",
        )

    try:
        register_extractor("fake.test", fake_extractor)
        html = "<html><body>ignored</body></html>"
        result = utm.extract_via_dispatch(html, url="https://fake.test/x")
        assert result.extraction_method == "fake_test_extractor"
        assert "Fake body" in result.body
        assert fake_called == ["https://fake.test/x"]
    finally:
        _RESET_REGISTRY_FOR_TESTS()


def test_run_falls_through_to_generic_for_unknown_host() -> None:
    import url_to_markdown as utm
    html = (FIXTURES / "reworked-ai-tool.html").read_text(encoding="utf-8")
    result = utm.extract_via_dispatch(
        html, url="https://www.reworked.co/digital-workplace/ai-is-a-tool/"
    )
    assert result.extraction_method == "generic_trafilatura"
    assert "White Whale" in (result.metadata.title or "")


# ---------------------------------------------------------------------------
# Phase 4.1 — --strict flag + compute_exit_code
# ---------------------------------------------------------------------------


def test_compute_exit_code_zero_when_no_escalate_warnings() -> None:
    import url_to_markdown as utm
    from structured_warnings import warning
    info_only = [warning("no_title_extracted", recovery_action="accept", severity="info")]
    accept_only = [warning("paywall_phrase_detected", recovery_action="retry",
                            recovery_hint="try_browser_cookies", matched_phrase="x")]
    assert utm.compute_exit_code(structured_warnings=info_only, strict=False) == 0
    assert utm.compute_exit_code(structured_warnings=info_only, strict=True) == 0
    assert utm.compute_exit_code(structured_warnings=accept_only, strict=True) == 0


def test_compute_exit_code_strict_promotes_escalate_to_8() -> None:
    import url_to_markdown as utm
    from structured_warnings import warning
    escalate = [warning(
        "short_body_suspected_spa_or_paywall", recovery_action="escalate",
        recovery_hint="js_render_required", body_bytes=2, html_bytes=50_000,
    )]
    assert utm.compute_exit_code(structured_warnings=escalate, strict=False) == 0
    assert utm.compute_exit_code(structured_warnings=escalate, strict=True) == 8


def test_compute_exit_code_ignores_non_escalate_warnings_with_strict() -> None:
    """A 'retry'-action warning under --strict must NOT promote to exit 8.
    Only 'escalate' is the trigger; other recovery_actions are informational
    or retry-suggesting and do not signal partial state."""
    import url_to_markdown as utm
    from structured_warnings import warning
    retry_only = [warning(
        "paywall_phrase_detected", recovery_action="retry",
        recovery_hint="try_browser_cookies", matched_phrase="subscribe to read",
    )]
    assert utm.compute_exit_code(structured_warnings=retry_only, strict=True) == 0


def test_strict_promotion_happens_after_file_write() -> None:
    """Even when --strict promotes to exit 8, the .md file MUST already be
    written. compute_exit_code is the LAST call in run()'s success path;
    file write is BEFORE it. Verify by reading the source: the LAST
    occurrence of `out_path.write_text(` (the actual write call) must
    appear before the LAST occurrence of `return compute_exit_code(`
    (the success-path return). rindex() finds the LAST occurrence; if a
    future commit adds a second write before the exit-code call, the
    test still asserts the final ordering is correct."""
    import url_to_markdown as utm
    import inspect
    src = inspect.getsource(utm.run)
    write_offset = src.rindex("out_path.write_text(")
    exit_offset = src.rindex("return compute_exit_code(")
    assert write_offset < exit_offset, (
        "out_path.write_text(...) must come BEFORE return compute_exit_code(...) "
        "in run() so --strict still produces an output file. "
        f"Found write at offset {write_offset}, exit-code return at {exit_offset}."
    )


# ---------------------------------------------------------------------------
# Phase 4.2 — --cookies-from-env VAR
# ---------------------------------------------------------------------------


def test_parse_cookie_header_value_returns_dict() -> None:
    import url_to_markdown as utm
    parsed = utm.parse_cookie_header_value("session_id=abc; user_token=xyz")
    assert parsed == {"session_id": "abc", "user_token": "xyz"}


def test_parse_cookie_header_value_handles_empty_string() -> None:
    import url_to_markdown as utm
    assert utm.parse_cookie_header_value("") == {}


def test_parse_cookie_header_value_handles_trailing_semicolons() -> None:
    import url_to_markdown as utm
    parsed = utm.parse_cookie_header_value("a=1; b=2; ")
    assert parsed == {"a": "1", "b": "2"}


def test_parse_cookie_header_value_handles_values_with_equals() -> None:
    """Cookie values are allowed to contain '=' (e.g., base64-encoded tokens)."""
    import url_to_markdown as utm
    parsed = utm.parse_cookie_header_value("token=abc=def==; sid=1")
    assert parsed == {"token": "abc=def==", "sid": "1"}


def test_load_env_cookies_reads_from_named_env_var() -> None:
    import os
    import url_to_markdown as utm
    os.environ["__TEST_COOKIE_VAR__"] = "foo=bar; baz=qux"
    try:
        cookies = utm.load_env_cookies("__TEST_COOKIE_VAR__")
        assert cookies == {"foo": "bar", "baz": "qux"}
    finally:
        del os.environ["__TEST_COOKIE_VAR__"]


def test_load_env_cookies_missing_var_raises_cookieerror() -> None:
    import url_to_markdown as utm
    try:
        utm.load_env_cookies("__DEFINITELY_NOT_SET_456__")
    except utm.CookieError:
        return
    raise AssertionError("Expected CookieError for missing env var")


def test_browser_cookies_and_cookies_from_env_are_mutually_exclusive() -> None:
    """argparse must reject the combination at parse time, before run().
    Without the mutually-exclusive group, the runtime fallback chain
    (browser-cookies wins; env-cookies silently ignored) would be a
    silent footgun for agents passing both."""
    import url_to_markdown as utm
    parser = utm.build_parser()
    try:
        parser.parse_args([
            "https://example.com",
            "--browser-cookies", "chrome",
            "--cookies-from-env", "X",
        ])
    except SystemExit:
        return  # argparse calls sys.exit on mutually-exclusive violation
    raise AssertionError(
        "Expected SystemExit (argparse mutually-exclusive error) when both "
        "--browser-cookies and --cookies-from-env are passed"
    )


# ---------------------------------------------------------------------------
# Phase 4.3 — --overwrite flag
# ---------------------------------------------------------------------------


def test_resolve_unique_path_with_overwrite_true_returns_original() -> None:
    import tempfile
    from pathlib import Path
    import url_to_markdown as utm
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "x.md").write_text("existing")
        result = utm.resolve_unique_path(d, "x.md", overwrite=True)
        assert result == d / "x.md"


def test_resolve_unique_path_with_overwrite_false_uniquifies() -> None:
    import tempfile
    from pathlib import Path
    import url_to_markdown as utm
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "x.md").write_text("existing")
        result = utm.resolve_unique_path(d, "x.md", overwrite=False)
        assert result == d / "x-2.md"


def test_resolve_unique_path_default_is_uniquify() -> None:
    """Backwards compat: positional / kwarg-default invocation matches today."""
    import tempfile
    from pathlib import Path
    import url_to_markdown as utm
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "x.md").write_text("existing")
        # No overwrite= kwarg passed → matches pre-Phase-4 behavior
        result = utm.resolve_unique_path(d, "x.md")
        assert result == d / "x-2.md"


# ---------------------------------------------------------------------------
# Phase 5.1 — content_hash_sha256
# ---------------------------------------------------------------------------


def test_content_hash_sha256_is_deterministic_for_same_body() -> None:
    import url_to_markdown as utm
    body = "# Hello\n\nWorld."
    h1 = utm.compute_content_hash(body)
    h2 = utm.compute_content_hash(body)
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex digest


def test_content_hash_sha256_differs_for_different_bodies() -> None:
    import url_to_markdown as utm
    assert utm.compute_content_hash("a") != utm.compute_content_hash("b")


def test_content_hash_excludes_frontmatter() -> None:
    """The hash is over the BODY only, not the full file with frontmatter.
    Without this contract, every re-fetch produces a different hash because
    the `fetched:` timestamp shifts."""
    import url_to_markdown as utm
    body = "# Article\n\nSome text."
    # The function hashes whatever it gets; this test asserts the contract
    # by hashing both body and body-with-frontmatter and confirming they
    # differ. Callers must pass body-only.
    full = "---\ntitle: x\n---\n\n" + body
    assert utm.compute_content_hash(body) != utm.compute_content_hash(full)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    tests: list[Callable[[], None]] = [
        test_reworked_article_extraction,
        test_mdn_docs_extraction,
        test_arxiv_abstract_extraction,
        test_simonw_post_extraction,
        test_github_raw_readme_is_plain_text,
        test_ssrf_guard_allows_public_url,
        test_ssrf_guard_blocks_cloud_metadata_ip,
        test_ssrf_guard_cloud_metadata_cannot_be_overridden,
        test_ssrf_guard_blocks_cloud_metadata_hostname,
        test_ssrf_guard_blocks_metadata_hostname_with_trailing_dot,
        test_ssrf_guard_blocks_loopback_by_default,
        test_ssrf_guard_allows_loopback_with_override,
        test_ssrf_guard_blocks_rfc1918,
        test_ssrf_guard_blocks_non_http_schemes,
        test_ssrf_guard_rejects_no_hostname,
        test_scheme_downgrade_detection,
        test_reworked_frontmatter_is_valid_yaml,
        test_all_fixtures_emit_valid_yaml_frontmatter,
        test_yaml_scalar_quotes_dangerous_values,
        # Phase 1 — structured warnings
        test_warning_builder_returns_dict_with_required_fields,
        test_warning_builder_passes_extra_fields_through,
        test_warning_builder_info_severity_overrides_default,
        test_warning_builder_rejects_unknown_code,
        test_warning_builder_rejects_unknown_recovery_action,
        test_warning_builder_rejects_unknown_severity,
        test_warning_known_codes_cover_existing_diagnostics,
        # Phase 2 — structured diagnostic emission
        test_detect_quality_warnings_returns_structured_short_body,
        test_detect_quality_warnings_returns_structured_paywall_phrase,
        test_detect_quality_warnings_returns_structured_no_title_info,
        test_format_structured_warning_as_string_matches_legacy_text,
        test_envelope_includes_extraction_warnings_and_complete_field,
        test_envelope_complete_true_when_no_escalate_warnings,
        # Phase 3 — extractor seam + BS4 primitives
        test_parse_html_returns_bs4_document,
        test_parse_html_tolerates_malformed_input,
        test_parse_html_tolerates_empty_input,
        test_extract_images_resolves_relative_urls_to_base,
        test_html_to_markdown_handles_headings_paragraphs_lists,
        test_html_to_markdown_handles_links_and_code,
        test_dispatch_returns_generic_for_unknown_host,
        test_register_extractor_then_dispatch_returns_it,
        test_extract_generic_trafilatura_returns_result_with_method_name,
        # Phase 3.3 — extract_via_dispatch integration
        test_run_uses_dispatched_extractor_when_host_is_registered,
        test_run_falls_through_to_generic_for_unknown_host,
        # Phase 4.1 — --strict flag + compute_exit_code
        test_compute_exit_code_zero_when_no_escalate_warnings,
        test_compute_exit_code_strict_promotes_escalate_to_8,
        test_compute_exit_code_ignores_non_escalate_warnings_with_strict,
        test_strict_promotion_happens_after_file_write,
        # Phase 4.2 — --cookies-from-env VAR
        test_parse_cookie_header_value_returns_dict,
        test_parse_cookie_header_value_handles_empty_string,
        test_parse_cookie_header_value_handles_trailing_semicolons,
        test_parse_cookie_header_value_handles_values_with_equals,
        test_load_env_cookies_reads_from_named_env_var,
        test_load_env_cookies_missing_var_raises_cookieerror,
        test_browser_cookies_and_cookies_from_env_are_mutually_exclusive,
        # Phase 4.3 — --overwrite flag
        test_resolve_unique_path_with_overwrite_true_returns_original,
        test_resolve_unique_path_with_overwrite_false_uniquifies,
        test_resolve_unique_path_default_is_uniquify,
        # Phase 5.1 — content_hash_sha256
        test_content_hash_sha256_is_deterministic_for_same_body,
        test_content_hash_sha256_differs_for_different_bodies,
        test_content_hash_excludes_frontmatter,
    ]

    passed = 0
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            test()
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {name}: {exc}")
            continue
        except Exception as exc:
            failed += 1
            print(f"[ERROR] {name}: {type(exc).__name__}: {exc}")
            continue
        passed += 1
        print(f"[PASS] {name}")

    print()
    print(f"{passed}/{len(tests)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
