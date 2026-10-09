"""This fork's changes reach the in-app update notification.

``VERSION`` follows upstream; ``FORK_VERSION`` adds the fork's own revision as a
fourth component, and ``CHANGELOG.fork.md`` is merged into the changelog the
what's-new dialog reads, every section headed with its source. The rules are
written down at the top of ``CHANGELOG.fork.md``; these tests hold the repo to
them.
"""

from __future__ import annotations

import pathlib
import re

from app import config
from app.config import APP_VERSION
from app.services import changelog
from app.services.catalogue_common import version_tuple
from app.services.changelog import (
    SOURCE_FORK,
    SOURCE_UPSTREAM,
    merge_changelogs,
    read_changelog,
    section_for,
    sections_between,
)
from app.services.release_notes import valid_version

REPO = pathlib.Path(__file__).resolve().parents[3]

UPSTREAM = """# Changelog

Preamble.

## [2.159.0] - 2026-10-12

### Added
- Upstream feature

## [2.158.0] - 2026-10-09

### Fixed
- Upstream fix
"""

FORK = """# Fork changelog

Notes for contributors, with a heading of their own.

## How a fork release is numbered

- Not a release.

## [2.159.0.3] - 2026-10-13

Synced with upstream 2.159.0.

## [2.158.0.2] - 2026-10-10

### Fixed
- Fork fix

## [2.158.0.1] - 2026-10-09

### Added
- Fork feature
"""


def _headings(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.startswith("## ")]


