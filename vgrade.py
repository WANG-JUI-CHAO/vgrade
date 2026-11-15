#!/usr/bin/env python3
"""
vgrade - Verilog 程式碼品質評分工具

從六個面向評分 Verilog 設計：
  語法 / 格式風格 / 編碼規範 / 暫存器使用 / 時序 / Pipeline

需要：Python 3.10+；建議安裝 Icarus Verilog（語法檢查）與 Yosys
（`pip install yowasp-yosys`，用於合成分析）。

用法：
    python vgrade.py design.v
    python vgrade.py top.v sub.v --top top --target asic --json report.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

__version__ = "0.2.0"

# 類別代號 -> (顯示名稱, 權重)
CATEGORIES = {
    "syntax":   ("語法", 15),
    "style":    ("格式風格", 15),
    "coding":   ("編碼規範", 20),
    "register": ("暫存器使用", 15),
    "timing":   ("時序", 20),
    "pipeline": ("Pipeline", 15),
}
SEV_LABEL = {"error": "錯誤", "warning": "警告", "info": "建議"}
SEV_ORDER = {"error": 0, "warning": 1, "info": 2}

KEYWORDS = set("""
always always_comb always_ff always_latch and assign automatic begin bit buf byte case casex casez
default defparam disable else end endcase endfunction endgenerate endmodule endtask for force forever
fork function generate genvar if initial inout input int integer join localparam logic module nand
negedge nor not or output parameter posedge priority real reg repeat signed task time tri unique
unsigned while wire xnor xor
""".split())


# ======================================================================= utilities

_COMMENT_RE = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*.*?\*/', re.S)
_NUM_RE = re.compile(r"\d*\s*'\s*[sS]?[bBoOdDhH]\s*[0-9a-fA-FxXzZ_?]+|\b\d[\d_]*(?:\.\d+)?\b")
_IDENT_RE = re.compile(r"(?<![\w$`'])[A-Za-z_]\w*")


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp950", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def strip_comments(text: str) -> str:
    """移除註解並把字串清空，保留換行以維持行號。"""
    def repl(m: re.Match) -> str:
        s = m.group(0)
        if s.startswith('"'):
            return '""'
        return " " + "\n" * s.count("\n")
    return _COMMENT_RE.sub(repl, text)


def find_close(s: str, i: int, limit: int | None = None) -> int:
    pairs = {"(": ")", "[": "]", "{": "}"}
    open_c = s[i]
    close_c = pairs[open_c]
    depth = 0
    for j in range(i, len(s) if limit is None else limit):
        if s[j] == open_c:
            depth += 1
        elif s[j] == close_c:
            depth -= 1
            if depth == 0:
                return j
    raise ValueError("unbalanced")


def skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def split_top(s: str, sep: str = ",") -> list[tuple[str, int]]:
    """在括號深度 0 處切割，回傳 (片段, 起始偏移)。"""
    out, depth, start = [], 0, 0
    for i, c in enumerate(s):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == sep and depth == 0:
            out.append((s[start:i], start))
            start = i + 1
    out.append((s[start:], start))
    return [(t, o) for t, o in out if t.strip()]


def idents(expr: str) -> set[str]:
    expr = _NUM_RE.sub(" ", expr)
    return {w for w in _IDENT_RE.findall(expr) if w not in KEYWORDS}


def disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - disp_width(s))


# ======================================================================= data model

@dataclass
class Issue:
    category: str
    severity: str
    message: str
    suggestion: str = ""
    file: str = ""
    line: int = 0
    penalty: float = 0.0


@dataclass
class Source:
    path: Path
    display: str
    raw: str
    code: str = ""

    def __post_init__(self):
        self.code = strip_comments(self.raw)

    def line(self, pos: int) -> int:
        return self.code.count("\n", 0, pos) + 1


@dataclass
class ModuleInfo:
    name: str
    src: Source
    start: int
    body_start: int
    end: int
    port_text: str
    param_text: str
    ansi: bool
    is_testbench: bool = False
    params: set[str] = field(default_factory=set)
    signals: dict[str, int] = field(default_factory=dict)   # 名稱 -> 宣告位置
    functions: set[str] = field(default_factory=set)


@dataclass
class AlwaysBlock:
    kind: str            # "seq" | "comb" | "other"
    sens: str | None
    start: int           # 'always' 關鍵字位置
    body_start: int
    body_end: int
    edges: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Assignment:
    lhs: set[str]
    op: str
    reads: set[str]
    pos: int


class Grader:
    def __init__(self):
        self.issues: list[Issue] = []
        self.penalty: dict[str, float] = defaultdict(float)
        self._rule_used: dict[tuple[str, str], float] = defaultdict(float)
        self.na: dict[str, str] = {}
        self.stats: dict = {}
        self.sources: list[tuple[str, str]] = []    # (顯示名稱, 原始碼)

    def add(self, cat: str, sev: str, msg: str, sugg: str = "", file: str = "", line: int = 0,
            penalty: float = 0.0, rule: str | None = None, cap: float | None = None) -> None:
        key = (cat, rule or msg)
        if cap is not None:
            penalty = max(0.0, min(penalty, cap - self._rule_used[key]))
        self._rule_used[key] += penalty
        self.penalty[cat] += penalty
        self.issues.append(Issue(cat, sev, msg, sugg, file, line, round(penalty, 1)))

    def scores(self) -> dict[str, float | None]:
        return {c: None if c in self.na else max(0.0, 100.0 - self.penalty[c]) for c in CATEGORIES}

    def weighted(self) -> float:
        """各類別分數的加權平均。"""
        num = den = 0.0
        for c, s in self.scores().items():
            if s is not None:
                w = CATEGORIES[c][1]
                num += s * w
                den += w
        return num / den if den else 0.0

    def severity_penalty(self) -> float:
        """嚴重度額外扣分：每個錯誤 -4、每個警告 -1（上限 30），避免問題被類別權重稀釋。"""
        n_err = sum(1 for i in self.issues if i.severity == "error")
        n_warn = sum(1 for i in self.issues if i.severity == "warning")
        return float(min(SEVERITY_CAP, n_err * SEVERITY_PENALTY["error"] + n_warn * SEVERITY_PENALTY["warning"]))

    def total(self) -> float:
        return max(0.0, self.weighted() - self.severity_penalty())

    def letter(self) -> str:
        """有「錯誤」等級的問題時，等級最高為 C（有真正 bug 的設計不該拿到 A/B）。"""
        return grade_letter(self.total(), any(i.severity == "error" for i in self.issues))


SEVERITY_PENALTY = {"error": 4, "warning": 1}
SEVERITY_CAP = 30
ERROR_GRADE_CAP = "C"


def grade_letter(score: float, has_error: bool = False) -> str:
    order = "ABCDF"
    for th, g in ((90, "A"), (80, "B"), (70, "C"), (60, "D")):
        if score >= th:
            if has_error and order.index(g) < order.index(ERROR_GRADE_CAP):
                return ERROR_GRADE_CAP
            return g
    return "F"


# ======================================================================= source parsing

_MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_]\w*)")
_ENDMODULE_RE = re.compile(r"\bendmodule\b")
_TAIL_RE = re.compile(r"([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$")


def parse_modules(src: Source) -> list[ModuleInfo]:
    code = src.code
    mods = []
    pos = 0
    while (m := _MODULE_RE.search(code, pos)):
        end_m = _ENDMODULE_RE.search(code, m.end())
        end = end_m.start() if end_m else len(code)
        i = skip_ws(code, m.end())
        param_text = port_text = ""
        try:
            if code.startswith("#", i):
                i = skip_ws(code, i + 1)
                j = find_close(code, i)
                param_text = code[i + 1:j]
                i = skip_ws(code, j + 1)
            if code.startswith("(", i):
                j = find_close(code, i)
                port_text = code[i + 1:j]
                i = j + 1
        except ValueError:
            pass
        semi = code.find(";", i)
        body_start = semi + 1 if semi != -1 else i
        ansi = bool(re.search(r"\b(input|output|inout)\b", port_text))
        mod = ModuleInfo(m.group(1), src, m.start(), body_start, end, port_text, param_text, ansi)
        body = code[body_start:end]
        lname = mod.name.lower()
        mod.is_testbench = (lname.startswith("tb") or lname.endswith("_tb") or lname.startswith("test")
                            or (not port_text.strip() and re.search(r"\binitial\b", body) is not None))
        _collect_decls(mod)
        mods.append(mod)
        pos = end_m.end() if end_m else len(code)
    return mods


def _collect_decls(mod: ModuleInfo) -> None:
    code = mod.src.code
    # parameters
    for text in [mod.param_text] + [m.group(1) for m in re.finditer(
            r"\b(?:parameter|localparam)\b([^;]*);", code[mod.body_start:mod.end])]:
        for item, _ in split_top(text):
            if "=" in item:
                lhs = re.sub(r"\[[^\]]*\]", " ", item.split("=", 1)[0])
                words = [w for w in _IDENT_RE.findall(lhs) if w not in KEYWORDS]
                if words:
                    mod.params.add(words[-1])
    # header ports (ANSI or bare names)
    hdr_off = code.find(mod.port_text, mod.start) if mod.port_text else -1
    for item, off in split_top(mod.port_text):
        m = _TAIL_RE.search(item.split("=", 1)[0].rstrip())
        if m:
            mod.signals.setdefault(m.group(1), hdr_off + off + m.start(1))
    # body declarations
    decl_re = re.compile(r"\b(?:wire|reg|logic|input|output|inout|integer)\b"
                         r"(?:\s+(?:signed|unsigned|wire|reg|logic)\b)*\s*(?:\[[^\]]*\]\s*)*([^;]*);")
    for m in decl_re.finditer(code, mod.body_start, mod.end):
        for item, off in split_top(m.group(1)):
            mm = re.match(r"\s*([A-Za-z_]\w*)", item)
            if mm and mm.group(1) not in KEYWORDS:
                mod.signals.setdefault(mm.group(1), m.start(1) + off + mm.start(1))
    for m in re.finditer(r"\b(?:function|task)\s+(?:automatic\s+)?(?:\w+\s+)?(?:\[[^\]]*\]\s*)?(\w+)",
                         code[mod.body_start:mod.end]):
        mod.functions.add(m.group(1))


_ALWAYS_RE = re.compile(r"\balways(_ff|_comb|_latch)?\b")
_BE_RE = re.compile(r"\b(begin|end)\b")
_NEXT_ITEM_RE = re.compile(r"\b(?:always\w*|assign|initial|endmodule|generate|endgenerate|function|task"
                           r"|wire|reg|logic|localparam|parameter)\b")


def _block_end(code: str, i: int, limit: int) -> int:
    if re.match(r"begin\b", code[i:i + 6]):
        depth = 0
        for m in _BE_RE.finditer(code, i, limit):
            depth += 1 if m.group(1) == "begin" else -1
            if depth == 0:
                return m.end()
        return limit
    m = _NEXT_ITEM_RE.search(code, i, limit)
    return m.start() if m else limit


def extract_always(mod: ModuleInfo) -> list[AlwaysBlock]:
    code = mod.src.code
    blocks = []
    for m in _ALWAYS_RE.finditer(code, mod.body_start, mod.end):
        i = skip_ws(code, m.end())
        sens = None
        if code.startswith("@", i):
            i = skip_ws(code, i + 1)
            if code.startswith("(", i):
                try:
                    j = find_close(code, i, mod.end)
                except ValueError:
                    continue
                sens = code[i + 1:j].strip()
                i = j + 1
            elif code.startswith("*", i):
                sens = "*"
                i += 1
            else:
                mm = re.match(r"[\w.]+", code[i:])
                if mm:
                    sens = mm.group(0)
                    i += mm.end()
        i = skip_ws(code, i)
        end = _block_end(code, i, mod.end)
        edges = re.findall(r"\b(posedge|negedge)\s+([\w.]+)", sens or "")
        suffix = m.group(1) or ""
        if suffix == "_ff" or edges:
            kind = "seq"
        elif suffix in ("_comb", "_latch") or sens is not None:
            kind = "comb"
        else:
            kind = "other"
        blocks.append(AlwaysBlock(kind, sens, m.start(), i, end, edges))
    return blocks


_LEAD_RE = re.compile(r"\s*(?:(?:begin|fork)\b(?:\s*:\s*\w+)?|(?:endcase|end|join|else|unique|priority)\b"
                      r"(?:\s*:\s*\w+)?|default\b\s*:?|#\s*[\d.]+\w*|@\s*\*)")
_CTRL_RE = re.compile(r"\s*(if|case|casez|casex|for|while|repeat|wait|@)\s*\(")
_LABEL_RE = re.compile(r"\s*(?:[\w']+\s*,\s*)*[\w']+\s*:(?!=)")
_ASSIGN_RE = re.compile(r"\s*((?:[A-Za-z_][\w.]*|\{[^}]*\})(?:\s*\[[^\]]*\])*)\s*(<=|=)(?!=)\s*(.*)", re.S)


def parse_statements(code: str, start: int, end: int) -> tuple[list[Assignment], set[str]]:
    """回傳區塊內的指定敘述與條件式讀取的訊號。"""
    assigns: list[Assignment] = []
    cond_reads: set[str] = set()
    for stmt, off in split_top(code[start:end], ";"):
        s = stmt
        while True:
            m = _LEAD_RE.match(s)
            if m and s[:m.end()].strip():
                s = s[m.end():]
                continue
            m = _CTRL_RE.match(s)
            if m:
                p = m.end() - 1
                try:
                    q = find_close(s, p)
                except ValueError:
                    break
                if m.group(1) != "for":
                    cond_reads |= idents(s[p + 1:q])
                s = s[q + 1:]
                continue
            m = _LABEL_RE.match(s)
            if m and not re.match(r"\s*(?:begin|end)\b", s):
                s = s[m.end():]
                continue
            break
        m = _ASSIGN_RE.match(s)
        if not m:
            continue
        lhs_text, op, rhs = m.groups()
        base = re.sub(r"\[[^\]]*\]", " ", lhs_text)
        idx_reads = set()
        for r in re.findall(r"\[([^\]]*)\]", lhs_text):
            idx_reads |= idents(r)
        pos = start + off + (len(stmt) - len(s)) + (m.start(1))
        assigns.append(Assignment(idents(base), op, idents(rhs) | idx_reads, pos))
    return assigns, cond_reads


# ======================================================================= source checks

def check_style(g: Grader, src: Source, mods: list[ModuleInfo], max_line: int) -> None:
    f = src.display
    lines = src.raw.splitlines()
    code_lines = src.code.splitlines()
    tab_lines = space_lines = trailing = 0
    indents: list[int] = []
    for i, ln in enumerate(lines, 1):
        lead = ln[:len(ln) - len(ln.lstrip(" \t"))]
        if ln.strip():
            if "\t" in lead:
                tab_lines += 1
            elif lead:
                space_lines += 1
                indents.append(len(lead))
        if ln != ln.rstrip():
            trailing += 1
        if disp_width(ln.expandtabs(4)) > max_line:
            g.add("style", "info", f"單行超過 {max_line} 字元", "拆成多行，維持可讀性",
                  f, i, 1, rule="long_line", cap=6)
    if tab_lines and space_lines:
        g.add("style", "warning", f"混用 tab（{tab_lines} 行）與空白（{space_lines} 行）縮排",
              "統一使用空白縮排（建議 4 格），避免不同編輯器顯示錯亂", f, 0, 5)
    if indents:
        odd = sum(1 for n in indents if n % 2)
        if odd / len(indents) > 0.2:
            g.add("style", "info", f"縮排不一致：{odd} 行縮排不是偶數格",
                  "固定使用 2 或 4 格縮排", f, 0, 3)
    if trailing:
        g.add("style", "info", f"{trailing} 行有行尾多餘空白", "設定編輯器自動移除行尾空白",
              f, 0, min(3, 1 + trailing / 20))

    # 一行多個敘述
    for i, ln in enumerate(code_lines, 1):
        if re.search(r"\bfor\s*\(", ln):
            continue
        if ln.count(";") >= 2:
            g.add("style", "info", "同一行有多個敘述", "每行只寫一個敘述，方便除錯與 diff",
                  f, i, 1, rule="multi_stmt", cap=4)

    # 註解比例（只計算 module 內，排除 Vivado 樣板標頭）
    if mods:
        first = src.line(mods[0].start)
        body_raw = lines[first - 1:]
        body_code = code_lines[first - 1:]
        total = sum(1 for ln in body_raw if ln.strip())
        commented = sum(1 for r, c in zip(body_raw, body_code)
                        if r.strip() and len(r.strip()) - len(c.strip()) >= 3)
        ratio = commented / total if total else 1
        g.stats.setdefault("comment_ratio", {})[f] = round(ratio, 2)
        if total >= 15 and ratio < 0.05:
            g.add("style", "warning", f"註解比例過低（{ratio:.0%}）",
                  "為 FSM 狀態、資料路徑與關鍵時序加上說明", f, 0, 8)
        elif total >= 15 and ratio < 0.10:
            g.add("style", "info", f"註解偏少（{ratio:.0%}）", "關鍵邏輯可多加說明", f, 0, 3)

    for mod in mods:
        if mod.is_testbench:
            continue
        if mod.port_text.strip() and not mod.ansi:
            g.add("style", "info", f"module {mod.name} 使用 Verilog-1995 舊式 port 宣告",
                  "改用 ANSI 風格：module m (input wire clk, output reg [7:0] q);",
                  f, src.line(mod.start), 2, rule="non_ansi")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", mod.name):
            g.add("style", "info", f"module 名稱 '{mod.name}' 不是 snake_case",
                  "module 與訊號建議使用小寫加底線，例如 gcd_core", f, src.line(mod.start), 2,
                  rule="naming", cap=12)
        for name, pos in mod.signals.items():
            if name in mod.params:
                continue
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                g.add("style", "info", f"訊號名稱 '{name}' 不是 snake_case",
                      "訊號建議小寫加底線（例如 reg_a、data_valid）", f, src.line(pos), 2,
                      rule="naming", cap=12)
        for p in sorted(mod.params):
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", p):
                g.add("style", "info", f"參數名稱 '{p}' 建議全大寫",
                      "parameter / localparam 慣例使用大寫，例如 DATA_WIDTH、S_IDLE",
                      f, src.line(mod.start), 1, rule="param_naming", cap=4)


def check_coding(g: Grader, mod: ModuleInfo) -> set[str]:
    """回傳在多個區塊被驅動的訊號名稱（供 Yosys 訊息去重）。"""
    src = mod.src
    code = src.code
    f = src.display
    blocks = extract_always(mod)
    drivers: dict[str, list[tuple[int, int]]] = defaultdict(list)   # name -> [(block_id, pos)]
    clocks: set[str] = set()

    for bid, blk in enumerate(blocks):
        if blk.kind == "other":
            continue
        assigns, cond_reads = parse_statements(code, blk.body_start, blk.body_end)
        body = code[blk.body_start:blk.body_end]
        lhs_all = set()
        for a in assigns:
            lhs_all |= a.lhs
            for n in a.lhs:
                drivers[n].append((bid, a.pos))
            if blk.kind == "seq" and a.op == "=":
                g.add("coding", "warning", f"循序邏輯 always 區塊使用 blocking 指定：{', '.join(sorted(a.lhs))} =",
                      "posedge/negedge 的 always 區塊請使用 non-blocking（<=），避免模擬順序造成的 race",
                      f, src.line(a.pos), 8, rule="blocking_seq", cap=32)
            if blk.kind == "comb" and a.op == "<=":
                g.add("coding", "warning", f"組合邏輯 always 區塊使用 non-blocking 指定：{', '.join(sorted(a.lhs))} <=",
                      "always @(*) 請使用 blocking（=），否則模擬結果可能與合成不一致",
                      f, src.line(a.pos), 8, rule="nonblocking_comb", cap=32)

        line = src.line(blk.start)
        if blk.kind == "comb" and blk.sens and blk.sens.replace(" ", "") != "*":
            sens_names = idents(re.sub(r"\bor\b", ",", blk.sens))
            reads = set(cond_reads)
            for a in assigns:
                reads |= a.reads
            reads -= mod.params | mod.functions | lhs_all
            missing = sorted(r for r in reads if r in mod.signals and r not in sens_names)
            if missing:
                g.add("coding", "error", f"敏感列表不完整，缺少：{', '.join(missing)}",
                      "改用 always @(*)；不完整的敏感列表會造成模擬與合成結果不同",
                      f, line, 15, rule="sens_incomplete", cap=45)
            else:
                g.add("coding", "info", "組合邏輯使用手寫敏感列表",
                      "建議改用 always @(*)，之後新增訊號時不會漏掉", f, line, 2, rule="sens_manual", cap=4)

        for cm in re.finditer(r"\b(case[zx]?)\s*\(", body):
            if cm.group(1) == "casex":
                g.add("coding", "warning", "使用 casex", "casex 會把 X 當成 don't care，可能遮蔽模擬錯誤；改用 casez 或 case",
                      f, src.line(blk.body_start + cm.start()), 3, rule="casex", cap=6)
            endm = re.search(r"\bendcase\b", body[cm.end():])
            seg = body[cm.end():cm.end() + endm.start()] if endm else body[cm.end():]
            if not re.search(r"\bdefault\b", seg):
                if blk.kind == "comb":
                    g.add("coding", "warning", "組合邏輯的 case 沒有 default",
                          "加上 default 分支（或在 case 前給預設值），避免推論出 latch",
                          f, src.line(blk.body_start + cm.start()), 5, rule="case_default_comb", cap=15)
                else:
                    g.add("coding", "info", "case 沒有 default",
                          "FSM 建議加 default 讓非法狀態回到 IDLE，提高可靠度",
                          f, src.line(blk.body_start + cm.start()), 1, rule="case_default_seq", cap=3)

        if blk.kind == "seq":
            else_ifs = len(re.findall(r"\belse\s+if\b", body))
            if else_ifs >= 6:
                g.add("timing", "info", f"長串 if-else-if（{else_ifs} 層）形成優先權鏈",
                      "優先權鏈會加深邏輯；若條件互斥，改用 case 或平行判斷", f, line, 4,
                      rule="if_chain", cap=8)
            if len(blk.edges) == 1:
                clocks.add(blk.edges[0][1])
            else:
                for edge, name in blk.edges:
                    if name not in cond_reads:
                        clocks.add(name)
            for edge, name in blk.edges:
                if name in clocks:
                    if edge == "negedge":
                        g.add("coding", "info", f"使用負緣時脈 negedge {name}",
                              "同一設計混用正負緣會讓時序分析變困難，除非必要請統一用 posedge",
                              f, line, 2, rule="negedge_clk", cap=4)
                elif edge == "negedge" and not re.search(r"(_n|n|_b|_l)$", name.lower()):
                    g.add("style", "info", f"低態有效的 reset '{name}' 名稱沒有 _n 結尾",
                          "低態有效訊號慣例加 _n，例如 rst_n", f, line, 1, rule="rst_naming", cap=2)

    # 連續指定 assign
    assign_lhs: dict[str, int] = {}
    for m in re.finditer(r"\bassign\b\s+(.*?);", code[mod.body_start:mod.end], re.S):
        text = m.group(1)
        if "=" not in text:
            continue
        lhs = re.sub(r"\[[^\]]*\]", " ", text.split("=", 1)[0])
        pos = mod.body_start + m.start()
        for n in idents(lhs):
            drivers[n].append((-1 - len(assign_lhs), pos))
            assign_lhs[n] = pos

    # gated / derived clock
    for c in clocks:
        if c in assign_lhs:
            g.add("coding", "warning", f"時脈 '{c}' 由組合邏輯產生（gated/derived clock）",
                  "FPGA 請改用 clock enable；ASIC 請使用標準 clock gating cell",
                  f, src.line(assign_lhs[c]), 15, rule="gated_clock", cap=30)

    # 多重驅動
    multi = set()
    for name, lst in drivers.items():
        ids = {b for b, _ in lst}
        if len(ids) >= 2:
            multi.add(name)
            g.add("coding", "error", f"訊號 '{name}' 在 {len(ids)} 個不同區塊被指定（多重驅動）",
                  "一個訊號只能在一個 always 或 assign 中被驅動，請合併邏輯",
                  f, src.line(min(p for _, p in lst)), 25, rule="multi_driver", cap=50)

    body = code[mod.body_start:mod.end]
    for m in re.finditer(r"\binitial\b", body):
        g.add("coding", "info", "RTL 中使用 initial",
              "initial 在 ASIC 不可合成（FPGA 可用於初值），重置值請用 reset 給定",
              f, src.line(mod.body_start + m.start()), 3, rule="initial", cap=6)
    for m in re.finditer(r"#\s*\d+(?:\.\d+)?", body):
        g.add("coding", "warning", "RTL 中使用 # 延遲", "延遲在合成時會被忽略，造成模擬與硬體行為不同，請移除",
              f, src.line(mod.body_start + m.start()), 3, rule="delay", cap=9)
    return multi


# ======================================================================= external tools

# Windows GUI（打包後沒有主控台）呼叫外部程式時不要跳出黑色視窗
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def find_iverilog() -> str | None:
    p = shutil.which("iverilog")
    if p:
        return p
    for cand in (r"C:\iverilog\bin\iverilog.exe", r"C:\Program Files\iverilog\bin\iverilog.exe"):
        if Path(cand).is_file():
            return cand
    return None


def run_iverilog(g: Grader, work: Path, files: list[str], name_map: dict[str, str]) -> bool | None:
    iv = find_iverilog()
    if not iv:
        return None
    r = subprocess.run([iv, "-g2012", "-Wall", "-t", "null", *files], cwd=work,
                       capture_output=True, text=True, errors="replace", **_NO_WINDOW)
    ok = r.returncode == 0
    seen = set()
    for ln in (r.stdout + r.stderr).splitlines():
        m = re.match(r"(src\d+\.v):(\d+):\s*(.*)", ln.strip())
        if not m or ln in seen:
            continue
        seen.add(ln)
        fname, lineno, msg = name_map.get(m.group(1), m.group(1)), int(m.group(2)), m.group(3)
        low = msg.lower()
        if "error" in low or "syntax" in low:
            ok = False
            g.add("syntax", "error", f"編譯錯誤：{msg}", "修正語法錯誤後才能進行合成分析",
                  fname, lineno, 50, rule="iv_error", cap=100)
        elif "warning" in low:
            if "timescale" in low:
                g.add("syntax", "info", f"timescale 不一致：{msg}", "所有檔案統一加上 `timescale 1ns/1ps",
                      fname, lineno, 2, rule="iv_timescale", cap=2)
            else:
                g.add("syntax", "warning", f"編譯警告：{msg}", "檢查位寬、隱含宣告的 net 或 port 連接",
                      fname, lineno, 5, rule="iv_warning", cap=30)
    return ok


def yosys_command() -> list[str] | None:
    if getattr(sys, "frozen", False):            # 打包後的應用程式：由自己執行內建的 Yosys
        return [sys.executable, "--run-yosys"]
    for name in ("yosys", "yowasp-yosys"):
        p = shutil.which(name)
        if p:
            return [p]
    try:
        import importlib.util
        if importlib.util.find_spec("yowasp_yosys"):
            return [sys.executable, "-c",
                    "import sys, yowasp_yosys; sys.exit(yowasp_yosys.run_yosys(sys.argv[1:]))"]
    except Exception:
        pass
    return None


def run_yosys(work: Path, files: list[str], top: str, target: str) -> tuple[bool, str]:
    cmd = yosys_command()
    assert cmd
    synth = f"synth -flatten -top {top}" + (" -lut 6" if target == "fpga" else "")
    script = "\n".join([
        f"read_verilog -sv -DSYNTHESIS {' '.join(files)}",
        f"hierarchy -check -top {top}",
        "proc",
        "flatten",
        "write_json rtl.json",
        "check",
        synth,
        "opt_clean -purge",
        "tee -o stat.txt stat",
        "write_json gate.json",
    ])
    (work / "script.ys").write_text(script + "\n", encoding="utf-8")
    try:
        r = subprocess.run(cmd + ["-q", "-l", "yosys.log", "-s", "script.ys"], cwd=work,
                           capture_output=True, text=True, errors="replace", timeout=900, **_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "Yosys 執行逾時"
    log = (work / "yosys.log").read_text(encoding="utf-8", errors="replace") \
        if (work / "yosys.log").exists() else r.stdout + r.stderr
    return r.returncode == 0 and (work / "gate.json").exists(), log


# ======================================================================= netlist analysis

SEQ_RE = re.compile(r"^\$_(DFFSRE|DFFSR|DFFE|DFF|SDFFCE|SDFFE|SDFF|ALDFFE|ALDFF|DLATCHSR|DLATCH|SR)_([PN01]*)_$")
RTL_FF_TYPES = {"$dff", "$adff", "$sdff", "$dffe", "$adffe", "$sdffe", "$sdffce", "$aldff", "$aldffe",
                "$dffsr", "$dffsre", "$ff"}
FREE_CELLS = {"$_NOT_", "$_BUF_"}
NEG = -1
INF = 10 ** 9


def has_reset(ctype: str) -> bool:
    m = SEQ_RE.match(ctype)
    if not m:
        return False
    base, pol = m.groups()
    if base in ("SDFF", "SDFFE", "SDFFCE", "DFFSR", "DFFSRE", "ALDFF", "ALDFFE", "DLATCHSR", "SR"):
        return True
    return len(pol) == {"DFF": 3, "DFFE": 4, "DLATCH": 3}.get(base, 99)


def _bit_names(mod: dict) -> dict[int, str]:
    names: dict[int, tuple[str, bool]] = {}
    for n, info in mod.get("netnames", {}).items():
        hidden = bool(info.get("hide_name")) or n.startswith("$")
        bits = info["bits"]
        off = info.get("offset", 0)
        for i, b in enumerate(bits):
            if not isinstance(b, int):
                continue
            label = n if len(bits) == 1 else f"{n}[{off + i}]"
            if b not in names or (names[b][1] and not hidden):
                names[b] = (label, hidden)
    return {b: v[0] for b, v in names.items()}


def _base(name: str) -> str:
    return re.sub(r"\[\d+\]$", "", name)


_CONTROL_WORDS = ("valid", "vld", "state", "cnt", "count", "en", "flag", "busy", "done", "ready",
                  "rdy", "req", "ack", "stall", "start", "stop", "ptr", "full", "empty")


def _is_control(name: str) -> bool:
    toks = re.split(r"[_.\d]+", name.split(".")[-1].lower())
    return any(t in _CONTROL_WORDS or t.startswith(("valid", "state", "cnt", "count")) for t in toks)


def analyze_netlist(mod: dict) -> dict:
    ports = mod.get("ports", {})
    cells = mod.get("cells", {})
    bname = _bit_names(mod)

    pi_bits: set[int] = set()
    po_bits: dict[int, str] = {}
    for pn, p in ports.items():
        for b in p["bits"]:
            if isinstance(b, int):
                if p["direction"] in ("input", "inout"):
                    pi_bits.add(b)
                if p["direction"] in ("output", "inout"):
                    po_bits.setdefault(b, pn)

    driver: dict[int, tuple[list[int], int]] = {}
    seq_cells = []          # (name, type, inputs_by_port, outputs)
    comb_count = 0
    comb_list = []          # (ins, outs)
    for cn, c in cells.items():
        t = c["type"]
        conns = c.get("connections", {})
        dirs = c.get("port_directions") or {p: ("output" if p in ("Y", "Q") else "input") for p in conns}
        ins_by_port = {p: [b for b in bs if isinstance(b, int)] for p, bs in conns.items()
                       if dirs.get(p) == "input"}
        outs = [b for p, bs in conns.items() if dirs.get(p) == "output" for b in bs if isinstance(b, int)]
        if SEQ_RE.match(t) or not (t.startswith("$_") or t == "$lut"):
            seq_cells.append((cn, t, ins_by_port, outs))
            continue
        ins = [b for bs in ins_by_port.values() for b in bs]
        w = 0 if t in FREE_CELLS else 1
        comb_count += w
        for b in outs:
            driver[b] = (ins, w)
        comb_list.append((ins, outs))

    seq_out = {b for _, _, _, outs in seq_cells for b in outs}
    memo: dict[int, tuple[int, int]] = {}
    loops = [0]

    def depth(b0: int) -> tuple[int, int]:
        if b0 in memo:
            return memo[b0]
        stack = [(b0, False)]
        onstack: set[int] = set()
        while stack:
            b, done = stack.pop()
            if b in memo:
                continue
            if b in seq_out:
                memo[b] = (NEG, 0)
                continue
            if b not in driver:
                memo[b] = (0, NEG) if b in pi_bits else (NEG, NEG)
                continue
            ins, w = driver[b]
            if not done:
                if b in onstack:
                    continue
                stack.append((b, True))
                onstack.add(b)
                for i in ins:
                    if i in onstack:
                        loops[0] += 1
                    elif i not in memo:
                        stack.append((i, False))
            else:
                onstack.discard(b)
                vals = [memo.get(i, (NEG, NEG)) for i in ins]
                di = max((v[0] for v in vals), default=NEG)
                dr = max((v[1] for v in vals), default=NEG)
                memo[b] = (di + w if di >= 0 else NEG, dr + w if dr >= 0 else NEG)
        return memo[b0]

    # 0-1 最短路徑：從輸入經過幾個暫存器
    dist: dict[int, int] = {b: 0 for b in pi_bits}
    for _ in range(len(cells) + 2):
        changed = False
        for ins, outs in comb_list:
            v = min((dist.get(i, INF) for i in ins), default=INF)
            for o in outs:
                if v < dist.get(o, INF):
                    dist[o] = v
                    changed = True
        for _, t, ibp, outs in seq_cells:
            d_bits = [b for p, bs in ibp.items() if p in ("D", "A", "AD") for b in bs] \
                or [b for p, bs in ibp.items() if p not in ("C", "CLK") for b in bs]
            v = min((dist.get(i, INF) for i in d_bits), default=INF)
            v = v + 1 if v < INF and SEQ_RE.match(t) else v
            for o in outs:
                if v < dist.get(o, INF):
                    dist[o] = v
                    changed = True
        if not changed:
            break

    res = {
        "ff_bits": 0, "latch_bits": 0, "ff_noreset": defaultdict(int), "ff_names": defaultdict(int),
        "comb_cells": comb_count, "in2reg": NEG, "reg2reg": NEG, "reg2out": NEG, "in2out": NEG,
        "critical": (NEG, "", ""), "stages": defaultdict(lambda: [0, 0]), "latency": {},
        "comb_out_ports": {}, "logic_out_ports": {}, "loops": 0, "latches": [],
    }

    def upd_crit(d: int, kind: str, name: str):
        if d > res["critical"][0]:
            res["critical"] = (d, kind, name)

    for cn, t, ibp, outs in seq_cells:
        m = SEQ_RE.match(t)
        qname = _base(bname.get(outs[0], cn)) if outs else cn
        if m:
            if m.group(1).startswith("DLATCH") or m.group(1) == "SR":
                res["latch_bits"] += len(outs)
                res["latches"].append(qname)
            else:
                res["ff_bits"] += len(outs)
                res["ff_names"][qname] += len(outs)
                if not has_reset(t):
                    res["ff_noreset"][qname] += len(outs)
        worst = NEG
        for p, bs in ibp.items():
            if p in ("C", "CLK"):
                continue
            for b in bs:
                di, dr = depth(b)
                if di > res["in2reg"]:
                    res["in2reg"] = di
                if dr > res["reg2reg"]:
                    res["reg2reg"] = dr
                worst = max(worst, di, dr)
                upd_crit(di, "input -> reg", qname)
                upd_crit(dr, "reg -> reg", qname)
        if m and outs:
            st = dist.get(outs[0], INF)
            key = st if st < INF else -1
            res["stages"][key][0] = max(res["stages"][key][0], worst)
            res["stages"][key][1] += len(outs)

    for b, pn in po_bits.items():
        di, dr = depth(b)
        res["reg2out"] = max(res["reg2out"], dr)
        res["in2out"] = max(res["in2out"], di)
        upd_crit(di, "input -> output", pn)
        upd_crit(dr, "reg -> output", pn)
        if di >= 0:
            res["comb_out_ports"][pn] = max(res["comb_out_ports"].get(pn, NEG), di)
        if dr > 0:
            res["logic_out_ports"][pn] = max(res["logic_out_ports"].get(pn, NEG), dr)
        d = dist.get(b, INF)
        if d < INF:
            res["latency"][pn] = min(res["latency"].get(pn, INF), d)
    res["loops"] = loops[0]
    return res


def rtl_registers(mod: dict) -> dict[str, int]:
    bname = _bit_names(mod)
    regs: dict[str, int] = defaultdict(int)
    for cn, c in mod.get("cells", {}).items():
        if c["type"] in RTL_FF_TYPES:
            q = [b for b in c["connections"].get("Q", []) if isinstance(b, int)]
            if q:
                regs[_base(bname.get(q[0], cn))] += len(q)
    return regs


def expensive_ops(mod: dict) -> list[tuple[str, int, str, bool]]:
    """回傳 (運算類型, 位寬, src, B 是否為 2 的冪次常數)。"""
    out = []
    for c in mod.get("cells", {}).values():
        t = c["type"]
        if t not in ("$mul", "$div", "$mod", "$divfloor", "$modfloor", "$pow"):
            continue
        conns = c.get("connections", {})
        a_const = all(not isinstance(b, int) for b in conns.get("A", []))
        b_bits = conns.get("B", [])
        b_const = all(not isinstance(b, int) for b in b_bits)
        if a_const and t == "$mul":
            continue
        pow2 = False
        if b_const and b_bits:
            val = int("".join("1" if b == "1" else "0" for b in reversed(b_bits)), 2)
            pow2 = val > 0 and (val & (val - 1)) == 0
            if t == "$mul" or pow2:
                continue
        width = int(str(c.get("parameters", {}).get("Y_WIDTH", "0")), 2) if c.get("parameters") else 0
        out.append((t[1:], width, c.get("attributes", {}).get("src", ""), b_const))
    return out


def map_src(src: str, name_map: dict[str, str]) -> tuple[str, int]:
    m = re.search(r"(src\d+\.v):(\d+)", src or "")
    if not m:
        return "", 0
    return name_map.get(m.group(1), m.group(1)), int(m.group(2))


def check_synthesis(g: Grader, work: Path, top: str, log: str, name_map: dict[str, str],
                    multi_src: set[str], budget: int, target: str,
                    sig_loc: dict[str, tuple[str, int]] | None = None) -> None:
    sig_loc = sig_loc or {}

    def loc(name: str) -> tuple[str, int]:
        return sig_loc.get(name.split(".")[-1], ("", 0))

    rtl = json.loads((work / "rtl.json").read_text(encoding="utf-8"))["modules"].get(top, {})
    gate = json.loads((work / "gate.json").read_text(encoding="utf-8"))["modules"].get(top, {})
    a = analyze_netlist(gate)
    unit = "LUT6" if target == "fpga" else "邏輯閘"

    # ---------------- Yosys 訊息
    latch_srcs: dict[str, tuple[str, int]] = {}
    for m in re.finditer(r"Latch inferred for signal `\\?[^.]*\.\\?([^']+)' from process `[^']*?(src\d+\.v):(\d+)", log):
        latch_srcs.setdefault(m.group(1), (name_map.get(m.group(2), m.group(2)), int(m.group(3))))
    seen = set()
    for ln in log.splitlines():
        if not ln.startswith("Warning:") or ln in seen:
            continue
        seen.add(ln)
        msg = ln[len("Warning:"):].strip()
        fname, lineno = map_src(msg, name_map)
        if ("conflicting drivers" in msg or "multiple drivers" in msg.lower()
                or "Driver-driver conflict" in msg):
            if multi_src:      # 原始碼檢查已回報，避免重複扣分
                continue
            g.add("coding", "error", f"多重驅動：{msg}", "一個訊號只能由一處驅動", fname, lineno, 25,
                  rule="multi_driver", cap=50)
        elif "has no driver" in msg or "is used but has no driver" in msg:
            g.add("coding", "warning", f"訊號沒有被驅動：{msg}", "確認該訊號有被指定值，或移除不用的訊號",
                  fname, lineno, 5, rule="undriven", cap=15)
        elif "logic loop" in msg:
            g.add("coding", "error", f"組合邏輯迴圈：{msg}", "組合邏輯不可回授自己，請在迴路中加入暫存器",
                  fname, lineno, 20, rule="comb_loop", cap=40)
        elif "Resizing cell port" in msg:
            g.add("coding", "warning", f"例化 port 位寬不符：{msg}", "檢查子模組 port 的位寬與連接訊號一致",
                  fname, lineno, 3, rule="port_resize", cap=9)
        elif "implicitly declared" in msg or "Replacing memory" in msg or "Latch inferred" in msg:
            continue
        else:
            g.add("coding", "info", f"Yosys：{msg}", "", fname, lineno, 1, rule="yosys_other", cap=5)
    if a["loops"]:
        g.add("coding", "error", f"合成後偵測到 {a['loops']} 處組合邏輯迴圈", "組合邏輯不可形成迴路",
              penalty=20, rule="comb_loop", cap=40)

    # ---------------- 暫存器
    latch_names = sorted(set(a["latches"]) | set(latch_srcs))
    for n in latch_names:
        fname, lineno = latch_srcs.get(n) or loc(n)
        g.add("register", "error", f"推論出 latch：{n}",
              "組合邏輯中所有分支都要指定該訊號（或在 always @(*) 開頭給預設值），case 要有 default",
              fname, lineno, 30, rule="latch", cap=90)

    rtl_regs = rtl_registers(rtl)
    removed = []
    for name, bits in sorted(rtl_regs.items()):
        if name in latch_names:
            continue
        kept = a["ff_names"].get(name, 0)
        if kept < bits:
            removed.append((name, bits - kept, bits))
    for name, lost, bits in removed:
        what = "整個" if lost == bits else f"{lost}/{bits} bits "
        hint = "（可能是上面的多重驅動造成）" if multi_src else ""
        g.add("register", "warning", f"暫存器 '{name}' {what}在合成時被移除（沒被使用、常數或重複）{hint}",
              "移除多餘的暫存器，或確認它是否該連到輸出；寬度可以縮小就縮小",
              *loc(name), penalty=5, rule="reg_removed", cap=25)

    nores = dict(a["ff_noreset"])
    if a["ff_bits"]:
        if nores and len(nores) < len(a["ff_names"]):
            for n, b in sorted(nores.items()):
                if _is_control(n):
                    g.add("register", "warning", f"控制暫存器 '{n}'（{b} bits）沒有 reset",
                          "state、valid、計數器等控制訊號一定要 reset，否則上電後行為不確定",
                          *loc(n), penalty=5, rule="no_reset_ctrl", cap=15)
        elif nores and len(nores) == len(a["ff_names"]):
            g.add("register", "info", "所有暫存器都沒有 reset",
                  "至少讓 FSM 狀態與 valid 訊號有 reset，確保上電後狀態確定", penalty=5)

    # ---------------- 時序
    crit_d, crit_kind, crit_name = a["critical"]
    if crit_d > budget:
        p = min(70.0, 60.0 * (crit_d - budget) / budget)
        g.add("timing", "warning",
              f"最長邏輯深度 {crit_d} 級 {unit}（{crit_kind}，終點 {crit_name}），超過建議值 {budget} 級",
              "把長組合路徑拆開：插入 pipeline 暫存器、提前計算（precompute）或改用平行結構",
              penalty=p)
    for op, width, srcattr, b_const in expensive_ops(rtl):
        fname, lineno = map_src(srcattr, name_map)
        if op in ("div", "mod", "divfloor", "modfloor", "pow"):
            g.add("timing", "warning", f"硬體 {op} 運算（{width} bits）",
                  "除法/取餘數的組合電路非常深又大；改用多週期迭代、查表，或讓除數為 2 的冪次（改用位移）",
                  fname, lineno, 15, rule="div", cap=30)
        else:
            g.add("timing", "info", f"乘法器（{width} bits）",
                  "寬乘法器建議使用 DSP 或在後面加一級暫存器", fname, lineno, 3, rule="mul", cap=9)

    # ---------------- Pipeline
    if a["ff_bits"] == 0:
        g.na["pipeline"] = "純組合電路，沒有暫存器"
        if crit_d > budget:
            g.add("timing", "info", "純組合電路深度過深，適合切 pipeline",
                  "在中間插入暫存器分成多級，以提高最高時脈")
    else:
        for pn, d in sorted(a["comb_out_ports"].items()):
            p = 20 if d > budget / 2 else 10
            g.add("pipeline", "warning", f"輸出 '{pn}' 有從輸入直達的組合路徑（深度 {d}）",
                  "輸出前加暫存器，避免與下一級電路串成過長路徑", penalty=p, rule="in2out", cap=30)
        unreg = sorted(pn for pn in a["logic_out_ports"] if pn not in a["comb_out_ports"])
        if unreg:
            g.add("pipeline", "info", f"輸出經過組合邏輯才送出（未直接由暫存器輸出）：{', '.join(unreg)}",
                  "把輸出改成暫存器輸出（registered output），時序較好估算", penalty=5)
        stages = {k: v for k, v in a["stages"].items() if k >= 0 and v[0] > 0}
        if len(stages) >= 2:
            depths = [v[0] for v in stages.values()]
            mx, avg = max(depths), sum(depths) / len(depths)
            ratio = mx / avg if avg else 1
            if ratio > 1.5 and mx > 2:
                worst = max(stages, key=lambda k: stages[k][0])
                g.add("pipeline", "warning",
                      f"各級邏輯深度不平衡：第 {worst} 級 {mx} 級 {unit}，平均 {avg:.1f}（比例 {ratio:.1f}）",
                      "把最深那一級的部分運算移到較淺的相鄰級，讓每級深度接近",
                      penalty=min(30.0, (ratio - 1.5) * 20))
        max_stage = max((v[0] for v in a["stages"].values()), default=0)
        if max_stage > budget:
            g.add("pipeline", "warning", f"有一級的邏輯深度 {max_stage} 超過建議值 {budget}",
                  "在這一級中間再切一級 pipeline", penalty=15)

    lat = a["latency"]
    g.stats.update({
        "target": target,
        "unit": unit,
        "ff_bits": a["ff_bits"],
        "ff_noreset_bits": sum(nores.values()),
        "latch_bits": a["latch_bits"],
        "comb_cells": a["comb_cells"],
        "depth": {k: a[k] for k in ("in2reg", "reg2reg", "reg2out", "in2out")},
        "critical": {"levels": crit_d, "kind": crit_kind, "endpoint": crit_name},
        "budget": budget,
        "stages": {str(k): {"depth": v[0], "ff_bits": v[1]} for k, v in sorted(a["stages"].items())},
        "latency": {k: v for k, v in sorted(lat.items())},
    })


# ======================================================================= main flow

def choose_top(mods: list[ModuleInfo]) -> list[ModuleInfo]:
    names = {m.name for m in mods}
    used = set()
    for m in mods:
        body = m.src.code[m.body_start:m.end]
        for n in names - {m.name}:
            if re.search(rf"\b{re.escape(n)}\s*(?:#\s*\(|[A-Za-z_]\w*\s*\()", body):
                used.add(n)
    return [m for m in mods if m.name not in used]


class GradeError(Exception):
    """評分無法進行（找不到 module 等），訊息可直接顯示給使用者。"""


def list_modules(paths: list[Path]) -> list[str]:
    """回傳檔案中可評分的 module 名稱（top 候選排前面），供 GUI 選單使用。"""
    mods = []
    for p in paths:
        s = Source(p, p.name, read_text(p))
        mods += [m for m in parse_modules(s) if not m.is_testbench]
    tops = {m.name for m in choose_top(mods)}
    return sorted({m.name for m in mods}, key=lambda n: (n not in tops, n))


def grade(paths: list[Path], top: str | None, target: str, budget: int | None,
          max_line: int = 100, progress=None) -> tuple[Grader, str]:
    def step(msg: str) -> None:
        if progress:
            progress(msg)

    step("解析原始碼…")
    g = Grader()
    budget = budget or (8 if target == "fpga" else 24)
    sources = []
    seen_names: dict[str, int] = defaultdict(int)
    for p in paths:
        seen_names[p.name] += 1
    for p in paths:
        disp = p.name if seen_names[p.name] == 1 else str(p)
        sources.append(Source(p, disp, read_text(p)))

    all_mods: list[ModuleInfo] = []
    for s in sources:
        all_mods += parse_modules(s)
    design = [m for m in all_mods if not m.is_testbench]
    for m in all_mods:
        if m.is_testbench:
            g.add("style", "info", f"略過 testbench module '{m.name}'", "評分只針對設計檔", m.src.display,
                  m.src.line(m.start), 0)
    if not design:
        raise GradeError("找不到可評分的 module（testbench 會被略過）")

    if top:
        if top not in {m.name for m in design}:
            raise GradeError(f"找不到 module '{top}'（有：{', '.join(m.name for m in design)}）")
    else:
        tops = choose_top(design)
        top = max(tops or design, key=lambda m: m.end - m.start).name
        if len(tops) > 1:
            g.stats["top_candidates"] = [m.name for m in tops]
            g.add("style", "info", f"偵測到多個 top module（{', '.join(m.name for m in tops)}），"
                  f"本次評分 {top}", "要評其他 module 請用 --top 指定（應用程式中可在選單切換）")
    g.stats["top"] = top
    g.stats["files"] = [s.display for s in sources]
    g.sources = [(s.display, s.raw) for s in sources]

    for s in sources:
        check_style(g, s, [m for m in all_mods if m.src is s], max_line)
    multi_src: set[str] = set()
    for m in design:
        multi_src |= check_coding(g, m)

    with tempfile.TemporaryDirectory(prefix="vgrade_") as tmp:
        work = Path(tmp)
        files, name_map = [], {}
        for i, s in enumerate(sources):
            fn = f"src{i}.v"
            (work / fn).write_text(s.raw, encoding="utf-8")
            files.append(fn)
            name_map[fn] = s.display

        step("語法檢查（iverilog）…")
        iv_ok = run_iverilog(g, work, files, name_map)
        if iv_ok is None:
            g.add("syntax", "info", "找不到 iverilog，語法僅以 Yosys 檢查", "安裝 Icarus Verilog 可得到更完整的語法檢查")

        synth_cats = ("register", "timing", "pipeline")
        if iv_ok is False:
            for c in synth_cats:
                g.na[c] = "語法錯誤，無法合成"
        elif not yosys_command():
            for c in synth_cats:
                g.na[c] = "未安裝 Yosys（pip install yowasp-yosys）"
            if iv_ok is None:
                g.na["syntax"] = "沒有可用的編譯器"
        else:
            step("合成與時序分析（Yosys）…")
            ok, log = run_yosys(work, files, top, target)
            if not ok:
                errs = [ln for ln in log.splitlines() if "ERROR" in ln][:3]
                msg = "；".join(errs) or "Yosys 合成失敗"
                fname, lineno = map_src(msg, name_map)
                g.add("syntax", "error", f"合成失敗：{msg}", "修正錯誤，或確認使用的語法 Yosys 有支援",
                      fname, lineno, 30, rule="yosys_error", cap=60)
                for c in synth_cats:
                    g.na[c] = "合成失敗"
            else:
                sig_loc = {n: (m.src.display, m.src.line(p)) for m in design for n, p in m.signals.items()}
                check_synthesis(g, work, top, log, name_map, multi_src, budget, target, sig_loc)
    return g, top


# ======================================================================= report

def print_report(g: Grader, top: str, show_info: bool = True) -> None:
    out = sys.stdout
    scores = g.scores()
    total = g.total()
    bar = "=" * 66
    print(bar, file=out)
    print(f" vgrade {__version__} 評分報告   top: {top}   檔案: {', '.join(g.stats.get('files', []))}", file=out)
    print(bar, file=out)
    note = f"（有錯誤時等級最高 {ERROR_GRADE_CAP}）" if g.letter() != grade_letter(total) else ""
    print(f" 總分 {total:5.1f} / 100   等級 {g.letter()}{note}", file=out)
    sp = g.severity_penalty()
    if sp:
        print(f"      = 類別加權 {g.weighted():.1f} − 嚴重度扣分 {sp:g}（錯誤 ×4、警告 ×1）", file=out)
    print("", file=out)
    print(f" {pad('類別', 14)}{pad('分數', 8)}{pad('權重', 6)}", file=out)
    for c, (label, w) in CATEGORIES.items():
        s = scores[c]
        if s is None:
            print(f" {pad(label, 14)}{pad('N/A', 8)}{pad(f'{w}%', 6)}({g.na[c]})", file=out)
        else:
            print(f" {pad(label, 14)}{pad(f'{s:.0f}', 8)}{pad(f'{w}%', 6)}{'#' * round(s / 5)}", file=out)

    st = g.stats
    if "ff_bits" in st:
        print("-" * 66, file=out)
        print(f" 設計統計（目標：{'FPGA LUT6' if st['target'] == 'fpga' else 'ASIC 通用邏輯閘'}）", file=out)
        print(f"   暫存器        : {st['ff_bits']} bits（無 reset {st['ff_noreset_bits']} bits）", file=out)
        print(f"   Latch         : {st['latch_bits']} bits", file=out)
        print(f"   組合邏輯      : {st['comb_cells']} 個 {st['unit']}", file=out)
        cr = st["critical"]
        if cr["levels"] >= 0:
            print(f"   最長邏輯深度  : {cr['levels']} 級（{cr['kind']}，終點 {cr['endpoint']}；建議 <= {st['budget']}）",
                  file=out)
        d = st["depth"]
        fmt = lambda v: "-" if v < 0 else str(v)   # noqa: E731
        print(f"   各類路徑深度  : in->reg {fmt(d['in2reg'])} | reg->reg {fmt(d['reg2reg'])} | "
              f"reg->out {fmt(d['reg2out'])} | in->out {fmt(d['in2out'])}", file=out)
        stages = [(int(k), v) for k, v in st["stages"].items() if int(k) >= 0]
        if stages:
            print("   各級深度      : " + " | ".join(f"S{k}: {v['depth']} 級/{v['ff_bits']}b"
                                                    for k, v in sorted(stages)), file=out)
        if st["latency"]:
            print("   輸出延遲      : " + ", ".join(f"{k}={v} cycle" for k, v in st["latency"].items()), file=out)

    issues = sorted(g.issues, key=lambda i: (SEV_ORDER[i.severity], -i.penalty, i.file, i.line))
    if not show_info:
        issues = [i for i in issues if i.severity != "info"]
    print("-" * 66, file=out)
    print(f" 問題清單（{len(issues)} 項）", file=out)
    if not issues:
        print("   沒有發現問題，做得好！", file=out)
    for i in issues:
        loc = f"{i.file}:{i.line}" if i.line else (i.file or "-")
        pen = f" (-{i.penalty:g})" if i.penalty else ""
        print(f" [{SEV_LABEL[i.severity]}][{CATEGORIES[i.category][0]}] {loc}  {i.message}{pen}", file=out)
        if i.suggestion:
            print(f"        -> {i.suggestion}", file=out)
    print(bar, file=out)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="vgrade", description="Verilog 程式碼品質評分工具")
    ap.add_argument("sources", nargs="+", type=Path, help="Verilog 設計檔（可多個）")
    ap.add_argument("-t", "--top", help="top module（預設自動判斷）")
    ap.add_argument("--target", choices=("fpga", "asic"), default="fpga",
                    help="時序分析目標：fpga = LUT6 級數（預設），asic = 通用邏輯閘級數")
    ap.add_argument("--max-levels", type=int, help="邏輯深度建議上限（預設 fpga 8 / asic 24）")
    ap.add_argument("--max-line", type=int, default=100, help="單行長度上限（預設 100）")
    ap.add_argument("--json", type=Path, metavar="FILE", help="另外輸出 JSON 報告")
    ap.add_argument("--html", type=Path, metavar="FILE", help="另外輸出 HTML 評分報告")
    ap.add_argument("--open", action="store_true", help="產生 HTML 報告後用瀏覽器開啟")
    ap.add_argument("--no-info", action="store_true", help="不顯示「建議」等級的項目")
    ap.add_argument("--version", action="version", version=f"vgrade {__version__}")
    args = ap.parse_args(argv)

    for p in args.sources:
        if not p.is_file():
            ap.error(f"找不到檔案：{p}")
    try:
        g, top = grade(args.sources, args.top, args.target, args.max_levels, args.max_line)
    except GradeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print_report(g, top, show_info=not args.no_info)
    if args.json:
        data = {
            "version": __version__, "top": top, "total": round(g.total(), 1),
            "weighted": round(g.weighted(), 1), "severity_penalty": g.severity_penalty(),
            "grade": g.letter(),
            "scores": {c: (None if s is None else round(s, 1)) for c, s in g.scores().items()},
            "not_applicable": g.na, "stats": g.stats,
            "issues": [asdict(i) for i in g.issues],
        }
        args.json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.html:
        from report_html import render_html
        args.html.write_text(render_html(g, top, __version__), encoding="utf-8")
        print(f"HTML 報告：{args.html.resolve()}")
        if args.open:
            import webbrowser
            webbrowser.open(args.html.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
