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
    check_deps,
    cli_installed,
    run_setup,
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
    root.minsize(480, 360)

    deps_var = tk.StringVar()
    step1_var = tk.StringVar()
    step2_var = tk.StringVar()
    result_var = tk.StringVar()

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

    def on_run_setup():
        try:
            messages = run_setup()
        except OSError as exc:
            messagebox.showerror("セットアップ実行", str(exc))
            return
        result_var.set("\n".join(messages))
        refresh()

    frame = tk.Frame(root, padx=12, pady=12)
    frame.pack(fill=tk.BOTH, expand=True)

    tk.Label(frame, textvariable=deps_var, anchor="w").pack(fill=tk.X, pady=(0, 8))

    tk.Label(frame, text="kanban CLI", anchor="w", font=("", 10, "bold")).pack(fill=tk.X)
    tk.Label(frame, textvariable=step1_var, anchor="w").pack(fill=tk.X, pady=(0, 8))

    tk.Label(frame, text="Claude Code スキル", anchor="w", font=("", 10, "bold")).pack(fill=tk.X)
    tk.Label(frame, textvariable=step2_var, anchor="w").pack(fill=tk.X, pady=(0, 8))

    tk.Button(frame, text="セットアップ実行", command=on_run_setup).pack(fill=tk.X, pady=(0, 8))

    tk.Label(
        frame,
        textvariable=result_var,
        anchor="w",
        wraplength=440,
        justify=tk.LEFT,
    ).pack(fill=tk.X, pady=(0, 8))

    tk.Label(
        frame,
        text="導入後はプロジェクトのペインで claude に「kanban の秘書として待機して」",
        anchor="w",
        wraplength=440,
        justify=tk.LEFT,
    ).pack(fill=tk.X, pady=(8, 8))

    tk.Button(frame, text="閉じる", command=root.destroy).pack(fill=tk.X)

    refresh()
    root.mainloop()


if __name__ == "__main__":
    main()
