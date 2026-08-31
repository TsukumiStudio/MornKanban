"""Project discovery: find `.kanban` boards under configured search roots.

Safety rules:
- Only descend under explicit search roots (never scans the whole filesystem).
- Never descends into a `.kanban` directory once found, so `.kanban/wt/<id>`
  worktree checkouts (which contain their own copy of `.kanban`) are never
  listed as separate projects.
- Deduplicates by `os.path.realpath` so symlink loops cannot cause infinite
  recursion or duplicate project entries.
"""
import json
import os
import re

DEFAULT_ROOTS = ["~/git"]

# Directory names we never descend into, even if not hidden.
SKIP_DIR_NAMES = {"node_modules", "vendor", "__pycache__"}


def config_dir():
    override = os.environ.get("KANBAN_MONITOR_CONFIG_DIR")
    if override:
        return override
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "mornkanban")


def config_path():
    override = os.environ.get("KANBAN_MONITOR_CONFIG")
    if override:
        return override
    return os.path.join(config_dir(), "monitor.json")


def load_config():
    path = config_path()
    if not os.path.isfile(path):
        return {"roots": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"roots": []}
    if not isinstance(data, dict):
        return {"roots": []}
    roots = data.get("roots")
    if not isinstance(roots, list):
        roots = []
    return {"roots": [str(r) for r in roots]}


def save_config(config):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"roots": config.get("roots", [])}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def add_root(root):
    cfg = load_config()
    roots = cfg.get("roots", [])
    if root not in roots:
        roots.append(root)
    cfg["roots"] = roots
    save_config(cfg)
    return roots


def remove_root(root):
    cfg = load_config()
    roots = [r for r in cfg.get("roots", []) if r != root]
    cfg["roots"] = roots
    save_config(cfg)
    return roots


def resolve_roots(extra_roots=None):
    """Effective search roots: env override > config file > built-in default."""
    env = os.environ.get("KANBAN_MONITOR_ROOTS")
    if env:
        raw = [p for p in env.split(os.pathsep) if p]
    else:
        cfg = load_config()
        raw = cfg.get("roots") or list(DEFAULT_ROOTS)
    raw = list(raw) + list(extra_roots or [])

    seen = set()
    roots = []
    for r in raw:
        p = os.path.realpath(os.path.expanduser(r))
        if not os.path.isdir(p):
            continue
        if p in seen:
            continue
        seen.add(p)
        roots.append(p)
    return roots


def _should_skip_dir(name):
    if name in SKIP_DIR_NAMES:
        return True
    if name.startswith("."):
        # Hidden dirs (.git, .cache, .Trash, ...) are never descended into.
        # `.kanban` is handled separately below so its `wt/` worktrees are
        # never treated as independent projects.
        return True
    return False


def discover_projects(roots):
    """Return {realpath: {"root": realpath, "kanban_dir": realpath, "name": str}}."""
    projects = {}
    visited = set()
    for root in roots:
        _walk(root, visited, projects)
    return projects


def _walk(start, visited, projects):
    stack = [start]
    while stack:
        d = stack.pop()
        try:
            rp = os.path.realpath(d)
        except OSError:
            continue
        if rp in visited:
            continue
        visited.add(rp)

        kanban_dir = os.path.join(d, ".kanban")
        if os.path.isdir(kanban_dir):
            projects[rp] = {
                "root": rp,
                "kanban_dir": os.path.realpath(kanban_dir),
                "name": os.path.basename(rp) or rp,
            }

        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name == ".kanban":
                continue
            if _should_skip_dir(entry.name):
                continue
            stack.append(entry.path)


_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _slugify(name):
    slug = _SLUG_RE.sub("-", name).strip("-").lower()
    return slug or "project"


def build_registry(roots):
    """Return {slug: project_info} with stable, unique slugs (sorted by root path)."""
    raw = discover_projects(roots)
    registry = {}
    used = set()
    for rp in sorted(raw):
        info = raw[rp]
        base = _slugify(info["name"])
        slug = base
        n = 2
        while slug in used:
            slug = "%s-%d" % (base, n)
            n += 1
        used.add(slug)
        registry[slug] = dict(info, slug=slug)
    return registry
