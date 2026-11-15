"""
vgrade 桌面應用程式（Windows）

把 .v / .sv 檔（或整個 Vivado 專案資料夾）拖進視窗，按「開始評分」，
即可看到分數並開啟完整的 HTML 評分報告。

開發時執行：python vgrade_app.py
打包：python build_exe.py
"""
from __future__ import annotations

import datetime
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
from report_html import render_html       # noqa: E402

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:                          # 拖放套件不存在時仍可用按鈕選檔
    HAS_DND = False

APP_NAME = "vgrade"
VERILOG_EXT = {".v", ".sv", ".vh", ".svh"}
SKIP_DIR_PARTS = (".sim", ".gen", ".ip_user_files", ".cache", ".hw", ".runs", "__pycache__", ".git")
AUTO_TOP = "自動判斷"
TARGETS = {"FPGA（LUT6 級數）": "fpga", "ASIC（邏輯閘級數）": "asic"}

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


def reports_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    d = base / APP_NAME / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
        self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
        self.root.title("vgrade · Verilog 程式碼評分")
        self.root.geometry("720x800")
        self.root.minsize(600, 640)
        self.root.configure(bg=C_BG)
        try:
            self.root.iconbitmap(str(resource_path("assets/vgrade.ico")))
        except Exception:
            pass

        self.files: list[Path] = []
        self.report_path: Path | None = None
        self.msgs: queue.Queue = queue.Queue()
        self.busy = False

        self._styles()
        self._build()
        self._refresh_tools()
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
        st.configure("Card.TLabel", background=C_SURFACE)
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
        pad = {"padx": 18}
        root = self.root

        head = ttk.Frame(root)
        head.pack(fill="x", pady=(16, 8), **pad)
        ttk.Label(head, text="vgrade", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="  Verilog 程式碼品質評分", style="Sub.TLabel").pack(side="left", pady=(6, 0))

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
        lst.pack(fill="both", expand=False, pady=(10, 0), **pad)
        top_row = ttk.Frame(lst)
        top_row.pack(fill="x")
        self.count_lbl = ttk.Label(top_row, text="尚未加入檔案", style="Sub.TLabel")
        self.count_lbl.pack(side="left")
        ttk.Button(top_row, text="清除全部", command=self.clear_files).pack(side="right")
        ttk.Button(top_row, text="移除選取", command=self.remove_selected).pack(side="right", padx=6)
        ttk.Button(top_row, text="加入資料夾…", command=self.pick_folder).pack(side="right")
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
        ttk.Label(opt, text="Top module").grid(row=0, column=0, sticky="w")
        self.top_var = tk.StringVar(value=AUTO_TOP)
        self.top_cb = ttk.Combobox(opt, textvariable=self.top_var, values=[AUTO_TOP], state="readonly", width=22)
        self.top_cb.grid(row=0, column=1, sticky="w", padx=(8, 24))
        ttk.Label(opt, text="時序目標").grid(row=0, column=2, sticky="w")
        self.target_var = tk.StringVar(value=next(iter(TARGETS)))
        ttk.Combobox(opt, textvariable=self.target_var, values=list(TARGETS), state="readonly",
                     width=18).grid(row=0, column=3, sticky="w", padx=(8, 0))

        # 執行
        run = ttk.Frame(root)
        run.pack(fill="x", pady=(14, 0), **pad)
        self.run_btn = ttk.Button(run, text="開始評分", style="Accent.TButton", command=self.start)
        self.run_btn.pack(side="left")
        self.prog = ttk.Progressbar(run, mode="determinate", length=140)
        self.prog.pack(side="left", padx=14)
        self.status = ttk.Label(run, text="", style="Sub.TLabel")
        self.status.pack(side="left")

        # 狀態列（先放在底部，視窗變矮時才不會被擠掉）
        self.tools_lbl = ttk.Label(root, text="", style="Muted.TLabel")
        self.tools_lbl.pack(side="bottom", fill="x", pady=(8, 10), **pad)

        # 結果卡片
        card = ttk.Frame(root, style="Card.TFrame", padding=14)
        card.pack(fill="both", expand=True, pady=(14, 0), **pad)
        left = ttk.Frame(card, style="Card.TFrame")
        left.pack(side="left", fill="y", padx=(0, 16))
        self.score_lbl = ttk.Label(left, text="—", style="Score.TLabel", width=6, anchor="center")
        self.score_lbl.pack()
        ttk.Label(left, text="/ 100 分", style="CardMuted.TLabel").pack()
        self.grade_lbl = ttk.Label(left, text="", style="Grade.TLabel")
        self.grade_lbl.pack(pady=(6, 0))
        self.top_lbl = ttk.Label(left, text="", style="CardMuted.TLabel", wraplength=140, justify="center")
        self.top_lbl.pack(pady=(10, 0))

        right = ttk.Frame(card, style="Card.TFrame")
        right.pack(side="left", fill="both", expand=True)
        cols = ("score", "weight", "issues")
        self.tree = ttk.Treeview(right, columns=cols, height=6, selectmode="none")
        self.tree.heading("#0", text="類別", anchor="w")
        self.tree.heading("score", text="分數")
        self.tree.heading("weight", text="權重")
        self.tree.heading("issues", text="問題數")
        self.tree.column("#0", width=150, anchor="w")
        for c, wdt in zip(cols, (70, 60, 70)):
            self.tree.column(c, width=wdt, anchor="center")
        self.tree.pack(fill="x")
        self.summary_lbl = ttk.Label(right, text="", style="CardMuted.TLabel", wraplength=420, justify="left")
        self.summary_lbl.pack(anchor="w", pady=(8, 0))

        btns = ttk.Frame(right, style="Card.TFrame")
        btns.pack(anchor="w", pady=(10, 0))
        self.open_btn = ttk.Button(btns, text="開啟完整報告", command=self.open_report, state="disabled")
        self.open_btn.pack(side="left")
        self.save_btn = ttk.Button(btns, text="另存報告…", command=self.save_report, state="disabled")
        self.save_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="報告資料夾", command=lambda: os.startfile(reports_dir())).pack(side="left")

    def _draw_drop(self, hover: bool = False) -> None:
        c = self.drop
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        color = C_ACCENT if hover else C_LINE
        c.create_rectangle(6, 6, w - 6, h - 6, outline=color, width=2, dash=(6, 4))
        main = "把 .v / .sv 檔或 Vivado 專案資料夾拖到這裡" if HAS_DND else "點這裡選擇 .v / .sv 檔案"
        c.create_text(w / 2, h / 2 - 10, text=main, font=(FONT, 12), fill=C_INK)
        c.create_text(w / 2, h / 2 + 16, text="或點一下選擇檔案（會自動略過模擬檔與 testbench）",
                      font=(FONT, 9), fill=C_MUTED)

    def _refresh_tools(self) -> None:
        iv = "iverilog：已找到" if vgrade.find_iverilog() else "iverilog：未安裝（語法改由 Yosys 檢查）"
        ys = "Yosys：可用" if vgrade.yosys_command() else "Yosys：找不到（無法做時序與暫存器分析）"
        self.tools_lbl.configure(text=f"{ys}　·　{iv}　·　vgrade {vgrade.__version__}")

    # ------------------------------------------------------------ files
    def _on_drop(self, event) -> None:
        self._draw_drop()
        self.add_paths(self.root.tk.splitlist(event.data))

    def pick_files(self) -> None:
        if self.busy:
            return
        names = filedialog.askopenfilenames(title="選擇 Verilog 檔案",
                                            filetypes=[("Verilog", "*.v *.sv *.vh *.svh"), ("所有檔案", "*.*")])
        self.add_paths(names)

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(title="選擇專案資料夾")
        if d:
            self.add_paths([d])

    def add_paths(self, paths) -> None:
        if self.busy or not paths:
            return
        new = [f for f in collect_verilog(list(paths)) if f not in self.files]
        if not new:
            self.status.configure(text="沒有找到可評分的 Verilog 檔")
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

    def _sync_files(self) -> None:
        self.listbox.delete(0, "end")
        names = [f.name for f in self.files]
        for f in self.files:
            label = f.name if names.count(f.name) == 1 else f"{f.name}    ({f.parent})"
            self.listbox.insert("end", label)
        self.count_lbl.configure(text=f"已加入 {len(self.files)} 個檔案" if self.files else "尚未加入檔案")
        values = [AUTO_TOP]
        try:
            values += vgrade.list_modules(self.files)
        except Exception:
            pass
        self.top_cb.configure(values=values)
        if self.top_var.get() not in values:
            self.top_var.set(AUTO_TOP)
        self.status.configure(text="")

    # ------------------------------------------------------------ grading
    def start(self) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("vgrade", "請先加入要評分的 Verilog 檔案。")
            return
        self.busy = True
        self.run_btn.configure(state="disabled")
        self.prog.configure(mode="indeterminate")
        self.prog.start(12)
        self.status.configure(text="準備中…")
        top = None if self.top_var.get() == AUTO_TOP else self.top_var.get()
        target = TARGETS[self.target_var.get()]
        threading.Thread(target=self._worker, args=(list(self.files), top, target), daemon=True).start()

    def _worker(self, files: list[Path], top: str | None, target: str) -> None:
        try:
            g, top_name = vgrade.grade(files, top, target, None,
                                       progress=lambda m: self.msgs.put(("status", m)))
            self.msgs.put(("status", "產生報告…"))
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out = reports_dir() / f"{top_name}_{stamp}.html"
            out.write_text(render_html(g, top_name, vgrade.__version__), encoding="utf-8")
            self.msgs.put(("done", (g, top_name, out)))
        except vgrade.GradeError as e:
            self.msgs.put(("error", str(e)))
        except Exception:
            self.msgs.put(("error", "評分時發生錯誤：\n\n" + traceback.format_exc(limit=3)))

    def _poll(self) -> None:
        try:
            while True:
                kind, data = self.msgs.get_nowait()
                if kind == "status":
                    self.status.configure(text=data)
                elif kind == "done":
                    self._finish()
                    self._show(*data)
                elif kind == "error":
                    self._finish()
                    self.status.configure(text="評分失敗")
                    messagebox.showerror("vgrade", data)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self) -> None:
        self.busy = False
        self.prog.stop()
        self.prog.configure(mode="determinate", value=0)
        self.run_btn.configure(state="normal")

    def _show(self, g, top: str, path: Path) -> None:
        total = g.total()
        letter = g.letter()
        self.report_path = path
        self.score_lbl.configure(text=f"{total:.1f}")
        self.grade_lbl.configure(text=f"等級 {letter}", foreground=GRADE_COLOR[letter])
        self.top_lbl.configure(text=f"top：{top}")
        counts: dict[str, int] = {}
        for i in g.issues:
            counts[i.category] = counts.get(i.category, 0) + 1
        self.tree.delete(*self.tree.get_children())
        for c, s in g.scores().items():
            label, w = vgrade.CATEGORIES[c]
            self.tree.insert("", "end", text=label,
                             values=("N/A" if s is None else f"{s:.0f}", f"{w}%", counts.get(c, 0)))
        sev = {k: sum(1 for i in g.issues if i.severity == k) for k in ("error", "warning", "info")}
        extra = ""
        st = g.stats
        if "ff_bits" in st:
            extra = (f"\n暫存器 {st['ff_bits']} bits · latch {st['latch_bits']} bits · "
                     f"最長邏輯深度 {max(st['critical']['levels'], 0)} 級（建議 ≤ {st['budget']}）")
        self.summary_lbl.configure(text=f"錯誤 {sev['error']} · 警告 {sev['warning']} · 建議 {sev['info']}{extra}")
        self.open_btn.configure(state="normal")
        self.save_btn.configure(state="normal")
        cands = g.stats.get("top_candidates")
        if cands and self.top_var.get() == AUTO_TOP:
            self.status.configure(text="完成 · 有多個 top，可在選單切換")
        else:
            self.status.configure(text="完成")
        if letter != vgrade.grade_letter(total):
            self.grade_lbl.configure(text=f"等級 {letter}（有錯誤）", foreground=GRADE_COLOR["C"])
        self.open_report()

    def open_report(self) -> None:
        if self.report_path and self.report_path.exists():
            os.startfile(self.report_path)

    def save_report(self) -> None:
        if not self.report_path:
            return
        dest = filedialog.asksaveasfilename(title="另存評分報告", defaultextension=".html",
                                            initialfile=self.report_path.name,
                                            filetypes=[("HTML 報告", "*.html")])
        if dest:
            Path(dest).write_text(self.report_path.read_text(encoding="utf-8"), encoding="utf-8")
            self.status.configure(text=f"已另存：{Path(dest).name}")

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
