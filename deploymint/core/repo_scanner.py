"""Deterministic repo scanning: file walk, language/framework/entrypoint/port
detection, and tree-sitter import extraction. See docs/04-agents-spec.md §4.1."""

import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    ".git", ".svn", "node_modules", "venv", ".venv", "env", "__pycache__",
    "dist", "build", ".next", "target", "vendor", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "site-packages", ".tox", "coverage",
    ".deploymint",
}
MAX_FILES = 5000
MAX_FILE_BYTES = 1_000_000

MANIFEST_SIGNALS = {
    "pyproject.toml": ("python", "poetry-or-pep621"),
    "requirements.txt": ("python", "pip"),
    "Pipfile": ("python", "pipenv"),
    "uv.lock": ("python", "uv"),
    "package.json": ("javascript", "npm"),
    "pnpm-lock.yaml": ("javascript", "pnpm"),
    "yarn.lock": ("javascript", "yarn"),
    "go.mod": ("go", "go-mod"),
    "pom.xml": ("java", "maven"),
    "build.gradle": ("java", "gradle"),
    "Cargo.toml": ("rust", "cargo"),
}

FRAMEWORK_SIGNALS = {
    "python": [
        ("fastapi", "fastapi"), ("django", "django"), ("flask", "flask"),
        ("streamlit", "streamlit"), ("celery", "celery"),
    ],
    "javascript": [
        ("next", "nextjs"), ("nestjs", "nestjs"), ("express", "express"),
        ("fastify", "fastify"),
    ],
    "go": [("gin-gonic", "gin"), ("gofiber", "fiber"), ("labstack/echo", "echo")],
    "java": [("spring-boot", "spring-boot"), ("quarkus", "quarkus")],
}

FRAMEWORK_DEFAULT_PORT = {
    "fastapi": 8000, "flask": 5000, "django": 8000, "express": 3000,
    "nextjs": 3000, "nestjs": 3000, "fastify": 3000,
    "gin": 8080, "fiber": 8080, "echo": 8080,
    "spring-boot": 8080, "quarkus": 8080, "streamlit": 8501,
}

PORT_PATTERNS = [
    re.compile(r"port\s*=\s*(\d{2,5})", re.IGNORECASE),
    re.compile(r"PORT\D{0,5}(\d{2,5})"),
    re.compile(r"listen\(\s*(\d{2,5})"),
    re.compile(r"--port[= ](\d{2,5})"),
    re.compile(r"EXPOSE\s+(\d{2,5})"),
]

EXTENSION_LANGUAGE = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "javascript", ".tsx": "javascript", ".go": "go",
    ".java": "java", ".rs": "rust",
}

LANGUAGE_TS_NAME = {"python": "python", "javascript": "typescript", "go": "go", "java": "java"}


@dataclass
class ScanResult:
    files: list[Path] = field(default_factory=list)
    truncated: bool = False
    manifest_path: Path | None = None


def walk_repo(root: Path) -> ScanResult:
    """Recursive walk, skipping vendor/build dirs and enforcing size caps."""
    result = ScanResult()
    gitignore_patterns = _load_gitignore(root)

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if _gitignore_matches(rel, gitignore_patterns):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue

        result.files.append(path)
        if len(result.files) >= MAX_FILES:
            result.truncated = True
            break

    return result


def _load_gitignore(root: Path) -> list[str]:
    gi = root / ".gitignore"
    if not gi.exists():
        return []
    try:
        lines = gi.read_text(errors="ignore").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def _gitignore_matches(rel: Path, patterns: list[str]) -> bool:
    from fnmatch import fnmatch

    rel_str = str(rel)
    for pat in patterns:
        pat = pat.rstrip("/")
        if fnmatch(rel_str, pat) or fnmatch(rel.name, pat) or fnmatch(rel_str, f"*/{pat}"):
            return True
    return False