class TestVersion:
    def test_fork_revision_is_appended_as_a_fourth_component(self, tmp_path):
        (tmp_path / "VERSION").write_text("2.158.0\n")
        (tmp_path / "FORK_VERSION").write_text("7\n")
        assert config._with_fork_revision(tmp_path / "VERSION") == "2.158.0.7"

    def test_without_a_fork_revision_the_version_is_upstreams(self, tmp_path):
        (tmp_path / "VERSION").write_text("2.158.0\n")
        assert config._with_fork_revision(tmp_path / "VERSION") == "2.158.0"

    def test_a_revision_that_is_not_a_whole_number_is_ignored(self, tmp_path):
        (tmp_path / "VERSION").write_text("2.158.0\n")
        for bad in ("", "beta", "1.2", "-1"):
            (tmp_path / "FORK_VERSION").write_text(bad)
            assert config._with_fork_revision(tmp_path / "VERSION") == "2.158.0", bad

    def test_the_running_version_is_upstreams_plus_this_forks_revision(self):
        upstream = (REPO / "VERSION").read_text().strip()
        revision = (REPO / "FORK_VERSION").read_text().strip()
        assert revision.isdigit()
        assert APP_VERSION == f"{upstream}.{revision}"

    def test_a_fork_version_sorts_between_upstream_releases(self):
        """So a fork release is an upgrade from the upstream version it is
        built on, and the next upstream release still counts as newer."""
        assert version_tuple("2.158.0.2") > version_tuple("2.158.0")
        assert version_tuple("2.158.0.2") > version_tuple("2.158.0.1")
        assert version_tuple("2.158.1") > version_tuple("2.158.0.2")
        assert version_tuple("2.159.0.3") > version_tuple("2.158.0.4")

    def test_a_fork_version_is_accepted_by_the_release_notes_endpoint(self):
        assert valid_version(APP_VERSION)

    def test_frontend_and_mcp_server_compose_the_version_the_same_way(self):
        """The frontend reloads when its build version differs from the
        server's, so all three readers must agree."""
        vite = (REPO / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
        assert '"FORK_VERSION"' in vite
        assert "`${upstreamVersion}.${forkRevision}`" in vite
        mcp = (REPO / "mcp-server" / "turbo_ea_mcp" / "config.py").read_text(encoding="utf-8")
        assert 'with_name("FORK_VERSION")' in mcp
        assert 'f"{version}.{revision}" if revision.isdigit() else version' in mcp


class TestMergeChangelogs:
    def test_sections_interleave_by_version_newest_first(self):
        assert _headings(merge_changelogs(UPSTREAM, FORK)) == [
            f"## [2.159.0.3] - 2026-10-13 · {SOURCE_FORK}",
            f"## [2.159.0] - 2026-10-12 · {SOURCE_UPSTREAM}",
            f"## [2.158.0.2] - 2026-10-10 · {SOURCE_FORK}",
            f"## [2.158.0.1] - 2026-10-09 · {SOURCE_FORK}",
            f"## [2.158.0] - 2026-10-09 · {SOURCE_UPSTREAM}",
        ]

    def test_every_entry_stays_under_its_own_heading(self):
        merged = merge_changelogs(UPSTREAM, FORK)
        fork_fix = section_for(merged, "2.158.0.2")
        assert "Fork fix" in fork_fix
        assert "Upstream" not in fork_fix.split("\n", 1)[1]
        assert "Fork feature" not in fork_fix
        upstream_fix = section_for(merged, "2.158.0")
        assert "Upstream fix" in upstream_fix and "Fork" not in upstream_fix

    def test_upstream_preamble_is_kept_and_the_forks_notes_are_dropped(self):
        merged = merge_changelogs(UPSTREAM, FORK)
        assert merged.startswith("# Changelog\n\nPreamble.\n\n## [2.159.0.3]")
        assert "Notes for contributors" not in merged
        assert "Not a release" not in merged
        assert merged.endswith("- Upstream fix\n")

    def test_the_dialog_heading_names_the_source(self):
        merged = merge_changelogs(UPSTREAM, FORK)
        assert section_for(merged, "2.158.0.2").startswith(
            f"## 2.158.0.2 — 2026-10-10 · {SOURCE_FORK}"
        )
        assert section_for(merged, "2.159.0").startswith(
            f"## 2.159.0 — 2026-10-12 · {SOURCE_UPSTREAM}"
        )

    def test_an_upgrade_across_a_sync_shows_both_sources(self):
        """2.158.0.2 → 2.159.0.3: one fork release and the upstream release
        it merged, nothing the user already had."""
        notes = sections_between(
            merge_changelogs(UPSTREAM, FORK), after="2.158.0.2", upto="2.159.0.3"
        )
        assert _headings(notes) == [
            f"## 2.159.0.3 — 2026-10-13 · {SOURCE_FORK}",
            f"## 2.159.0 — 2026-10-12 · {SOURCE_UPSTREAM}",
        ]
        assert "Upstream feature" in notes and "Fork fix" not in notes

    def test_the_first_numbered_fork_release_shows_what_was_never_announced(self):
        notes = sections_between(
            merge_changelogs(UPSTREAM, FORK), after="2.158.0", upto="2.158.0.2"
        )
        assert "Fork fix" in notes and "Fork feature" in notes
        assert "Upstream fix" not in notes

    def test_without_fork_sections_upstreams_file_is_returned_untouched(self):
        assert merge_changelogs(UPSTREAM, "") == UPSTREAM
        assert merge_changelogs(UPSTREAM, "# Fork changelog\n\nNothing yet.\n") == UPSTREAM

    def test_a_fork_changelog_alone_still_reads(self):
        merged = merge_changelogs("", FORK)
        assert merged.startswith(f"## [2.159.0.3] - 2026-10-13 · {SOURCE_FORK}")
        assert SOURCE_UPSTREAM not in merged

    def test_windows_line_endings_are_read(self):
        merged = merge_changelogs(UPSTREAM.replace("\n", "\r\n"), FORK.replace("\n", "\r\n"))
        assert len(_headings(merged)) == 5
        assert "\r" not in merged


class TestThisRepository:
    def test_the_bundled_changelog_carries_both_sources(self):
        text = read_changelog()
        assert text.startswith("# Changelog")
        assert f" · {SOURCE_FORK}" in text and f" · {SOURCE_UPSTREAM}" in text

    def test_the_running_version_has_a_fork_section(self):
        """Every release of this fork, a sync included, raises FORK_VERSION
        and adds its section — otherwise the update is announced with an
        empty dialog."""
        assert section_for(read_changelog(), APP_VERSION).startswith(f"## {APP_VERSION} — "), (
            f"CHANGELOG.fork.md has no '## [{APP_VERSION}] - <date>' section"
        )
        assert SOURCE_FORK in section_for(read_changelog(), APP_VERSION).split("\n")[0]

    def test_fork_sections_are_numbered_and_in_order(self):
        text = (REPO / "CHANGELOG.fork.md").read_text(encoding="utf-8")
        versions = re.findall(r"^## \[([^\]]+)\]", text, flags=re.M)
        assert versions, "CHANGELOG.fork.md has no version sections"
        assert versions[0] == APP_VERSION
        for version in versions:
            assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version), version
        revisions = [int(v.rsplit(".", 1)[1]) for v in versions]
        assert revisions == sorted(set(revisions), reverse=True), (
            "fork revisions must be unique and newest first"
        )
        tuples = [version_tuple(v) for v in versions]
        assert tuples == sorted(tuples, reverse=True)

    def test_the_image_ships_the_fork_revision_and_changelog(self):
        dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
        # Backend build and runtime stages, frontend build, MCP server.
        assert dockerfile.count("COPY FORK_VERSION ./FORK_VERSION") == 3
        assert "COPY --from=backend-build /app/FORK_VERSION ./FORK_VERSION" in dockerfile
        assert "COPY CHANGELOG.fork.md ./CHANGELOG.fork.md" in dockerfile
        assert "COPY --from=backend-build /app/CHANGELOG.fork.md ./CHANGELOG.fork.md" in dockerfile
        # The frontend must have the revision before it builds.
        frontend = dockerfile[dockerfile.index("AS frontend-build") :]
        assert frontend.index("COPY FORK_VERSION") < frontend.index("RUN npm run build")

    def test_dockerignore_readmits_the_fork_changelog(self):
        lines = [
            line.strip()
            for line in (REPO / ".dockerignore").read_text(encoding="utf-8").split("\n")
        ]
        assert lines.index("!CHANGELOG.fork.md") > lines.index("*.md")

    def test_the_docker_path_candidate_matches_the_image_layout(self):
        source = pathlib.Path(changelog.__file__).read_text(encoding="utf-8")
        assert 'here.parent.parent / "CHANGELOG.fork.md"' in source
