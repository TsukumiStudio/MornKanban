#!/usr/bin/env python3
"""MornKanban environment setup GUI (native, tkinter).

Standard library only. Ports gui/server.py's logic to local function calls
with a tkinter UI instead of an HTTP server.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
KANBAN_SH = os.path.join(REPO, "kanban.sh")
CONFIG = os.path.expanduser("~/.config/mornkanban/gui.json")
LOCAL_BIN = os.path.expanduser("~/.local/bin")
KANBAN_LINK = os.path.join(LOCAL_BIN, "kanban")
SKILL_DIR = os.path.expanduser("~/.claude/skills/kanban-dispatch")
SKILL_PATH = os.path.join(SKILL_DIR, "SKILL.md")
TIMEOUT = 30

SKILL_TEMPLATE = """---
name: kanban-dispatch
description: "File-based kanban dispatch: card every implementation request and run the background dispatcher. Use when assigned implementation work in a project with .kanban/, or when asked to set up or operate kanban dispatch."
user_invocable: true
---
# kanban-dispatch
The kanban CLI and the full workflow contract live in {repo}.
**Read {repo}/README.md and follow it** (Secretary Bootstrap, Dialogue-Agent Contract, Model Policy, Herdr Integration).
"""


# --- logic functions (UI-independent) --------------------------------------

def check_deps():
    which = shutil.which
    return {
        "herdr": bool(which("herdr")),
        "claude": bool(which("claude")),
        "codex": bool(which("codex")),
    }


def cli_installed():
    return bool(shutil.which("kanban")) or os.path.exists(KANBAN_LINK)


def skill_installed():
    return os.path.isfile(SKILL_PATH)


def in_worktree():
    return "/.kanban/wt/" in REPO


def path_contains(directory):
    path_env = os.environ.get("PATH", "")
    parts = path_env.split(os.pathsep)
    directory = directory.rstrip(os.sep)
    return any(os.path.normpath(p) == os.path.normpath(directory) for p in parts if p)


def install_cli():
    if in_worktree():
        return False, "refusing to install from a kanban worktree; run from the real checkout"
    os.makedirs(LOCAL_BIN, exist_ok=True)
    if os.path.lexists(KANBAN_LINK):
        if not os.path.islink(KANBAN_LINK):
            return False, "%s exists and is not a symlink" % KANBAN_LINK
        os.remove(KANBAN_LINK)
    os.symlink(KANBAN_SH, KANBAN_LINK)
    msg = "installed: %s -> %s" % (KANBAN_LINK, KANBAN_SH)
    if not path_contains(LOCAL_BIN):
        msg += "\n警告: %s が PATH に含まれていません" % LOCAL_BIN
    return True, msg


def install_skill(force=False):
    if in_worktree():
        return False, "refusing to install from a kanban worktree; run from the real checkout"
    if os.path.isfile(SKILL_PATH) and not force:
        return False, "already installed"
    os.makedirs(SKILL_DIR, exist_ok=True)
    content = SKILL_TEMPLATE.format(repo=REPO)
    with open(SKILL_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)
    return True, "installed: %s" % SKILL_PATH


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"projects": []}
    projects = data.get("projects")
    if not isinstance(projects, list):
        projects = []
    return {"projects": [p for p in projects if isinstance(p, str)]}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def project_entry(path):
    return {
        "path": path,
        "name": os.path.basename(path.rstrip(os.sep)) or path,
        "has_kanban": os.path.isdir(os.path.join(path, ".kanban")),
    }


def load_projects():
    cfg = load_config()
    return [project_entry(p) for p in cfg["projects"] if os.path.isdir(p)]


def add_project(path):
    if not isinstance(path, str) or not path.strip():
        return False, "path is required"
    norm = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.isdir(norm):
        return False, "not a directory: %s" % norm
    cfg = load_config()
    if norm not in cfg["projects"]:
        cfg["projects"].append(norm)
        save_config(cfg)
    return True, "added: %s" % norm


def init_project(path):
    if not os.path.isdir(path):
        return False, "not a directory: %s" % path
    try:
        proc = subprocess.run(
            ["bash", KANBAN_SH, "init", path],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "command timed out"
    except OSError as exc:
        return False, "failed to run kanban.sh: %s" % exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, "command failed (%d): %s" % (proc.returncode, detail)
    return True, "initialized: %s" % path


# --- UI ----------------------------------------------------------------

def main():
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        sys.stderr.write("python3 の tkinter が必要\n")
        sys.exit(1)

    root = tk.Tk()
    root.title("MornKanban Setup")
    root.minsize(520, 480)

    deps_var = tk.StringVar()
    step1_var = tk.StringVar()
    step2_var = tk.StringVar()
    path_var = tk.StringVar()

    def refresh():
        deps = check_deps()
        deps_var.set(
            "herdr: %s  claude: %s  codex: %s"
            % (
                "✓" if deps["herdr"] else "✗",
                "✓" if deps["claude"] else "✗",
                "✓" if deps["codex"] else "✗",
            )
        )
        step1_var.set("kanban CLI: %s" % ("導入済み" if cli_installed() else "未導入"))
        step2_var.set("Claude Code スキル: %s" % ("導入済み" if skill_installed() else "未導入"))
        refresh_projects()

    def refresh_projects():
        listbox.delete(0, tk.END)
        for p in load_projects():
            mark = "✓" if p["has_kanban"] else "✗"
            listbox.insert(tk.END, "[%s] %s (%s)" % (mark, p["name"], p["path"]))

    def on_install_cli():
        ok, msg = install_cli()
        if ok:
            messagebox.showinfo("kanban CLI", msg)
        else:
            messagebox.showerror("kanban CLI", msg)
        refresh()

    def on_install_skill():
        force = False
        if skill_installed():
            force = messagebox.askyesno("Claude Code スキル", "既に導入済みです。上書きしますか?")
            if not force:
                return
        ok, msg = install_skill(force=force)
        if ok:
            messagebox.showinfo("Claude Code スキル", msg)
        else:
            messagebox.showerror("Claude Code スキル", msg)
        refresh()

    def on_add_project():
        ok, msg = add_project(path_var.get())
        if ok:
            path_var.set("")
        else:
            messagebox.showerror("プロジェクト追加", msg)
        refresh_projects()

    def on_init_selected():
        sel = listbox.curselection()
        if not sel:
            messagebox.showerror("kanban init", "プロジェクトを選択してください")
            return
        projects = load_projects()
        p = projects[sel[0]]
        ok, msg = init_project(p["path"])
        if ok:
            messagebox.showinfo("kanban init", msg)
        else:
            messagebox.showerror("kanban init", msg)
        refresh_projects()

    frame = tk.Frame(root, padx=12, pady=12)
    frame.pack(fill=tk.BOTH, expand=True)

    tk.Label(frame, textvariable=deps_var, anchor="w").pack(fill=tk.X, pady=(0, 8))

    tk.Label(frame, text="Step 1: kanban CLI", anchor="w", font=("", 10, "bold")).pack(fill=tk.X)
    tk.Label(frame, textvariable=step1_var, anchor="w").pack(fill=tk.X)
    tk.Button(frame, text="kanban CLI をインストール", command=on_install_cli).pack(fill=tk.X, pady=(0, 8))

    tk.Label(frame, text="Step 2: Claude Code スキル", anchor="w", font=("", 10, "bold")).pack(fill=tk.X)
    tk.Label(frame, textvariable=step2_var, anchor="w").pack(fill=tk.X)
    tk.Button(frame, text="Claude Code スキルを導入", command=on_install_skill).pack(fill=tk.X, pady=(0, 8))

    tk.Label(frame, text="Step 3: プロジェクト", anchor="w", font=("", 10, "bold")).pack(fill=tk.X)
    path_frame = tk.Frame(frame)
    path_frame.pack(fill=tk.X)
    tk.Entry(path_frame, textvariable=path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
    tk.Button(path_frame, text="追加", command=on_add_project).pack(side=tk.LEFT, padx=(4, 0))

    listbox = tk.Listbox(frame, height=8)
    listbox.pack(fill=tk.BOTH, expand=True, pady=(4, 4))
    tk.Button(frame, text="選択を kanban init", command=on_init_selected).pack(fill=tk.X, pady=(0, 8))

    tk.Label(
        frame,
        text="導入後は Herdr のペインで claude を起動し「kanban の秘書として待機して」と一言",
        anchor="w",
        wraplength=480,
        justify=tk.LEFT,
    ).pack(fill=tk.X, pady=(8, 0))

    refresh()
    root.mainloop()


if __name__ == "__main__":
    main()
