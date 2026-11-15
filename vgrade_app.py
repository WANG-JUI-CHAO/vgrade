"""
vgrade 桌面應用程式（Windows）/ vgrade desktop app

把 .v / .sv 檔（或整個 Vivado 專案資料夾）拖進視窗，按「開始評分」，
即可看到分數並開啟完整的 HTML 評分報告。介面可切換繁體中文 / English。

開發時執行：python vgrade_app.py
打包：python build_exe.py
"""
from __future__ import annotations

import datetime
import json
import os
import queue
import sys
import threading
import traceback
from pathlib import Path


def _special_entry() -> None:
    """打包後的 exe 也負責執行內建 Yosys 與命令列模式。"""
    if len(sys.argv) > 1 and sys.argv[1] == "--run-yosys":
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w")
        import yowasp_yosys
        sys.exit(yowasp_yosys.run_yosys(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        import vgrade
        sys.exit(vgrade.main(sys.argv[2:]))


_special_entry()

import tkinter as tk                      # noqa: E402
from tkinter import filedialog, messagebox, ttk   # noqa: E402

import vgrade                             # noqa: E402
from i18n import LANG_NAMES, LANGS, detect_lang, get_lang, set_lang, t   # noqa: E402
from report_html import render_html       # noqa: E402

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:                          # 拖放套件不存在時仍可用按鈕選檔
    HAS_DND = False

APP_NAME = "vgrade"
VERILOG_EXT = {".v", ".sv", ".vh", ".svh"}
SKIP_DIR_PARTS = (".sim", ".gen", ".ip_user_files", ".cache", ".hw", ".runs", "__pycache__", ".git")
TARGETS = ("fpga", "asic")

FONT = "Microsoft JhengHei UI"
C_BG = "#f9f9f7"
C_SURFACE = "#fcfcfb"
C_INK = "#0b0b0b"
C_INK2 = "#52514e"
C_MUTED = "#898781"
C_LINE = "#c3c2b7"
C_ACCENT = "#2a78d6"
GRADE_COLOR = {"A": "#0ca30c", "B": "#0ca30c", "C": "#b07800", "D": "#b07800", "F": "#d03b3b"}


def resource_path(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / rel


def app_dir() -> Path:
    d = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = app_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_settings() -> dict:
    try:
        return json.loads((app_dir() / "settings.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        (app_dir() / "settings.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _looks_like_tb(stem: str) -> bool:
    s = stem.lower()
    return s in ("tb", "testbench") or s.startswith(("tb_", "test_")) or s.endswith(("_tb", "_test"))


def collect_verilog(paths: list[str]) -> list[Path]:
    """展開拖進來的檔案與資料夾，排除 Vivado 產生的模擬檔與 testbench。"""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if (f.suffix.lower() in VERILOG_EXT and f.is_file()
                        and not any(part.endswith(SKIP_DIR_PARTS) or part in SKIP_DIR_PARTS for part in f.parts)
                        and f.name.lower() != "glbl.v"
                        and not _looks_like_tb(f.stem)):
                    out.append(f)
        elif p.is_file() and p.suffix.lower() in VERILOG_EXT:
            out.append(p)
    return out


class App:
    def __init__(self) -> None:
        self.settings = load_settings()
        set_lang(self.settings.get("lang") or detect_lang())

        self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        self.root.geometry("740x830")
        self.root.minsize(620, 660)
        self.root.configure(bg=C_BG)
        try:
            self.root.iconbitmap(str(resource_path("assets/vgrade.ico")))
        except Exception:
            pass

        self.files: list[Path] = []
        self.modules: list[str] = []
        self.top_choice: str | None = None          # None = 自動判斷
        self.target = "fpga"
        self.result = None                          # (Grader, top, report_path)
        self.msgs: queue.Queue = queue.Queue()
        self.busy = False

        self._styles()
        self._build()
        self.root.after(100, self._poll)

    # ------------------------------------------------------------ UI
    def _styles(self) -> None:
        st = ttk.Style(self.root)
        try:
            st.theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            st.theme_use("clam")
        st.configure(".", font=(FONT, 10))
        st.configure("TFrame", background=C_BG)
        st.configure("Card.TFrame", background=C_SURFACE)
        st.configure("TLabel", background=C_BG, foreground=C_INK)
        st.configure("Title.TLabel", font=(FONT, 16, "bold"))
        st.configure("Sub.TLabel", foreground=C_INK2)
        st.configure("Muted.TLabel", foreground=C_MUTED, font=(FONT, 9))
        st.configure("CardMuted.TLabel", background=C_SURFACE, foreground=C_MUTED, font=(FONT, 9))
        st.configure("Score.TLabel", background=C_SURFACE, font=(FONT, 34, "bold"))
        st.configure("Grade.TLabel", background=C_SURFACE, font=(FONT, 14, "bold"))
        st.configure("Accent.TButton", font=(FONT, 11, "bold"), padding=(18, 8))
        st.configure("Treeview", rowheight=26, font=(FONT, 10))
        st.configure("Treeview.Heading", font=(FONT, 9, "bold"))

    def _build(self) -> None:
        """建立（或語言切換後重建）所有元件。"""
        for w in self.root.winfo_children():
            w.destroy()
        self.root.title(t("app.title"))
        pad = {"padx": 18}
        root = self.root

        head = ttk.Frame(root)
        head.pack(fill="x", pady=(16, 8), **pad)
        ttk.Label(head, text="vgrade", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="  " + t("app.subtitle"), style="Sub.TLabel").pack(side="left", pady=(6, 0))
        self.lang_var = tk.StringVar(value=LANG_NAMES[get_lang()])
        lang_cb = ttk.Combobox(head, textvariable=self.lang_var, values=[LANG_NAMES[x] for x in LANGS],
                               state="readonly", width=10)
        lang_cb.pack(side="right")
        lang_cb.bind("<<ComboboxSelected>>", self._on_lang)
        ttk.Label(head, text="🌐", style="Sub.TLabel").pack(side="right", padx=(0, 4))

        # 拖放區
        self.drop = tk.Canvas(root, height=110, bg=C_SURFACE, highlightthickness=0, cursor="hand2")
        self.drop.pack(fill="x", **pad)
        self.drop.bind("<Configure>", lambda e: self._draw_drop())
        self.drop.bind("<Button-1>", lambda e: self.pick_files())
        if HAS_DND:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self._on_drop)
            self.drop.dnd_bind("<<DropEnter>>", lambda e: self._draw_drop(True))
            self.drop.dnd_bind("<<DropLeave>>", lambda e: self._draw_drop())

        # 檔案清單
        lst = ttk.Frame(root)
        lst.pack(fill="x", pady=(10, 0), **pad)
        top_row = ttk.Frame(lst)
        top_row.pack(fill="x")
        self.count_lbl = ttk.Label(top_row, style="Sub.TLabel")
        self.count_lbl.pack(side="left")
        ttk.Button(top_row, text=t("app.clear"), command=self.clear_files).pack(side="right")
        ttk.Button(top_row, text=t("app.remove"), command=self.remove_selected).pack(side="right", padx=6)
        ttk.Button(top_row, text=t("app.add_folder"), command=self.pick_folder).pack(side="right")
        box = ttk.Frame(lst)
        box.pack(fill="x", pady=(6, 0))
        self.listbox = tk.Listbox(box, height=5, selectmode="extended", font=(FONT, 10),
                                  bg=C_SURFACE, fg=C_INK, relief="solid", bd=1,
                                  highlightthickness=0, activestyle="none",
                                  selectbackground=C_ACCENT, selectforeground="white")
        sb = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # 選項
        opt = ttk.Frame(root)
        opt.pack(fill="x", pady=(12, 0), **pad)
        ttk.Label(opt, text=t("app.top")).grid(row=0, column=0, sticky="w")
        self.top_var = tk.StringVar()
        self.top_cb = ttk.Combobox(opt, textvariable=self.top_var, state="readonly", width=22)
        self.top_cb.grid(row=0, column=1, sticky="w", padx=(8, 24))
        self.top_cb.bind("<<ComboboxSelected>>", self._on_top)
        ttk.Label(opt, text=t("app.target")).grid(row=0, column=2, sticky="w")
        self.target_var = tk.StringVar(value=t(f"target.{self.target}"))
        tcb = ttk.Combobox(opt, textvariable=self.target_var, values=[t(f"target.{x}") for x in TARGETS],
                           state="readonly", width=20)
        tcb.grid(row=0, column=3, sticky="w", padx=(8, 0))
        tcb.bind("<<ComboboxSelected>>",
                 lambda e: setattr(self, "target", TARGETS[[t(f"target.{x}") for x in TARGETS]
                                                           .index(self.target_var.get())]))

        # 執行
        run = ttk.Frame(root)
        run.pack(fill="x", pady=(14, 0), **pad)
        self.run_btn = ttk.Button(run, text=t("app.run"), style="Accent.TButton", command=self.start)
        self.run_btn.pack(side="left")
        self.prog = ttk.Progressbar(run, mode="determinate", length=140)
        self.prog.pack(side="left", padx=14)
        self.status = ttk.Label(run, text="", style="Sub.TLabel")
        self.status.pack(side="left")

        # 狀態列（先放在底部，視窗變矮時才不會被擠掉）
        iv = t("app.iv_ok") if vgrade.find_iverilog() else t("app.iv_missing")
        ys = t("app.ys_ok") if vgrade.yosys_command() else t("app.ys_missing")
        ttk.Label(root, text=f"{ys}　·　{iv}　·　vgrade {vgrade.__version__}",
                  style="Muted.TLabel").pack(side="bottom", fill="x", pady=(8, 10), **pad)

        # 結果卡片
        card = ttk.Frame(root, style="Card.TFrame", padding=14)
        card.pack(fill="both", expand=True, pady=(14, 0), **pad)
        left = ttk.Frame(card, style="Card.TFrame")
        left.pack(side="left", fill="y", padx=(0, 16))
        self.score_lbl = ttk.Label(left, text="—", style="Score.TLabel", width=6, anchor="center")
        self.score_lbl.pack()
        ttk.Label(left, text=t("app.of100"), style="CardMuted.TLabel").pack()
        self.grade_lbl = ttk.Label(left, text="", style="Grade.TLabel")
        self.grade_lbl.pack(pady=(6, 0))
        self.top_lbl = ttk.Label(left, text="", style="CardMuted.TLabel", wraplength=150, justify="center")
        self.top_lbl.pack(pady=(10, 0))

        right = ttk.Frame(card, style="Card.TFrame")
        right.pack(side="left", fill="both", expand=True)
        cols = ("score", "weight", "issues")
        self.tree = ttk.Treeview(right, columns=cols, height=6, selectmode="none")
        self.tree.heading("#0", text=t("app.col_cat"), anchor="w")
        self.tree.heading("score", text=t("app.col_score"))
        self.tree.heading("weight", text=t("app.col_weight"))
        self.tree.heading("issues", text=t("app.col_issues"))
        self.tree.column("#0", width=150, anchor="w")
        for c, wdt in zip(cols, (70, 60, 70)):
            self.tree.column(c, width=wdt, anchor="center")
        self.tree.pack(fill="x")
        self.summary_lbl = ttk.Label(right, text="", style="CardMuted.TLabel", wraplength=420, justify="left")
        self.summary_lbl.pack(anchor="w", pady=(8, 0))

        btns = ttk.Frame(right, style="Card.TFrame")
        btns.pack(anchor="w", pady=(10, 0))
        self.open_btn = ttk.Button(btns, text=t("app.open"), command=self.open_report, state="disabled")
        self.open_btn.pack(side="left")
        self.save_btn = ttk.Button(btns, text=t("app.save"), command=self.save_report, state="disabled")
        self.save_btn.pack(side="left", padx=6)
        ttk.Button(btns, text=t("app.folder"), command=lambda: os.startfile(reports_dir())).pack(side="left")

        self._sync_files(rescan=False)
        if self.result:
            self._show(*self.result, open_now=False)

    def _draw_drop(self, hover: bool = False) -> None:
        c = self.drop
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        c.create_rectangle(6, 6, w - 6, h - 6, outline=C_ACCENT if hover else C_LINE, width=2, dash=(6, 4))
        c.create_text(w / 2, h / 2 - 10, text=t("app.drop") if HAS_DND else t("app.drop_nodnd"),
                      font=(FONT, 12), fill=C_INK)
        c.create_text(w / 2, h / 2 + 16, text=t("app.drop_sub"), font=(FONT, 9), fill=C_MUTED)

    # ------------------------------------------------------------ language
    def _on_lang(self, _event=None) -> None:
        name = self.lang_var.get()
        lang = next(k for k, v in LANG_NAMES.items() if v == name)
        if lang == get_lang():
            return
        set_lang(lang)
        self.settings["lang"] = lang
        save_settings(self.settings)
        if self.result and not self.busy:           # 報告也換成新語言，不必重新評分
            g, top, path = self.result
            path.write_text(render_html(g, top, vgrade.__version__), encoding="utf-8")
        if not self.busy:
            self._build()

    # ------------------------------------------------------------ files
    def _on_drop(self, event) -> None:
        self._draw_drop()
        self.add_paths(self.root.tk.splitlist(event.data))

    def pick_files(self) -> None:
        if self.busy:
            return
        names = filedialog.askopenfilenames(title=t("app.pick_files"),
                                            filetypes=[("Verilog", "*.v *.sv *.vh *.svh"), (t("app.all_files"), "*.*")])
        self.add_paths(names)

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(title=t("app.pick_folder"))
        if d:
            self.add_paths([d])

    def add_paths(self, paths) -> None:
        if self.busy or not paths:
            return
        new = [f for f in collect_verilog(list(paths)) if f not in self.files]
        if not new:
            self.status.configure(text=t("app.no_verilog"))
            return
        self.files += new
        self._sync_files()

    def remove_selected(self) -> None:
        for i in reversed(self.listbox.curselection()):
            del self.files[i]
        self._sync_files()

    def clear_files(self) -> None:
        self.files.clear()
        self._sync_files()

    def _sync_files(self, rescan: bool = True) -> None:
        self.listbox.delete(0, "end")
        names = [f.name for f in self.files]
        for f in self.files:
            self.listbox.insert("end", f.name if names.count(f.name) == 1 else f"{f.name}    ({f.parent})")
        self.count_lbl.configure(text=t("app.n_files", n=len(self.files)) if self.files else t("app.no_files"))
        if rescan:
            try:
                self.modules = vgrade.list_modules(self.files)
            except Exception:
                self.modules = []
            if self.top_choice not in self.modules:
                self.top_choice = None
            self.status.configure(text="")
        self.top_cb.configure(values=[t("app.auto")] + self.modules)
        self.top_var.set(self.top_choice or t("app.auto"))

    def _on_top(self, _event=None) -> None:
        v = self.top_var.get()
        self.top_choice = None if v == t("app.auto") else v

    # ------------------------------------------------------------ grading
    def start(self) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("vgrade", t("app.need_files"))
            return
        self.busy = True
        self.run_btn.configure(state="disabled")
        self.prog.configure(mode="indeterminate")
        self.prog.start(12)
        self.status.configure(text=t("app.ready"))
        threading.Thread(target=self._worker, args=(list(self.files), self.top_choice, self.target),
                         daemon=True).start()

    def _worker(self, files: list[Path], top: str | None, target: str) -> None:
        try:
            g, top_name = vgrade.grade(files, top, target, None,
                                       progress=lambda m: self.msgs.put(("status", m)))
            self.msgs.put(("status", t("prog.report")))
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out = reports_dir() / f"{top_name}_{stamp}.html"
            out.write_text(render_html(g, top_name, vgrade.__version__), encoding="utf-8")
            self.msgs.put(("done", (g, top_name, out)))
        except vgrade.GradeError as e:
            self.msgs.put(("error", str(e)))
        except Exception:
            self.msgs.put(("error", t("app.crash") + "\n\n" + traceback.format_exc(limit=3)))

    def _poll(self) -> None:
        try:
            while True:
                kind, data = self.msgs.get_nowait()
                if kind == "status":
                    self.status.configure(text=data)
                elif kind == "done":
                    self._finish()
                    self.result = data
                    self._show(*data)
                elif kind == "error":
                    self._finish()
                    self.status.configure(text=t("app.failed"))
                    messagebox.showerror("vgrade", data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self) -> None:
        self.busy = False
        self.prog.stop()
        self.prog.configure(mode="determinate", value=0)
        self.run_btn.configure(state="normal")

    def _show(self, g, top: str, path: Path, open_now: bool = True) -> None:
        total = g.total()
        letter = g.letter()
        capped = letter != vgrade.grade_letter(total)
        self.score_lbl.configure(text=f"{total:.1f}")
        self.grade_lbl.configure(text=t("app.grade_capped" if capped else "app.grade", g=letter),
                                 foreground=GRADE_COLOR["C" if capped else letter])
        self.top_lbl.configure(text=t("app.top_is", top=top))
        counts: dict[str, int] = {}
        for i in g.issues:
            counts[i.category] = counts.get(i.category, 0) + 1
        self.tree.delete(*self.tree.get_children())
        for c, s in g.scores().items():
            self.tree.insert("", "end", text=vgrade.cat_label(c),
                             values=("N/A" if s is None else f"{s:.0f}", f"{vgrade.CATEGORIES[c]}%", counts.get(c, 0)))
        sev = {k: sum(1 for i in g.issues if i.severity == k) for k in ("error", "warning", "info")}
        text = t("app.summary", e=sev["error"], w=sev["warning"], i=sev["info"])
        st = g.stats
        if "ff_bits" in st:
            text += "\n" + t("app.summary_synth", ff=st["ff_bits"], latch=st["latch_bits"],
                             d=max(st["critical"]["levels"], 0), budget=st["budget"])
        self.summary_lbl.configure(text=text)
        self.open_btn.configure(state="normal")
        self.save_btn.configure(state="normal")
        multi = g.stats.get("top_candidates") and self.top_choice is None
        self.status.configure(text=t("app.done_multi") if multi else t("app.done"))
        if open_now:
            self.open_report()

    def open_report(self) -> None:
        if self.result and self.result[2].exists():
            os.startfile(self.result[2])

    def save_report(self) -> None:
        if not self.result:
            return
        path = self.result[2]
        dest = filedialog.asksaveasfilename(title=t("app.save_title"), defaultextension=".html",
                                            initialfile=path.name, filetypes=[(t("app.html_report"), "*.html")])
        if dest:
            Path(dest).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            self.status.configure(text=t("app.saved", name=Path(dest).name))

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    if sys.platform == "win32":
        try:                                   # 高 DPI 螢幕不模糊
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().run()


if __name__ == "__main__":
    main()