def detect_language(root: Path, files: list[Path]) -> tuple[str, str, Path | None]:
    """Manifest files beat extension counts. Returns (language, package_manager, manifest_path)."""
    for fname, (lang, pm) in MANIFEST_SIGNALS.items():
        candidate = root / fname
        if candidate.exists():
            return lang, pm, candidate

    counts: dict[str, int] = {}
    for f in files:
        lang = EXTENSION_LANGUAGE.get(f.suffix)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    if not counts:
        return "unknown", "unknown", None
    lang = max(counts, key=counts.get)
    return lang, "unknown", None


def detect_framework(language: str, manifest_path: Path | None, root: Path) -> str:
    """Read manifest content and match dependency names, in priority order."""
    if language not in FRAMEWORK_SIGNALS:
        return "unknown"

    text = ""
    candidates = [manifest_path] if manifest_path else []
    if language == "python":
        candidates += [root / "requirements.txt", root / "pyproject.toml", root / "Pipfile"]
    elif language == "javascript":
        candidates += [root / "package.json"]
    elif language == "go":
        candidates += [root / "go.mod"]
    elif language == "java":
        candidates += [root / "pom.xml", root / "build.gradle"]

    for c in candidates:
        if c and c.exists():
            try:
                text += c.read_text(errors="ignore").lower()
            except OSError:
                pass

    for needle, framework in FRAMEWORK_SIGNALS[language]:
        if needle in text:
            return framework
    return "unknown"


def find_entrypoint(root: Path, language: str, files: list[Path]) -> str:
    """Priority order per docs/04-agents-spec.md §4.1 Step 4."""
    if language == "python":
        pyproject = root / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text(errors="ignore")
                m = re.search(r"\[project\.scripts\]\s*\n\s*[\w.-]+\s*=\s*\"([^\"]+)\"", content)
                if m:
                    return m.group(1)
            except OSError:
                pass

    if language == "javascript":
        pkg = root / "package.json"
        if pkg.exists():
            try:
                import json
                data = json.loads(pkg.read_text(errors="ignore"))
                start = data.get("scripts", {}).get("start")
                if start:
                    return start
                main = data.get("main")
                if main:
                    return main
            except (OSError, ValueError):
                pass

    conventional = {
        "python": ["main.py", "app.py", "app/main.py", "src/main.py"],
        "javascript": ["server.js", "index.js", "src/index.js"],
        "go": ["main.go", "cmd/server/main.go"],
    }
    for rel in conventional.get(language, []):
        if (root / rel).exists():
            return rel

    if language == "go":
        for f in files:
            if f.name == "main.go":
                return str(f.relative_to(root))

    if language == "python":
        for f in files:
            if f.suffix != ".py":
                continue
            try:
                content = f.read_text(errors="ignore")
            except OSError:
                continue
            if "if __name__" in content and "__main__" in content:
                return str(f.relative_to(root))
            if re.search(r"=\s*(FastAPI|Flask)\s*\(", content):
                return str(f.relative_to(root))

    return ""


def infer_port(root: Path, framework: str, entrypoint: str) -> int:
    """Regex the entrypoint and common config files for a port; fall back to
    the framework's conventional default."""
    candidates = [root / entrypoint] if entrypoint else []
    candidates += [root / "Dockerfile", root / "docker-compose.yml", root / ".env"]

    for c in candidates:
        if not c.exists():
            continue
        try:
            content = c.read_text(errors="ignore")
        except OSError:
            continue
        for pattern in PORT_PATTERNS:
            m = pattern.search(content)
            if m:
                port = int(m.group(1))
                if 1 <= port <= 65535:
                    return port

    return FRAMEWORK_DEFAULT_PORT.get(framework, 8000)


