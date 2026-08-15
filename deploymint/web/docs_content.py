"""Renders the project's own docs/*.md (including docs/PROJECT.md) as
browsable in-app pages at /guide — a GitBook-style sidebar + page view over
documentation that already exists, not new writing. See docs/20-in-app-docs.md.

Not /docs: that's FastAPI's own built-in Swagger UI route.

Resolved relative to this file's location (repo root, two directories up),
not the process's cwd — works identically whether run from a local dev
checkout or inside the container, where the Dockerfile COPYs docs/ to the
same relative layout (WORKDIR /app)."""

from dataclasses import dataclass
from pathlib import Path

import markdown

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"


@dataclass(frozen=True)
class DocPage:
    slug: str
    title: str
    path: Path


# Order matches the README's own doc index — PROJECT.md first as the
# zero-to-full-picture overview, then the build log 00-19 in sequence.
NAV: list[DocPage] = [
    DocPage("overview", "Project Overview", DOCS_DIR / "PROJECT.md"),
    DocPage("00-prerequisites", "Prerequisites", DOCS_DIR / "00-prerequisites.md"),
    DocPage("01-architecture", "Architecture", DOCS_DIR / "01-architecture.md"),
    DocPage("02-repo-layout", "Repo Layout", DOCS_DIR / "02-repo-layout.md"),
    DocPage("03-data-model", "Data Model", DOCS_DIR / "03-data-model.md"),
    DocPage("04-agents-spec", "Agents Spec", DOCS_DIR / "04-agents-spec.md"),
    DocPage("05-phase-1-foundation", "Phase 1: Foundation", DOCS_DIR / "05-phase-1-foundation.md"),
    DocPage("06-phase-2-generation", "Phase 2: Generation", DOCS_DIR / "06-phase-2-generation.md"),
    DocPage("07-phase-3-security", "Phase 3: Security", DOCS_DIR / "07-phase-3-security.md"),
    DocPage("08-phase-4-execution", "Phase 4: Execution", DOCS_DIR / "08-phase-4-execution.md"),
    DocPage("09-phase-5-orchestration", "Phase 5: Orchestration",
           DOCS_DIR / "09-phase-5-orchestration.md"),
    DocPage("10-phase-6-finops-ui", "Phase 6: FinOps + UI", DOCS_DIR / "10-phase-6-finops-ui.md"),
    DocPage("11-phase-7-polish-demo", "Phase 7: Polish + Demo",
           DOCS_DIR / "11-phase-7-polish-demo.md"),
    DocPage("12-testing-strategy", "Testing Strategy", DOCS_DIR / "12-testing-strategy.md"),
    DocPage("13-risks-and-cutlines", "Risks & Cutlines", DOCS_DIR / "13-risks-and-cutlines.md"),
    DocPage("14-command-reference", "Command Reference", DOCS_DIR / "14-command-reference.md"),
    DocPage("15-learning-path", "Learning Path", DOCS_DIR / "15-learning-path.md"),
    DocPage("16-decisions-log", "Decisions Log", DOCS_DIR / "16-decisions-log.md"),
    DocPage("17-pending-work", "Pending Work", DOCS_DIR / "17-pending-work.md"),
    DocPage("18-iac-generation", "IaC Generation", DOCS_DIR / "18-iac-generation.md"),
    DocPage("19-managed-clusters", "Managed Clusters", DOCS_DIR / "19-managed-clusters.md"),
    DocPage("20-in-app-docs", "This Docs Viewer", DOCS_DIR / "20-in-app-docs.md"),
]

_by_slug = {p.slug: p for p in NAV}

_MD_EXTENSIONS = ["fenced_code", "tables", "toc", "sane_lists"]


def get_page(slug: str) -> DocPage | None:
    return _by_slug.get(slug)


def render(page: DocPage) -> str:
    if not page.path.is_file():
        return "<p><em>This page is not available in the running container.</em></p>"
    text = page.path.read_text()
    return markdown.markdown(text, extensions=_MD_EXTENSIONS)
