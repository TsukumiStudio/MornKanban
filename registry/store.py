"""PC-wide MornKanban project registry: alias -> project root.

Lets `kanban send <alias> "title"` file a card into a registered project's
`.kanban/todo/` from any directory, any session, regardless of cwd.

Config lives under `KANBAN_CONFIG_DIR` or the XDG `mornkanban` directory.

python3 standard library only.
"""
import json
import os
import re
import time

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class RegistryError(Exception):
    """User-facing registry error (bad alias, bad path, corrupt store, ...)."""


def config_dir():
    override = os.environ.get("KANBAN_CONFIG_DIR")
    if override:
        return override
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = xdg if xdg else os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "mornkanban")


def registry_path():
    override = os.environ.get("KANBAN_PROJECTS_FILE")
    if override:
        return override
    return os.path.join(config_dir(), "projects.json")


class _FileLock:
    """mkdir-based lock, same technique as kanban.sh's merge_lock (bash 3.2 /
    stdlib-only environments can't rely on fcntl.flock behaving identically
    across platforms, but a directory create is atomic everywhere)."""

    def __init__(self, path, timeout=30):
        self.dir = path + ".lock"
        self.timeout = timeout

    def __enter__(self):
        os.makedirs(os.path.dirname(self.dir), exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                os.mkdir(self.dir)
                return self
            except FileExistsError:
                if time.time() > deadline:
                    raise RegistryError("registry lock timed out: %s" % self.dir)
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        try:
            os.rmdir(self.dir)
        except OSError:
            pass


def _empty():
    return {"projects": {}}


def load():
    path = registry_path()
    if not os.path.isfile(path):
        return _empty()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise RegistryError("registry file is unreadable or corrupt: %s (%s)" % (path, e))
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        raise RegistryError("registry file has an unexpected shape: %s" % path)
    return data


def _save_unlocked(data):
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def _validate_alias(alias):
    if not alias or not _SLUG_RE.match(alias):
        raise RegistryError(
            "invalid alias %r: use lowercase letters, digits, '-', '_', "
            "starting with a letter/digit, max 64 chars" % alias
        )


def _validate_project_path(path):
    if not os.path.isdir(path):
        raise RegistryError("not a directory: %s" % path)
    real = os.path.realpath(path)
    kanban_dir = os.path.join(real, ".kanban")
    if not os.path.isdir(kanban_dir):
        raise RegistryError(
            "%s has no .kanban directory (run `kanban init` there first)" % real
        )
    return real, os.path.realpath(kanban_dir)


def _find_alias_by_root(data, real_root, exclude_alias=None):
    for alias, info in data["projects"].items():
        if alias == exclude_alias:
            continue
        if info.get("root") == real_root:
            return alias
    return None


def add(alias, path, force=False):
    """Register a new alias. Rejects an existing alias or a path already
    registered under a different alias unless force=True."""
    _validate_alias(alias)
    real_root, real_kanban = _validate_project_path(path)
    with _FileLock(registry_path()):
        data = load()
        if alias in data["projects"] and not force:
            raise RegistryError(
                "alias %r is already registered (%s); use `kanban projects update` "
                "or pass --force to overwrite" % (alias, data["projects"][alias]["root"])
            )
        dup = _find_alias_by_root(data, real_root, exclude_alias=alias)
        if dup and not force:
            raise RegistryError(
                "%s is already registered as alias %r; use that alias or pass --force"
                % (real_root, dup)
            )
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry = data["projects"].get(alias) or {}
        entry.update({
            "alias": alias,
            "root": real_root,
            "kanban_dir": real_kanban,
            "updated_at": now,
        })
        entry.setdefault("added_at", now)
        data["projects"][alias] = entry
        _save_unlocked(data)
        return entry


def update(alias, path):
    """Repoint an existing alias at a (possibly new) path."""
    _validate_alias(alias)
    real_root, real_kanban = _validate_project_path(path)
    with _FileLock(registry_path()):
        data = load()
        if alias not in data["projects"]:
            raise RegistryError("alias %r is not registered; use `kanban projects add`" % alias)
        dup = _find_alias_by_root(data, real_root, exclude_alias=alias)
        if dup:
            raise RegistryError(
                "%s is already registered as alias %r" % (real_root, dup)
            )
        entry = data["projects"][alias]
        entry["root"] = real_root
        entry["kanban_dir"] = real_kanban
        entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save_unlocked(data)
        return entry


def remove(alias):
    with _FileLock(registry_path()):
        data = load()
        if alias not in data["projects"]:
            raise RegistryError("alias %r is not registered" % alias)
        del data["projects"][alias]
        _save_unlocked(data)


def get(alias):
    data = load()
    entry = data["projects"].get(alias)
    if entry is None:
        raise RegistryError(
            "alias %r is not registered (see `kanban projects list`)" % alias
        )
    return entry


def list_all():
    data = load()
    return dict(data["projects"])


def find_by_path(path):
    """Return the alias whose registered root matches `path` (or an ancestor
    of it), else None. Used to record where a `kanban send` was issued from."""
    try:
        real = os.path.realpath(path)
    except OSError:
        return None
    data = load()
    d = real
    while True:
        alias = _find_alias_by_root(data, d)
        if alias:
            return alias
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