def resolve_module(mod: str, current_file: Path, repo_root: Path) -> str | None:
    """Resolve an import string to a repo-relative file path, or None if external."""
    if mod.startswith("."):
        depth = len(mod) - len(mod.lstrip("."))
        base = current_file.parent
        for _ in range(depth - 1):
            base = base.parent
        rest = mod.lstrip(".").replace(".", "/")
        candidates = [base / f"{rest}.py", base / rest / "__init__.py"] if rest else [
            base / "__init__.py"
        ]
    else:
        rest = mod.replace(".", "/")
        candidates = [repo_root / f"{rest}.py", repo_root / rest / "__init__.py"]

    for c in candidates:
        if c.exists():
            try:
                return str(c.relative_to(repo_root))
            except ValueError:
                return None
    return None


def extract_python_imports(source: bytes) -> list[tuple[str, bool]]:
    """Returns [(module, is_relative), ...] using tree-sitter."""
    from tree_sitter_language_pack import get_parser

    parser = get_parser("python")
    tree = parser.parse(source)
    found: list[tuple[str, bool]] = []

    def walk(node):
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    found.append((source[child.start_byte:child.end_byte].decode(), False))
        elif node.type == "import_from_statement":
            for child in node.children:
                if child.type in ("dotted_name", "relative_import"):
                    text = source[child.start_byte:child.end_byte].decode()
                    found.append((text, text.startswith(".")))
                    break
        for c in node.children:
            walk(c)

    walk(tree.root_node)
    return found


def extract_js_imports(source: bytes) -> list[str]:
    """Returns raw import specifiers (relative or bare) for JS/TS files."""
    from tree_sitter_language_pack import get_parser

    parser = get_parser("typescript")
    tree = parser.parse(source)
    found: list[str] = []

    def walk(node):
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "string":
                    text = source[child.start_byte:child.end_byte].decode().strip("'\"")
                    found.append(text)
        elif node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn and source[fn.start_byte:fn.end_byte] == b"require":
                args = node.child_by_field_name("arguments")
                if args:
                    for c in args.children:
                        if c.type == "string":
                            found.append(source[c.start_byte:c.end_byte].decode().strip("'\""))
        for c in node.children:
            walk(c)

    walk(tree.root_node)
    return found


def detect_dependencies(language: str, manifest_path: Path | None, root: Path) -> list[str]:
    """Top-level external dependency names from the manifest."""
    deps: list[str] = []
    try:
        if language == "python":
            req = root / "requirements.txt"
            if req.exists():
                for line in req.read_text(errors="ignore").splitlines():
                    line = line.split("#")[0].strip()
                    if line and not line.startswith("-"):
                        name = re.split(r"[<>=!~\[]", line)[0].strip()
                        if name:
                            deps.append(name)
            elif manifest_path and manifest_path.name == "pyproject.toml":
                content = manifest_path.read_text(errors="ignore")
                deps = re.findall(r'"([\w-]+)(?:[<>=!~\[][^"]*)?"', content)[:50]
        elif language == "javascript":
            pkg = root / "package.json"
            if pkg.exists():
                import json
                data = json.loads(pkg.read_text(errors="ignore"))
                deps = list(data.get("dependencies", {}).keys())
    except (OSError, ValueError):
        pass
    return deps[:50]


def detect_microservices(root: Path) -> list[dict]:
    """A subdirectory is a service if it has its own manifest and isn't the repo
    root. docker-compose.yml services: keys are the highest-signal source."""
    services: list[dict] = []

    compose = root / "docker-compose.yml"
    if compose.exists():
        try:
            import yaml
            data = yaml.safe_load(compose.read_text(errors="ignore")) or {}
            for name, cfg in (data.get("services") or {}).items():
                build = cfg.get("build") if isinstance(cfg, dict) else None
                path = build.get("context") if isinstance(build, dict) else (build or ".")
                services.append({"name": name, "path": path, "port": None})
        except Exception:
            pass

    if services:
        return services

    manifest_names = set(MANIFEST_SIGNALS.keys()) | {"Dockerfile"}
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in SKIP_DIRS:
            continue
        if any((child / m).exists() for m in manifest_names):
            services.append({"name": child.name, "path": str(child.name), "port": None})

    return services
