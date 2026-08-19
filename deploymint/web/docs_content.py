"""Renders docs/user-guide/*.md as browsable in-app pages at /guide — a
GitBook-style sidebar + page view of genuine end-user documentation: what
DeployMint does, how to use it, and its capabilities. See
docs/24-landing-and-docs.md.

This used to render docs/PROJECT.md + the numbered build-log docs (00-23) —
this project's own contributor-facing build history, not something a user
deploying their own repo needs. That content still exists on disk under
docs/*.md (nothing was deleted), it just isn't reachable from /guide anymore.

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
USER_GUIDE_DIR = DOCS_DIR / "user-guide"


@dataclass(frozen=True)
class DocPage:
    slug: str
    title: str
    path: Path


NAV: list[DocPage] = [
    DocPage("overview", "Overview", USER_GUIDE_DIR / "00-overview.md"),
    DocPage("getting-started", "Getting Started", USER_GUIDE_DIR / "01-getting-started.md"),
    DocPage("agents", "The Agent Pipeline", USER_GUIDE_DIR / "02-agents.md"),
    DocPage("iac-formats", "Generated Artifacts", USER_GUIDE_DIR / "03-iac-formats.md"),
    DocPage("security", "Security & Compliance", USER_GUIDE_DIR / "04-security.md"),
    DocPage("cost-tracking", "Cost Tracking", USER_GUIDE_DIR / "05-cost-tracking.md"),
    DocPage("cloud-deploy", "Cloud Deploy", USER_GUIDE_DIR / "06-cloud-deploy.md"),
    DocPage("chat-assistant", "Chat Assistant", USER_GUIDE_DIR / "07-chat-assistant.md"),
    DocPage("cli", "CLI Reference", USER_GUIDE_DIR / "08-cli.md"),
    DocPage("faq", "FAQ", USER_GUIDE_DIR / "09-faq.md"),
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
