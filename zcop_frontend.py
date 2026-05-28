"""
zcop_frontend.py — GUI frontend for the ZCOP PowerBI pipeline.

Select a ZCOP Excel file and click Extract to run extract_zcop.py.
CSV merge and PowerBI copy run silently in the background.

Run:  python zcop_frontend.py
"""

import datetime
import os
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE           = Path(__file__).resolve().parent
WATCH_FOLDER   = BASE / "Zcop Analysis"
EXTRACT_SCRIPT = BASE / "Core Python files" / "extract_zcop.py"
PYTHON_EXE     = BASE / ".venv" / "Scripts" / "python.exe"

# ── Colours (VS Code dark palette) ────────────────────────────────────────────
CLR_BG      = "#1e1e1e"
CLR_FG      = "#d4d4d4"
CLR_PANEL   = "#252526"
CLR_BORDER  = "#3c3c3c"
CLR_GREEN   = "#4ec9b0"
CLR_RED     = "#f48771"
CLR_YELLOW  = "#dcdcaa"
CLR_BLUE    = "#9cdcfe"
CLR_LIME    = "#b5cea8"
CLR_BTN     = "#0e639c"
CLR_BTN_HVR = "#1177bb"


def _get_zcop_files() -> list:
    """Return ZCOP xlsx filenames sorted newest-first."""
    try:
        files = sorted(
            (f for f in WATCH_FOLDER.glob("*.xlsx") if not f.name.startswith("~")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [f.name for f in files]
    except OSError:
        return []


# ── Application ───────────────────────────────────────────────────────────────
class ZcopApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("ZCOP PowerBI — Extract")
        self.geometry("720x480")
        self.minsize(600, 400)
        self.configure(bg=CLR_BG)

        self._extract_thread = None
        self._output_var = tk.StringVar(value="")
        # Maps display name (shown in combo) -> actual path passed to extract script
        self._file_paths: dict = {}

        self._apply_styles()
        self._build_ui()
        self._refresh_files()

    # ── Styles ────────────────────────────────────────────────────────────────
    def _apply_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=CLR_BG, foreground=CLR_FG,
                    fieldbackground=CLR_PANEL, bordercolor=CLR_BORDER,
                    troughcolor=CLR_PANEL, selectbackground=CLR_BTN,
                    selectforeground="white", font=("Segoe UI", 9))
        s.configure("TLabelframe",       background=CLR_BG, bordercolor=CLR_BORDER)
        s.configure("TLabelframe.Label", background=CLR_BG, foreground=CLR_BLUE,
                    font=("Segoe UI", 9, "bold"))
        s.configure("TButton",           background=CLR_BTN, foreground="white",
                    borderwidth=0, focusthickness=0, padding=(8, 4))
        s.map("TButton",
              background=[("active", CLR_BTN_HVR), ("disabled", CLR_PANEL)],
              foreground=[("disabled", "#666666")])
        s.configure("TCombobox",         fieldbackground=CLR_PANEL,
                    background=CLR_PANEL, foreground=CLR_FG, arrowcolor=CLR_FG)
        s.configure("TProgressbar",      background=CLR_GREEN, troughcolor=CLR_PANEL)

    # ── UI Construction ────────────────────────────────────────────────────────
    def _build_ui(self):
        PAD = dict(padx=12, pady=6)

        # ── File selector panel ───────────────────────────────────────────────
        ef = ttk.LabelFrame(self, text=" ZCOP File ", padding=(12, 10))
        ef.pack(fill="x", **PAD)
        ef.columnconfigure(1, weight=1)

        tk.Label(ef, text="File:", bg=CLR_BG, fg=CLR_FG,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self._file_var   = tk.StringVar()
        self._file_combo = ttk.Combobox(ef, textvariable=self._file_var,
                                        state="readonly", width=54)
        self._file_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        ttk.Button(ef, text="↻", command=self._refresh_files, width=3
                   ).grid(row=0, column=2, padx=(0, 8))

        ttk.Button(ef, text="📂  Browse…", command=self._browse_file, width=14
                   ).grid(row=0, column=3, padx=(0, 8))

        self._btn_extract = ttk.Button(ef, text="⚡  Run Extract",
                                       command=self._run_extract, width=16)
        self._btn_extract.grid(row=0, column=4)

        # ── Output folder row ──────────────────────────────────────────────
        tk.Label(ef, text="Also copy to:", bg=CLR_BG, fg=CLR_FG,
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))

        self._output_entry = ttk.Entry(ef, textvariable=self._output_var)
        self._output_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=(8, 0))

        ttk.Button(ef, text="📁  Browse Folder", command=self._browse_output, width=16
                   ).grid(row=1, column=3, columnspan=2, sticky="w", pady=(8, 0))

        self._progress = ttk.Progressbar(ef, mode="indeterminate")
        self._progress.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(10, 0))
        self._progress.grid_remove()

        tk.Label(ef, text=f"Source:  {WATCH_FOLDER}",
                 bg=CLR_BG, fg="#666666", font=("Segoe UI", 8)
                 ).grid(row=3, column=0, columnspan=5, sticky="w", pady=(6, 0))

        # ── Log panel ─────────────────────────────────────────────────────────
        lf = ttk.LabelFrame(self, text=" Output ", padding=(12, 8))
        lf.pack(fill="both", expand=True, **PAD)

        self._log = scrolledtext.ScrolledText(
            lf, state="disabled", font=("Consolas", 9),
            bg="#141414", fg=CLR_FG, insertbackground="white",
            wrap="word", relief="flat", borderwidth=0,
        )
        self._log.pack(fill="both", expand=True)

        self._log.tag_config("INFO",    foreground=CLR_BLUE)
        self._log.tag_config("WARNING", foreground=CLR_YELLOW)
        self._log.tag_config("ERROR",   foreground=CLR_RED)
        self._log.tag_config("OK",      foreground=CLR_GREEN)
        self._log.tag_config("UI",      foreground=CLR_LIME)

        ttk.Button(lf, text="Clear", command=self._clear_log
                   ).pack(side="right", pady=(6, 0))

        # ── Status bar ────────────────────────────────────────────────────────
        self._statusbar = tk.StringVar(value="Ready.")
        tk.Label(self, textvariable=self._statusbar, bg="#007acc", fg="white",
                 font=("Segoe UI", 8), anchor="w", padx=8
                 ).pack(fill="x", side="bottom", ipady=2)

    # ── File / folder choosers ───────────────────────────────────────────────
    def _browse_output(self):
        folder = filedialog.askdirectory(
            title="Select folder to copy combined CSV files to",
            initialdir=self._output_var.get() or str(BASE),
        )
        if folder:
            self._output_var.set(folder)
            self._statusbar.set(f"Output folder: {folder}")

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select ZCOP Excel file",
            initialdir=str(WATCH_FOLDER),
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
        )
        if not path:
            return
        chosen = Path(path)
        # Always show just the filename in the combo for readability.
        # Store the full path in _file_paths when the file is outside Zcop Analysis.
        try:
            is_in_watch = chosen.parent.resolve() == WATCH_FOLDER.resolve()
        except OSError:
            is_in_watch = False
        display = chosen.name
        self._file_paths[display] = display if is_in_watch else str(chosen)
        current = list(self._file_combo["values"])
        if display not in current:
            current.insert(0, display)
            self._file_combo["values"] = current
        self._file_var.set(display)
        loc = "Zcop Analysis" if is_in_watch else str(chosen.parent)
        self._statusbar.set(f"Selected: {display}  ({loc})")

    # ── File list ─────────────────────────────────────────────────────────────
    def _refresh_files(self):
        files = _get_zcop_files()
        self._file_combo["values"] = files
        # Bare filenames in Zcop Analysis resolve to themselves
        for f in files:
            self._file_paths[f] = f
        if files:
            self._file_combo.current(0)
        n = len(files)
        self._statusbar.set(f"Found {n} ZCOP file{'s' if n != 1 else ''}.")

    # ── Extract ───────────────────────────────────────────────────────────────
    def _run_extract(self):
        display_name = self._file_var.get().strip()
        if not display_name:
            messagebox.showwarning("No file selected", "Please select a ZCOP file first.")
            return
        # Resolve to full path if the file was browsed from outside Zcop Analysis
        filename = self._file_paths.get(display_name, display_name)
        if self._extract_thread and self._extract_thread.is_alive():
            messagebox.showinfo("Busy", "An extraction is already running — please wait.")
            return
        self._btn_extract.state(["disabled"])
        self._progress.grid()
        self._progress.start(12)
        display_name = Path(filename).name
        self._log_ui(f"Extracting: {display_name} …")
        output_folder = self._output_var.get().strip()
        if output_folder:
            self._log_ui(f"Output folder: {output_folder}", "INFO")
        self._statusbar.set(f"Extracting {display_name}…")
        self._extract_thread = threading.Thread(
            target=self._extract_worker, args=(filename, output_folder), daemon=True
        )
        self._extract_thread.start()

    def _extract_worker(self, filename: str, output_folder: str):
        display_name = Path(filename).name
        cmd = [str(PYTHON_EXE), str(EXTRACT_SCRIPT), filename]
        if output_folder:
            cmd += ["--output", output_folder]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                cwd=str(BASE),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()

            if result.returncode == 0:
                self.after(0, self._log_ui, f"✓  Done: {display_name}", "OK")
                for line in stdout.splitlines():
                    self.after(0, self._log_ui, f"   {line}", "INFO")
                self.after(0, self._statusbar.set, f"Extracted {display_name} successfully.")
            elif result.returncode == 2:
                self.after(0, self._log_ui,
                           f"⚠  Format validation failed — {display_name} skipped.", "WARNING")
                for line in stderr.splitlines():
                    self.after(0, self._log_ui, f"   {line}", "WARNING")
                self.after(0, self._statusbar.set, f"Format error — {display_name} skipped.")
            else:
                self.after(0, self._log_ui,
                           f"✗  Extraction failed (rc={result.returncode})", "ERROR")
                for line in (stdout + "\n" + stderr).strip().splitlines():
                    if line.strip():
                        self.after(0, self._log_ui, f"   {line}", "ERROR")
                self.after(0, self._statusbar.set, f"Extraction failed for {display_name}.")
        except Exception as exc:
            self.after(0, self._log_ui, f"Error: {exc}", "ERROR")
        finally:
            self.after(0, self._extract_done)

    def _extract_done(self):
        self._progress.stop()
        self._progress.grid_remove()
        self._btn_extract.state(["!disabled"])

    # ── Log helpers ───────────────────────────────────────────────────────────
    def _log_ui(self, message: str, tag: str = "UI"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._append_log(f"{ts}  {message}", tag)

    def _append_log(self, line: str, tag: str = "UI"):
        self._log.config(state="normal")
        self._log.insert("end", line + "\n", tag)
        self._log.see("end")
        self._log.config(state="disabled")

    def _clear_log(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = ZcopApp()
    app.mainloop()