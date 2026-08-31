#!/usr/bin/env python3
"""MornKanban environment setup GUI (native, tkinter).

Standard library only. UI frontend over setup_core's logic functions.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from setup_core import (  # noqa: E402
    add_project,
    check_deps,
    cli_installed,
    init_project,
    install_cli,
    install_skill,
    load_projects,
    skill_installed,
)


# --- UI ----------------------------------------------------------------

def main():
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        sys.stderr.write("python3 の tkinter が必要\n")
        sys.exit(2)

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
