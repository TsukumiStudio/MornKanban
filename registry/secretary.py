"""Per-project MornKanban secretary Herdr-agent name resolution.

Every project used to default its secretary agent to the fixed name
`secretary`, so two projects bootstrapped in the same Herdr environment
fought over one agent name (a real incident forced hand-picking
`secretary-kimekyawa` for a second project). This module derives a stable,
project-specific default instead - basic form `secretary-<project-slug>` -
and applies the documented override precedence:

  environment (KANBAN_HERDR_SECRETARY) > .git/kanban/KANBAN.md frontmatter
  (secretary_agent) > this generated default

python3 standard library only, matching the rest of MornKanban's
distribution constraints.
"""
import hashlib
import os
import re
import unicodedata

from registry import store

# Same shape as registry/store.py's alias pattern (a generated or overridden
# secretary name is a valid alias-like Herdr agent name too), but must start
# with a letter since "secretary-" always prefixes a generated slug.
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ALLOWED_SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
_PREFIX = "secretary-"
_MAX_NAME_LEN = 48
_MAX_SLUG_LEN = _MAX_NAME_LEN - len(_PREFIX)


class SecretaryNameError(Exception):
    """Invalid explicit override, or a root with no Git-common board."""


def validate_agent_name(name, what="secretary agent name"):
    if not name or not _NAME_RE.match(name):
        raise SecretaryNameError(
            "invalid %s %r: must match ^[a-z][a-z0-9_-]{0,63}$ "
            "(lowercase letters, digits, '-', '_', starting with a letter, "
            "max 64 chars)" % (what, name)
        )
    return name


def _slugify(text):
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _registry_alias_for_root(root):
    """Exact-match alias for this realpath'd root, if the project itself
    (not an ancestor) is registered. A corrupt/unreadable registry must not
    break secretary name resolution - fall back to no alias."""
    try:
        projects = store.list_all()
    except store.RegistryError:
        return None
    for alias, info in projects.items():
        if info.get("root") == root:
            return alias
    return None


def _stable_hash(root):
    return hashlib.sha256(root.encode("utf-8")).hexdigest()[:6]


def default_slug(root):
    """Deterministic project slug for `root` (an already realpath'd project
    root). Prefers a PC-wide registry alias for this exact root; otherwise
    slugifies the root's basename.

    A short stable hash of `root` is appended only when the basename had to
    be altered in a way that risks an accidental collision between
    *unrelated* projects: fully non-ASCII/symbol-only/empty names (which
    would otherwise all collapse to the same "project" placeholder) and
    names long enough to require truncation (which could otherwise collapse
    distinct long names onto the same truncated prefix).

    A plain ASCII basename that happens to match another project's basename
    is deliberately NOT disambiguated here - two projects both named
    "app" get the same generated default. That collision is documented
    behavior: it surfaces when Herdr agent registration for the second one
    fails (see kanban-secretary.sh bootstrap), with guidance to set an
    explicit `secretary_agent` override - the same fix used in production
    when `secretary-kimekyawa` was hand-picked.
    """
    alias = _registry_alias_for_root(root)
    needs_hash = False
    if alias:
        base = alias
    else:
        raw = os.path.basename(root.rstrip(os.sep)) or "project"
        base = _slugify(raw)
        if not base:
            base = "project"
            needs_hash = True
        elif not set(raw.lower()).issubset(_ALLOWED_SLUG_CHARS):
            needs_hash = True

    if len(base) > _MAX_SLUG_LEN:
        base = base[:_MAX_SLUG_LEN].rstrip("-")
        needs_hash = True

    if needs_hash:
        h = _stable_hash(root)
        budget = _MAX_SLUG_LEN - (len(h) + 1)
        if len(base) > budget:
            base = base[:budget].rstrip("-")
        base = "%s-%s" % (base, h) if base else h

    return base or _stable_hash(root)


def default_name(root):
    return _PREFIX + default_slug(root)


def _read_frontmatter_override(kanban_md_path):
    """Minimal reader for the simple `key: value` frontmatter kanban.sh's
    fm_get/cmd_init produce; not a general YAML parser (matches
    registry/cli.py's _read_frontmatter)."""
    if not os.path.isfile(kanban_md_path):
        return None
    with open(kanban_md_path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() == "secretary_agent":
            value = value.strip()
            return value or None
    return None


def resolve(root, env_override=None):
    """Resolve the secretary agent name for `root` (an existing project
    Git project root containing a board). Returns (name, source) where source is one
    of "environment", "kanban_md", "generated".

    Raises SecretaryNameError for an invalid explicit override (env or
    KANBAN.md) - it never silently substitutes a different name in that
    case, so a typo'd override fails loudly instead of quietly colliding
    with (or silently diverging from) another project.
    """
    try:
        root, kanban_dir = store.project_paths(root)
    except store.RegistryError as exc:
        raise SecretaryNameError(str(exc))

    if env_override:
        return validate_agent_name(env_override, "KANBAN_HERDR_SECRETARY"), "environment"

    md_override = _read_frontmatter_override(os.path.join(kanban_dir, "KANBAN.md"))
    if md_override:
        return validate_agent_name(md_override, "KANBAN.md secretary_agent"), "kanban_md"

    return default_name(root), "generated"
