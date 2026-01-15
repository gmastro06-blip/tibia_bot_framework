from __future__ import annotations

import argparse
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Tk harness window to verify LIVE INPUT MODE safely")
    parser.add_argument(
        "--title",
        default="TibiaClone Harness",
        help="Window title to allowlist in the bot UI (substring match)",
    )
    args = parser.parse_args()

    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(str(args.title))
    root.geometry("640x360")

    header = ttk.Label(
        root,
        text=(
            "LIVE INPUT HARNESS\n"
            "Focus this window, then arm live input in the bot UI and press 'Siguiente accion'.\n"
            "You should see injected keystrokes appear below."
        ),
        justify="left",
    )
    header.pack(fill="x", padx=10, pady=10)

    txt = tk.Text(root, height=12, width=80)
    txt.pack(fill="both", expand=True, padx=10)

    footer = ttk.Label(root, text="Keys received: 0")
    footer.pack(fill="x", padx=10, pady=(6, 10))

    state = {"n": 0}

    def log(line: str) -> None:
        txt.insert("end", line + "\n")
        txt.see("end")

    def on_key(event) -> None:
        state["n"] += 1
        footer.configure(text=f"Keys received: {state['n']}")
        ts = time.time()
        try:
            log(f"{ts:.6f} keypress keysym={event.keysym} keycode={event.keycode} char={repr(event.char)}")
        except Exception:
            log(f"{ts:.6f} keypress")

    def on_focus_in(_event) -> None:
        log(f"{time.time():.6f} focus_in")

    def on_focus_out(_event) -> None:
        log(f"{time.time():.6f} focus_out")

    root.bind("<KeyPress>", on_key)
    root.bind("<FocusIn>", on_focus_in)
    root.bind("<FocusOut>", on_focus_out)

    try:
        log("Ready. (Tip: allowlist this window title in the bot UI)")
    except Exception:
        pass

    root.mainloop()


if __name__ == "__main__":
    main()
