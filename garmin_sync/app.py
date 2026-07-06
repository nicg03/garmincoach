"""
Garmin Sync -- a minimalistic desktop app.

Run it with:  python -m garmin_sync.app   (or the Garmin Sync.command launcher)

It wraps garmin_sync.core:
  - Log in        one-time visible sign-in (email / password / MFA)
  - Fill the blank pull from the last stored day up to today  (main action)
  - Fetch range   pull the last N days
  - Full history  write ONE combined JSON of the entire local history

Long operations run in a worker thread; the browser/Playwright work never
touches Tk's main loop. Log lines flow back through a thread-safe queue that
the UI drains on a timer.
"""
from __future__ import annotations

import queue
import threading
from datetime import date, timedelta
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

from . import core

# ---- palette (soft, minimal, light) ----------------------------------------
BG = "#f5f6f8"
CARD = "#ffffff"
INK = "#1c2530"
MUTED = "#8b95a1"
ACCENT = "#2f6df6"
ACCENT_DK = "#2457cc"
OK = "#1f9d5b"
LINE = "#e6e9ee"


class GarminApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.busy = False

        root.title("Garmin Sync")
        root.configure(bg=BG)
        root.geometry("560x560")
        root.minsize(520, 520)

        self._build_style()
        self._build_ui()
        self._refresh_status()
        self.root.after(120, self._drain_queue)

    # -- styling --------------------------------------------------------------
    def _build_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=BG, foreground=INK, font=("Helvetica", 12))
        st.configure("Card.TLabel", background=CARD, foreground=INK, font=("Helvetica", 12))
        st.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Helvetica", 11))
        st.configure("Title.TLabel", background=BG, foreground=INK, font=("Helvetica", 20, "bold"))
        st.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Helvetica", 11))

        st.configure("Accent.TButton", font=("Helvetica", 12, "bold"),
                     foreground="#ffffff", background=ACCENT, borderwidth=0, padding=(14, 10))
        st.map("Accent.TButton",
               background=[("active", ACCENT_DK), ("disabled", "#a9c0f7")])
        st.configure("Ghost.TButton", font=("Helvetica", 12),
                     foreground=INK, background=CARD, borderwidth=1, padding=(12, 9))
        st.map("Ghost.TButton",
               background=[("active", "#eef1f6"), ("disabled", "#f3f4f6")],
               bordercolor=[("!disabled", LINE)])

    # -- layout ---------------------------------------------------------------
    def _build_ui(self):
        pad = 18
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=pad, pady=(pad, 6))
        ttk.Label(header, text="Garmin Sync", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Pull your Garmin data to this folder.",
                  style="Sub.TLabel").pack(anchor="w")

        # status card
        card = tk.Frame(self.root, bg=CARD, highlightbackground=LINE,
                        highlightthickness=1)
        card.pack(fill="x", padx=pad, pady=8)
        self.lbl_last = ttk.Label(card, text="Last data: —", style="Card.TLabel")
        self.lbl_last.pack(anchor="w", padx=14, pady=(12, 2))
        self.lbl_counts = ttk.Label(card, text="History: —", style="Muted.TLabel")
        self.lbl_counts.pack(anchor="w", padx=14, pady=(0, 12))

        # primary action
        actions = ttk.Frame(self.root)
        actions.pack(fill="x", padx=pad, pady=(2, 4))
        self.btn_fill = ttk.Button(actions, text="Fill the blank  →  today",
                                   style="Accent.TButton", command=self.on_fill)
        self.btn_fill.pack(fill="x")

        # secondary row: fetch range
        row = ttk.Frame(self.root)
        row.pack(fill="x", padx=pad, pady=(8, 4))
        ttk.Label(row, text="or fetch the last", style="Sub.TLabel").pack(side="left")
        self.days_var = tk.StringVar(value="14")
        self.spin = tk.Spinbox(row, from_=1, to=365, width=4, textvariable=self.days_var,
                               font=("Helvetica", 12), justify="center",
                               relief="solid", bd=1, highlightthickness=0)
        self.spin.pack(side="left", padx=6)
        ttk.Label(row, text="days", style="Sub.TLabel").pack(side="left")
        self.btn_fetch = ttk.Button(row, text="Fetch", style="Ghost.TButton",
                                    command=self.on_fetch_range)
        self.btn_fetch.pack(side="right")

        # tertiary row: history + login
        row2 = ttk.Frame(self.root)
        row2.pack(fill="x", padx=pad, pady=(4, 6))
        self.btn_history = ttk.Button(row2, text="Combine full history → 1 file",
                                      style="Ghost.TButton", command=self.on_history)
        self.btn_history.pack(side="left")
        self.btn_login = ttk.Button(row2, text="Log in", style="Ghost.TButton",
                                    command=self.on_login)
        self.btn_login.pack(side="right")

        # options
        opts = ttk.Frame(self.root)
        opts.pack(fill="x", padx=pad, pady=(0, 4))
        self.watch_var = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(opts, text="Watch the browser while fetching",
                            variable=self.watch_var, bg=BG, fg=MUTED,
                            activebackground=BG, selectcolor=CARD,
                            font=("Helvetica", 10), highlightthickness=0, bd=0)
        cb.pack(anchor="w")

        # log pane
        logwrap = tk.Frame(self.root, bg=CARD, highlightbackground=LINE,
                           highlightthickness=1)
        logwrap.pack(fill="both", expand=True, padx=pad, pady=(6, 8))
        self.log = tk.Text(logwrap, height=8, wrap="word", relief="flat",
                           bg=CARD, fg=INK, font=("Menlo", 10), padx=12, pady=10,
                           highlightthickness=0, state="disabled")
        self.log.pack(fill="both", expand=True)

        # progress + footer
        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.footer = ttk.Label(self.root, text="Ready", style="Sub.TLabel")
        self.footer.pack(side="bottom", anchor="w", padx=pad, pady=(0, 10))

    # -- helpers --------------------------------------------------------------
    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_busy(self, busy: bool, footer: str = ""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_fill, self.btn_fetch, self.btn_history, self.btn_login):
            b.configure(state=state)
        if busy:
            self.progress.pack(fill="x", padx=18, pady=(0, 4), before=self.footer)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
        if footer:
            self.footer.configure(text=footer)

    def _refresh_status(self):
        last = core.last_pulled_date()
        counts = core.db_counts()
        if last:
            gap = (date.today() - date.fromisoformat(last)).days
            when = "today" if gap == 0 else ("yesterday" if gap == 1 else f"{gap} days ago")
            self.lbl_last.configure(text=f"Last data: {last}  ({when})")
        else:
            self.lbl_last.configure(text="Last data: none yet — log in, then Fill the blank")
        self.lbl_counts.configure(
            text=f"History: {counts['activities']} activities · {counts['days']} days stored")

    # -- worker plumbing ------------------------------------------------------
    def _run(self, fn, done_msg: str):
        if self.busy:
            return
        self._set_busy(True, "Working…")

        def worker():
            try:
                fn(lambda m: self.q.put(("log", m)))
                self.q.put(("done", done_msg))
            except Exception as e:  # noqa: BLE001 - surfaced to the user
                self.q.put(("error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "done":
                    self._set_busy(False, str(payload))
                    self._refresh_status()
                elif kind == "error":
                    self._set_busy(False, "Error")
                    self._log("✗ " + str(payload))
                    messagebox.showerror("Garmin Sync", str(payload))
        except queue.Empty:
            pass
        self.root.after(120, self._drain_queue)

    def _headless(self) -> bool:
        return not self.watch_var.get()

    # -- button handlers ------------------------------------------------------
    def on_login(self):
        self._run(lambda log: core.login(fresh=False, log=log), "Login complete")

    def on_fill(self):
        headless = self._headless()
        self._run(lambda log: core.fill_the_blank(headless=headless, log=log),
                  "Filled up to today")

    def on_fetch_range(self):
        try:
            n = max(1, int(self.days_var.get()))
        except ValueError:
            messagebox.showwarning("Garmin Sync", "Enter a whole number of days.")
            return
        since = date.today() - timedelta(days=n - 1)
        headless = self._headless()
        self._run(lambda log: core.pull(since=since, headless=headless, log=log),
                  f"Fetched last {n} days")

    def on_history(self):
        self._run(lambda log: core.export_history(log=log), "Combined history written")


def main():
    root = tk.Tk()
    GarminApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
