"""Few-shot example retrieval. See docs/06-phase-2-generation.md §2.4.

5 curated examples today, not 15-25 — each one is machine-verified to satisfy
every hard requirement (generated from the same template functions the
deterministic fallback uses; see scripts/build_fewshot.py). With retrieval by
(language, framework) and 2 examples injected per prompt, a small verified set
outperforms a large unverified one at this context size."""

import json
from functools import lru_cache
from importlib.resources import files


@lru_cache
def load_fewshot() -> list[dict]:
    text = (files("deploymint") / "data/fewshot.jsonl").read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def select_fewshot(language: str, framework: str, k: int = 2) -> list[dict]:
    rows = load_fewshot()
    exact = [r for r in rows if r["language"] == language and r["framework"] == framework]
    same_lang = [r for r in rows if r["language"] == language and r not in exact]
    generic = [r for r in rows if r["language"] != language]
    return (exact + same_lang + generic)[:k]


def format_fewshot(language: str, framework: str, k: int = 2) -> str:
    examples = select_fewshot(language, framework, k)
    if not examples:
        return "(no examples available)"
    parts = []
    for i, ex in enumerate(examples, 1):
        parts.append(
            f"--- Example {i} ({ex['language']}/{ex['framework']}) ---\n"
            f"Dockerfile:\n{ex['dockerfile']}\n"
            f"k8s_deployment:\n{ex['k8s_deployment']}\n"
            f"Notes: {ex['notes']}"
        )
    return "\n\n".join(parts)
