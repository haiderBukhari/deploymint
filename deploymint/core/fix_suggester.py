"""LLM-suggested fixes for a single security finding. See docs/28-ai-fix.md.

Goes through core.llm.complete() like every other LLM call in the app, so
whichever provider/model is configured (Anthropic, OpenAI, a local
OpenAI-compatible runtime) works here with no special-casing.

The diff is computed here with stdlib difflib, never asked of the model —
an LLM-authored diff would be one more thing that could be subtly wrong,
and the point of showing a diff is to be trustworthy about what changed.
"""

import difflib

from deploymint.core import llm, prompts

_FENCE_LANGS = ("dockerfile", "yaml", "yml", "hcl", "terraform", "json", "")


def _strip_fences(text: str) -> str:
    """Models wrap file content in ``` fences despite being told not to.
    Strip one leading/trailing fence pair if present, leaving inner content
    untouched (a Dockerfile HEREDOC could legitimately contain backticks)."""
    lines = text.strip().splitlines()
    if not lines or not lines[0].startswith("```"):
        return text.strip()
    lang = lines[0][3:].strip().lower()
    if lang not in _FENCE_LANGS:
        return text.strip()
    if len(lines) > 1 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return "\n".join(lines[1:]).strip()


def build_diff(filename: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    ))


async def suggest_fix(finding: dict, content: str, filename: str) -> dict:
    """Returns {"suggested_content", "diff", "changed"}. Raises whatever
    llm.complete() raises — the caller (the API route) turns that into a
    clean HTTP error, since unlike the Warden's optional explanations this
    IS the whole point of the request, so failing silently would be wrong."""
    raw = await llm.complete(
        system="You fix security findings in infrastructure-as-code files.",
        user=prompts.FIX_SUGGESTION_PROMPT.format(
            filename=filename,
            finding_id=finding.get("id", "?"),
            message=finding.get("message", ""),
            remediation=finding.get("remediation", "(none given)"),
            content=content,
        ),
        max_tokens=4000,
    )
    suggested = _strip_fences(raw)
    return {
        "suggested_content": suggested,
        "diff": build_diff(filename, content, suggested),
        "changed": suggested.strip() != content.strip(),
    }
