"""Public repository asset regression tests."""

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^]]*]\(([^)]+)\)")
MARKDOWN_LINK_WITH_LABEL = re.compile(r"\[[^]]*]\([^)]+\)")
FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`]*`")
HTML_TAG = re.compile(r"<[^>]+>")


def public_markdown_files() -> list[Path]:
    """Return maintained Markdown files while excluding dependency trees."""
    files = list(ROOT.glob("*.md"))
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / ".github").glob("*.md"))
    return sorted(files)


def markdown_prose(content: str) -> str:
    """Remove code and link labels before checking translated prose."""
    content = FENCED_CODE.sub("", content)
    content = INLINE_CODE.sub("", content)
    content = HTML_TAG.sub("", content)
    return MARKDOWN_LINK_WITH_LABEL.sub("", content)


def test_publication_metadata_exists() -> None:
    """Keep the files required for a usable public repository."""
    required = (
        "README.md",
        "README.uk.md",
        "README.ru.md",
        "README.pl.md",
        "LICENSE",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        ".github/workflows/ci.yml",
        ".github/dependabot.yml",
    )

    for relative_path in required:
        assert (ROOT / relative_path).is_file(), relative_path

    document_names = (
        "architecture.md",
        "backups.md",
        "databases.md",
        "development.md",
        "developer-guide.md",
        "installation.md",
        "minecraft-forge.md",
        "monitoring.md",
        "projects.md",
        "releasing.md",
        "security.md",
        "telegram.md",
        "user-guide.md",
    )
    for language in ("en", "ru", "uk", "pl"):
        for document_name in document_names:
            path = ROOT / "docs" / language / document_name
            assert path.is_file(), path.relative_to(ROOT)


def test_readmes_link_to_every_translation() -> None:
    """Make every README language discoverable from every translation."""
    readmes = ("README.md", "README.uk.md", "README.ru.md", "README.pl.md")

    for readme in readmes:
        content = (ROOT / readme).read_text(encoding="utf-8")
        for translation in readmes:
            if translation != readme:
                assert translation in content, f"{readme} does not link to {translation}"


def test_guides_link_to_every_translation() -> None:
    """Keep every primary guide translation directly discoverable."""
    guide_groups = (
        (
            "en/user-guide.md",
            "uk/user-guide.md",
            "ru/user-guide.md",
            "pl/user-guide.md",
        ),
        (
            "en/developer-guide.md",
            "uk/developer-guide.md",
            "ru/developer-guide.md",
            "pl/developer-guide.md",
        ),
    )

    for guide_group in guide_groups:
        for guide in guide_group:
            content = (ROOT / "docs" / guide).read_text(encoding="utf-8")
            for translation in guide_group:
                if translation != guide:
                    assert translation in content, f"{guide} does not link to {translation}"


def test_readmes_describe_supported_interface_languages() -> None:
    """Prevent the obsolete Russian-only interface claim from returning."""
    obsolete_claims = (
        "web interface is currently Russian",
        "web UI сейчас на русском",
        "web UI наразі російською",
        "web UI jest obecnie po rosyjsku",
    )

    for readme in ("README.md", "README.uk.md", "README.ru.md", "README.pl.md"):
        content = (ROOT / readme).read_text(encoding="utf-8")
        assert not any(claim in content for claim in obsolete_claims), readme


def test_translated_guides_do_not_mix_untranslated_english_prose() -> None:
    """Prevent common untranslated English terms from returning to guides."""
    translated_files = (
        "README.ru.md",
        "README.uk.md",
        "README.pl.md",
        "docs/ru/user-guide.md",
        "docs/uk/user-guide.md",
        "docs/pl/user-guide.md",
        "docs/ru/developer-guide.md",
        "docs/uk/developer-guide.md",
        "docs/pl/developer-guide.md",
    )
    untranslated = re.compile(
        r"\b(?:runtime|backend|frontend|rollback|releases?|credentials|payload|"
        r"storage|backups?|recovery|restore)\b|health check|resource guardrails|"
        r"production checklist|targeted tests|quality gate|persistent data|"
        r"failed projects|scheduled backups|tagged release|disk limit|"
        r"filesystem quota|web ui",
        re.IGNORECASE,
    )

    for relative_path in translated_files:
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert not untranslated.search(markdown_prose(content)), relative_path


def test_local_markdown_links_resolve() -> None:
    """Reject broken relative links in public Markdown documentation."""
    for markdown_file in public_markdown_files():
        content = markdown_file.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(content):
            target = raw_target.strip().strip("<>").split("#", maxsplit=1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (markdown_file.parent / unquote(target)).resolve()
            assert resolved.is_relative_to(ROOT), f"Unsafe link in {markdown_file}: {target}"
            assert resolved.exists(), f"Broken link in {markdown_file}: {target}"
