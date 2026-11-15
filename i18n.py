"""
vgrade 多語系字串（繁體中文 / English）

用法：
    from i18n import t, set_lang
    set_lang("en")
    t("cat.syntax")                    -> "Syntax"
    t("long_line", max=100)            -> "Line longer than 100 characters"

參數值若是以 "@" 開頭的字串，會被當成另一個翻譯鍵（例如 unit="@unit.fpga"）。
"""
from __future__ import annotations

import locale
import os

LANGS = ("zh", "en")
LANG_NAMES = {"zh": "繁體中文", "en": "English"}
_lang = "zh"


def detect_lang() -> str:
    env = (os.environ.get("VGRADE_LANG") or "").lower()
    if env[:2] in LANGS:
        return env[:2]
    try:
        loc = (locale.getlocale()[0] or "").lower()
    except Exception:
        loc = ""
    if not loc and os.name == "nt":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "zh" if (lcid & 0x3FF) == 0x04 else "en"      # LANG_CHINESE = 0x04
        except Exception:
            pass
    return "zh" if ("zh" in loc or "chinese" in loc) else "en"


def set_lang(lang: str) -> None:
    global _lang
    _lang = lang if lang in LANGS else "zh"


def get_lang() -> str:
    return _lang


def has(key: str) -> bool:
    return key in S


def t(key: str, lang: str | None = None, **kw) -> str:
    lang = lang or _lang
    entry = S.get(key)
    if entry is None:
        return key
    text = entry[lang] if lang in entry else entry["zh"]     # 空字串是刻意的，不要退回中文
    if not kw:
        return text
    vals = {k: (t(v[1:], lang) if isinstance(v, str) and v.startswith("@") else v) for k, v in kw.items()}
    try:
        return text.format(**vals)
    except (KeyError, IndexError, ValueError):
        return text


def _e(zh: str, en: str) -> dict:
    return {"zh": zh, "en": en}


S: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------ 類別 / 嚴重度 / 單位
    "cat.syntax": _e("語法", "Syntax"),
    "cat.style": _e("格式風格", "Style"),
    "cat.coding": _e("編碼規範", "Coding"),
    "cat.register": _e("暫存器使用", "Registers"),
    "cat.timing": _e("時序", "Timing"),
    "cat.pipeline": _e("Pipeline", "Pipeline"),
    "sev.error": _e("錯誤", "Error"),
    "sev.warning": _e("警告", "Warning"),
    "sev.info": _e("建議", "Info"),
    "unit.fpga": _e("LUT6", "LUT6"),
    "unit.asic": _e("邏輯閘", "gates"),
    "target.fpga": _e("FPGA（LUT6 級數）", "FPGA (LUT6 levels)"),
    "target.asic": _e("ASIC（邏輯閘級數）", "ASIC (gate levels)"),

    # ------------------------------------------------------------ 語法
    "iv_error": _e("編譯錯誤：{msg}", "Compile error: {msg}"),
    "iv_error.fix": _e("修正語法錯誤後才能進行合成分析", "Fix the syntax error before synthesis analysis can run"),
    "iv_timescale": _e("timescale 不一致：{msg}", "Inconsistent timescale: {msg}"),
    "iv_timescale.fix": _e("所有檔案統一加上 `timescale 1ns/1ps", "Add `timescale 1ns/1ps to every file"),
    "iv_warning": _e("編譯警告：{msg}", "Compile warning: {msg}"),
    "iv_warning.fix": _e("檢查位寬、隱含宣告的 net 或 port 連接",
                         "Check bit widths, implicitly declared nets and port connections"),
    "no_iverilog": _e("找不到 iverilog，語法僅以 Yosys 檢查", "iverilog not found; syntax is checked by Yosys only"),
    "no_iverilog.fix": _e("安裝 Icarus Verilog 可得到更完整的語法檢查",
                          "Install Icarus Verilog for more thorough syntax checking"),
    "yosys_error": _e("合成失敗：{msg}", "Synthesis failed: {msg}"),
    "yosys_error.fix": _e("修正錯誤，或確認使用的語法 Yosys 有支援",
                          "Fix the error, or check that Yosys supports the syntax used"),

    # ------------------------------------------------------------ 格式風格
    "long_line": _e("單行超過 {max} 字元", "Line longer than {max} characters"),
    "long_line.fix": _e("拆成多行，維持可讀性", "Split it across several lines for readability"),
    "mixed_indent": _e("混用 tab（{tabs} 行）與空白（{spaces} 行）縮排",
                       "Mixed tab ({tabs} lines) and space ({spaces} lines) indentation"),
    "mixed_indent.fix": _e("統一使用空白縮排（建議 4 格），避免不同編輯器顯示錯亂",
                           "Indent with spaces only (4 recommended) so every editor shows the same layout"),
    "odd_indent": _e("縮排不一致：{n} 行縮排不是偶數格", "Inconsistent indentation: {n} lines use an odd indent width"),
    "odd_indent.fix": _e("固定使用 2 或 4 格縮排", "Use a fixed 2- or 4-space indent"),
    "trailing_ws": _e("{n} 行有行尾多餘空白", "{n} lines have trailing whitespace"),
    "trailing_ws.fix": _e("設定編輯器自動移除行尾空白", "Let your editor strip trailing whitespace on save"),
    "multi_stmt": _e("同一行有多個敘述", "Multiple statements on one line"),
    "multi_stmt.fix": _e("每行只寫一個敘述，方便除錯與 diff", "One statement per line makes debugging and diffs easier"),
    "comment_low": _e("註解比例過低（{ratio}）", "Very few comments ({ratio})"),
    "comment_low.fix": _e("為 FSM 狀態、資料路徑與關鍵時序加上說明",
                          "Document FSM states, the datapath and timing-critical logic"),
    "comment_few": _e("註解偏少（{ratio}）", "Few comments ({ratio})"),
    "comment_few.fix": _e("關鍵邏輯可多加說明", "Add comments to the key logic"),
    "non_ansi": _e("module {name} 使用 Verilog-1995 舊式 port 宣告",
                   "module {name} uses Verilog-1995 (non-ANSI) port declarations"),
    "non_ansi.fix": _e("改用 ANSI 風格：module m (input wire clk, output reg [7:0] q);",
                       "Use ANSI style: module m (input wire clk, output reg [7:0] q);"),
    "module_naming": _e("module 名稱 '{name}' 不是 snake_case", "Module name '{name}' is not snake_case"),
    "module_naming.fix": _e("module 與訊號建議使用小寫加底線，例如 gcd_core",
                            "Use lower case with underscores for modules and signals, e.g. gcd_core"),
    "signal_naming": _e("訊號名稱 '{name}' 不是 snake_case", "Signal name '{name}' is not snake_case"),
    "signal_naming.fix": _e("訊號建議小寫加底線（例如 reg_a、data_valid）",
                            "Use lower case with underscores (e.g. reg_a, data_valid)"),
    "param_naming": _e("參數名稱 '{name}' 建議全大寫", "Parameter name '{name}' should be UPPER_CASE"),
    "param_naming.fix": _e("parameter / localparam 慣例使用大寫，例如 DATA_WIDTH、S_IDLE",
                           "parameter / localparam names are conventionally upper case, e.g. DATA_WIDTH, S_IDLE"),
    "rst_naming": _e("低態有效的 reset '{name}' 名稱沒有 _n 結尾", "Active-low reset '{name}' has no _n suffix"),
    "rst_naming.fix": _e("低態有效訊號慣例加 _n，例如 rst_n", "Active-low signals conventionally end in _n, e.g. rst_n"),
    "tb_skipped": _e("略過 testbench module '{name}'", "Skipped testbench module '{name}'"),
    "tb_skipped.fix": _e("評分只針對設計檔", "Only design modules are graded"),
    "multi_top": _e("偵測到多個 top module（{names}），本次評分 {top}",
                    "Several top modules found ({names}); graded {top}"),
    "multi_top.fix": _e("要評其他 module 請用 --top 指定（應用程式中可在選單切換）",
                        "Use --top to grade another one (or pick it from the menu in the app)"),

    # ------------------------------------------------------------ 編碼規範
    "blocking_seq": _e("循序邏輯 always 區塊使用 blocking 指定：{lhs} =",
                       "Blocking assignment in a sequential always block: {lhs} ="),
    "blocking_seq.fix": _e("posedge/negedge 的 always 區塊請使用 non-blocking（<=），避免模擬順序造成的 race",
                           "Use non-blocking (<=) in clocked always blocks to avoid simulation races"),
    "nonblocking_comb": _e("組合邏輯 always 區塊使用 non-blocking 指定：{lhs} <=",
                           "Non-blocking assignment in a combinational always block: {lhs} <="),
    "nonblocking_comb.fix": _e("always @(*) 請使用 blocking（=），否則模擬結果可能與合成不一致",
                               "Use blocking (=) in always @(*), otherwise simulation may not match synthesis"),
    "sens_incomplete": _e("敏感列表不完整，缺少：{names}", "Incomplete sensitivity list, missing: {names}"),
    "sens_incomplete.fix": _e("改用 always @(*)；不完整的敏感列表會造成模擬與合成結果不同",
                              "Use always @(*); an incomplete list makes simulation differ from synthesis"),
    "sens_manual": _e("組合邏輯使用手寫敏感列表", "Hand-written sensitivity list in combinational logic"),
    "sens_manual.fix": _e("建議改用 always @(*)，之後新增訊號時不會漏掉",
                          "Prefer always @(*) so new signals are never left out"),
    "casex": _e("使用 casex", "casex is used"),
    "casex.fix": _e("casex 會把 X 當成 don't care，可能遮蔽模擬錯誤；改用 casez 或 case",
                    "casex treats X as don't-care and can hide simulation bugs; use casez or case"),
    "case_default_comb": _e("組合邏輯的 case 沒有 default", "case without default in combinational logic"),
    "case_default_comb.fix": _e("加上 default 分支（或在 case 前給預設值），避免推論出 latch",
                                "Add a default branch (or assign defaults before the case) to avoid a latch"),
    "case_default_seq": _e("case 沒有 default", "case without default"),
    "case_default_seq.fix": _e("FSM 建議加 default 讓非法狀態回到 IDLE，提高可靠度",
                               "Give FSMs a default that returns illegal states to IDLE"),
    "negedge_clk": _e("使用負緣時脈 negedge {name}", "Falling-edge clock: negedge {name}"),
    "negedge_clk.fix": _e("同一設計混用正負緣會讓時序分析變困難，除非必要請統一用 posedge",
                          "Mixing clock edges complicates timing analysis; use posedge unless required"),
    "gated_clock": _e("時脈 '{name}' 由組合邏輯產生（gated/derived clock）",
                      "Clock '{name}' is generated by logic (gated/derived clock)"),
    "gated_clock.fix": _e("FPGA 請改用 clock enable；ASIC 請使用標準 clock gating cell",
                          "On FPGA use a clock enable; on ASIC use a standard clock-gating cell"),
    "multi_driver": _e("訊號 '{name}' 在 {n} 個不同區塊被指定（多重驅動）",
                       "Signal '{name}' is assigned in {n} different blocks (multiple drivers)"),
    "multi_driver.fix": _e("一個訊號只能在一個 always 或 assign 中被驅動，請合併邏輯",
                           "Drive each signal from exactly one always block or assign; merge the logic"),
    "initial": _e("RTL 中使用 initial", "initial block in RTL"),
    "initial.fix": _e("initial 在 ASIC 不可合成（FPGA 可用於初值），重置值請用 reset 給定",
                      "initial is not synthesizable for ASIC (FPGA uses it for init values); use a reset instead"),
    "delay": _e("RTL 中使用 # 延遲", "# delay in RTL"),
    "delay.fix": _e("延遲在合成時會被忽略，造成模擬與硬體行為不同，請移除",
                    "Delays are ignored by synthesis, so simulation differs from hardware; remove them"),
    "yosys_multi": _e("多重驅動：{msg}", "Multiple drivers: {msg}"),
    "yosys_multi.fix": _e("一個訊號只能由一處驅動", "A signal may only have one driver"),
    "undriven": _e("訊號沒有被驅動：{msg}", "Undriven signal: {msg}"),
    "undriven.fix": _e("確認該訊號有被指定值，或移除不用的訊號", "Make sure it is assigned, or remove the unused signal"),
    "comb_loop": _e("組合邏輯迴圈：{msg}", "Combinational loop: {msg}"),
    "comb_loop.fix": _e("組合邏輯不可回授自己，請在迴路中加入暫存器",
                        "Combinational logic must not feed back on itself; add a register in the loop"),
    "comb_loop_count": _e("合成後偵測到 {n} 處組合邏輯迴圈", "{n} combinational loop(s) found after synthesis"),
    "comb_loop_count.fix": _e("組合邏輯不可形成迴路", "Combinational logic must not form loops"),
    "port_resize": _e("例化 port 位寬不符：{msg}", "Port width mismatch in instance: {msg}"),
    "port_resize.fix": _e("檢查子模組 port 的位寬與連接訊號一致",
                          "Make the connected signal match the submodule port width"),
    "yosys_other": _e("Yosys：{msg}", "Yosys: {msg}"),

    # ------------------------------------------------------------ 暫存器
    "latch": _e("推論出 latch：{name}", "Latch inferred: {name}"),
    "latch.fix": _e("組合邏輯中所有分支都要指定該訊號（或在 always @(*) 開頭給預設值），case 要有 default",
                    "Assign the signal on every branch (or give it a default at the top of always @(*)); "
                    "give every case a default"),
    "reg_removed_all": _e("暫存器 '{name}' 整個在合成時被移除（沒被使用、常數或重複）",
                          "Register '{name}' was removed entirely by synthesis (unused, constant or duplicate)"),
    "reg_removed_part": _e("暫存器 '{name}' 有 {lost}/{bits} bits 在合成時被移除（沒被使用、常數或重複）",
                           "{lost} of {bits} bits of register '{name}' were removed by synthesis "
                           "(unused, constant or duplicate)"),
    "reg_removed_all.fix": _e("移除多餘的暫存器，或確認它是否該連到輸出；寬度可以縮小就縮小",
                              "Remove the redundant register, or check whether it should drive an output; "
                              "narrow it if possible"),
    "reg_removed_part.fix": _e("移除多餘的暫存器，或確認它是否該連到輸出；寬度可以縮小就縮小",
                               "Remove the redundant bits, or check whether they should drive an output; "
                               "narrow the register if possible"),
    "hint.multi_driver": _e("（可能是上面的多重驅動造成）", " (possibly caused by the multiple drivers above)"),
    "no_reset_ctrl": _e("控制暫存器 '{name}'（{bits} bits）沒有 reset", "Control register '{name}' ({bits} bits) has no reset"),
    "no_reset_ctrl.fix": _e("state、valid、計數器等控制訊號一定要 reset，否則上電後行為不確定",
                            "Always reset control signals (state, valid, counters) so power-up behavior is defined"),
    "no_reset_all": _e("所有暫存器都沒有 reset", "No register has a reset"),
    "no_reset_all.fix": _e("至少讓 FSM 狀態與 valid 訊號有 reset，確保上電後狀態確定",
                           "At least reset FSM state and valid signals so the power-up state is defined"),

    # ------------------------------------------------------------ 時序
    "depth_over": _e("最長邏輯深度 {d} 級 {unit}（{kind}，終點 {end}），超過建議值 {budget} 級",
                     "Longest logic depth is {d} {unit} levels ({kind}, ends at {end}), above the budget of {budget}"),
    "depth_over.fix": _e("把長組合路徑拆開：插入 pipeline 暫存器、提前計算（precompute）或改用平行結構",
                         "Break the long path: insert pipeline registers, precompute, or use a parallel structure"),
    "div": _e("硬體 {op} 運算（{width} bits）", "Hardware {op} operator ({width} bits)"),
    "div.fix": _e("除法/取餘數的組合電路非常深又大；改用多週期迭代、查表，或讓除數為 2 的冪次（改用位移）",
                  "Combinational divide/modulo is deep and large; use a multi-cycle divider, a lookup table, "
                  "or a power-of-two divisor (shift)"),
    "mul": _e("乘法器（{width} bits）", "Multiplier ({width} bits)"),
    "mul.fix": _e("寬乘法器建議使用 DSP 或在後面加一級暫存器", "Map wide multipliers to DSPs or register their output"),
    "if_chain": _e("長串 if-else-if（{n} 層）形成優先權鏈", "Long if-else-if chain ({n} levels) forms a priority chain"),
    "if_chain.fix": _e("優先權鏈會加深邏輯；若條件互斥，改用 case 或平行判斷",
                       "Priority chains deepen logic; use case or parallel checks if the conditions are exclusive"),
    "comb_too_deep": _e("純組合電路深度過深，適合切 pipeline", "Purely combinational design is deep; consider pipelining"),
    "comb_too_deep.fix": _e("在中間插入暫存器分成多級，以提高最高時脈",
                            "Insert registers to split it into stages and raise the maximum clock"),

    # ------------------------------------------------------------ Pipeline
    "in2out": _e("輸出 '{name}' 有從輸入直達的組合路徑（深度 {d}）",
                 "Output '{name}' has a combinational path straight from the inputs (depth {d})"),
    "in2out.fix": _e("輸出前加暫存器，避免與下一級電路串成過長路徑",
                     "Register the output so it cannot chain into a long path with the next block"),
    "unreg_out": _e("輸出經過組合邏輯才送出（未直接由暫存器輸出）：{names}",
                    "Outputs pass through logic after the last register (not registered): {names}"),
    "unreg_out.fix": _e("把輸出改成暫存器輸出（registered output），時序較好估算",
                        "Use registered outputs; their timing is easier to predict"),
    "imbalance": _e("各級邏輯深度不平衡：第 {stage} 級 {d} 級 {unit}，平均 {avg}（比例 {ratio}）",
                    "Unbalanced pipeline: stage {stage} has {d} {unit} levels vs an average of {avg} (ratio {ratio})"),
    "imbalance.fix": _e("把最深那一級的部分運算移到較淺的相鄰級，讓每級深度接近",
                        "Move part of the deepest stage's logic into a shallower neighbor to even out the stages"),
    "stage_over": _e("有一級的邏輯深度 {d} 超過建議值 {budget}", "A stage has logic depth {d}, above the budget of {budget}"),
    "stage_over.fix": _e("在這一級中間再切一級 pipeline", "Split that stage with another pipeline register"),

    # ------------------------------------------------------------ 不適用原因
    "na.comb_only": _e("純組合電路，沒有暫存器", "purely combinational, no registers"),
    "na.syntax_error": _e("語法錯誤，無法合成", "syntax errors, cannot synthesize"),
    "na.no_yosys": _e("未安裝 Yosys（pip install yowasp-yosys）", "Yosys not installed (pip install yowasp-yosys)"),
    "na.no_compiler": _e("沒有可用的編譯器", "no compiler available"),
    "na.synth_failed": _e("合成失敗", "synthesis failed"),

    # ------------------------------------------------------------ 進度 / 錯誤
    "prog.parse": _e("解析原始碼…", "Parsing source…"),
    "prog.syntax": _e("語法檢查（iverilog）…", "Checking syntax (iverilog)…"),
    "prog.synth": _e("合成與時序分析（Yosys）…", "Synthesis and timing analysis (Yosys)…"),
    "prog.report": _e("產生報告…", "Writing report…"),
    "err.no_module": _e("找不到可評分的 module（testbench 會被略過）", "No module to grade (testbenches are skipped)"),
    "err.top_not_found": _e("找不到 module '{top}'（有：{names}）", "Module '{top}' not found (available: {names})"),
    "err.file_not_found": _e("找不到檔案：{path}", "File not found: {path}"),

    # ------------------------------------------------------------ 命令列報告
    "rep.title": _e("vgrade {ver} 評分報告   top: {top}   檔案: {files}", "vgrade {ver} report   top: {top}   files: {files}"),
    "rep.total": _e("總分 {total} / 100   等級 {grade}", "Score {total} / 100   Grade {grade}"),
    "rep.capped": _e("（有錯誤時等級最高 {cap}）", " (capped at {cap} because of errors)"),
    "rep.breakdown": _e("= 類別加權 {w} − 嚴重度扣分 {p}（錯誤 ×4、警告 ×1）",
                        "= weighted categories {w} − severity penalty {p} (error ×4, warning ×1)"),
    "rep.col_cat": _e("類別", "Category"),
    "rep.col_score": _e("分數", "Score"),
    "rep.col_weight": _e("權重", "Weight"),
    "rep.stats": _e("設計統計（目標：{target}）", "Design statistics (target: {target})"),
    "rep.regs": _e("暫存器        : {n} bits（無 reset {nr} bits）", "Registers     : {n} bits ({nr} bits without reset)"),
    "rep.latch": _e("Latch         : {n} bits", "Latches       : {n} bits"),
    "rep.comb": _e("組合邏輯      : {n} 個 {unit}", "Logic         : {n} {unit}"),
    "rep.depth": _e("最長邏輯深度  : {d} 級（{kind}，終點 {end}；建議 <= {budget}）",
                    "Longest depth : {d} levels ({kind}, ends at {end}; budget <= {budget})"),
    "rep.paths": _e("各類路徑深度  : {text}", "Path depths   : {text}"),
    "rep.stages": _e("各級深度      : {text}", "Stage depths  : {text}"),
    "rep.stage_item": _e("S{k}: {d} 級/{b}b", "S{k}: {d} lv/{b}b"),
    "rep.latency": _e("輸出延遲      : {text}", "Output latency: {text}"),
    "rep.issues": _e("問題清單（{n} 項）", "Issues ({n})"),
    "rep.no_issues": _e("沒有發現問題，做得好！", "No issues found. Nice work!"),
    "rep.html_saved": _e("HTML 報告：{path}", "HTML report: {path}"),

    # ------------------------------------------------------------ HTML 報告
    "html.title": _e("vgrade 評分報告 · {top}", "vgrade report · {top}"),
    "html.h1": _e("Verilog 評分報告：{top}", "Verilog grading report: {top}"),
    "html.meta": _e("檔案：{files}　·　時序目標：{target}　·　{time}　·　vgrade {ver}",
                    "Files: {files}　·　Timing target: {target}　·　{time}　·　vgrade {ver}"),
    "html.of100": _e("/ 100 分", "/ 100"),
    "html.grade": _e("等級 {g}", "Grade {g}"),
    "html.breakdown": _e("類別加權 {w} − 嚴重度扣分 {p}", "weighted {w} − severity penalty {p}"),
    "html.capped": _e("有「錯誤」等級的問題，等級最高為 {cap}", "Errors present: grade capped at {cap}"),
    "html.cat_scores": _e("各類別分數", "Category scores"),
    "html.na": _e("不適用：{reason}", "Not applicable: {reason}"),
    "html.bar_tip": _e("{label}：{s} 分（權重 {w}%），{n} 個問題", "{label}: {s} points (weight {w}%), {n} issues"),
    "html.t_regs": _e("暫存器", "Registers"),
    "html.t_regs_sub": _e("無 reset：{n} bits", "{n} bits without reset"),
    "html.t_latch": _e("Latch", "Latches"),
    "html.t_latch_bad": _e("應為 0", "should be 0"),
    "html.t_latch_ok": _e("沒有推論出 latch", "no latch inferred"),
    "html.t_logic": _e("組合邏輯", "Logic"),
    "html.t_logic_sub": _e("合成後數量", "after synthesis"),
    "html.t_depth": _e("最長邏輯深度", "Longest logic depth"),
    "html.t_depth_sub": _e("建議 ≤ {budget}　{kind} {end}", "budget ≤ {budget}　{kind} {end}"),
    "html.t_latency": _e("輸出延遲", "Output latency"),
    "html.t_latency_sub": _e("各輸出：{text}", "per output: {text}"),
    "html.t_issues": _e("問題", "Issues"),
    "html.t_issues_sub": _e("錯誤 {e} · 警告 {w} · 建議 {i}", "{e} errors · {w} warnings · {i} info"),
    "html.u_bits": _e("bits", "bits"),
    "html.u_levels": _e("級", "levels"),
    "html.u_cycle": _e("cycle", "cycles"),
    "html.u_items": _e("項", ""),
    "html.stage_title": _e("Pipeline 各級邏輯深度", "Logic depth per pipeline stage"),
    "html.stage_sub": _e("S<i>n</i> = 距離輸入第 n 級暫存器；各級深度越接近越平衡",
                         "S<i>n</i> = registers n stages from the inputs; similar depths mean a balanced pipeline"),
    "html.stage_aria": _e("各 pipeline 級的最大邏輯深度長條圖，建議上限 {budget}",
                          "Bar chart of the maximum logic depth per pipeline stage, budget {budget}"),
    "html.stage_tip": _e("第 {k} 級：最大邏輯深度 {d} 級 {unit}，{b} 個暫存器 bit",
                         "Stage {k}: max logic depth {d} {unit} levels, {b} register bits"),
    "html.over": _e("（超過建議上限）", " (over budget)"),
    "html.budget": _e("建議上限 {budget}", "budget {budget}"),
    "html.budget_unit": _e("建議上限 {budget} 級 {unit}", "budget {budget} {unit} levels"),
    "html.no_stage": _e("沒有可分級的暫存器。", "No registers to group into stages."),
    "html.path_title": _e("各類路徑最長深度", "Longest depth per path type"),
    "html.path_sub": _e("輸入→輸出若有路徑，代表輸出沒有經過暫存器",
                        "An input → output path means an output is not registered"),
    "html.p_in2reg": _e("輸入 → 暫存器", "input → reg"),
    "html.p_reg2reg": _e("暫存器 → 暫存器", "reg → reg"),
    "html.p_reg2out": _e("暫存器 → 輸出", "reg → output"),
    "html.p_in2out": _e("輸入 → 輸出", "input → output"),
    "html.p_none": _e("無", "none"),
    "html.p_none_tip": _e("{label}：沒有這類路徑", "{label}: no such path"),
    "html.p_tip": _e("{label}：最長 {d} 級 {unit}", "{label}: longest {d} {unit} levels"),
    "html.issues": _e("問題清單", "Issues"),
    "html.filter": _e("篩選", "Filter"),
    "html.all_cats": _e("全部類別", "All categories"),
    "html.th_sev": _e("嚴重度", "Severity"),
    "html.th_cat": _e("類別", "Category"),
    "html.th_loc": _e("位置", "Location"),
    "html.th_msg": _e("問題與建議", "Issue and suggestion"),
    "html.th_pen": _e("扣分", "Penalty"),
    "html.overall": _e("整體", "overall"),
    "html.no_match": _e("沒有符合篩選條件的項目。", "Nothing matches the filter."),
    "html.no_issues": _e("沒有發現任何問題，做得好！", "No issues found. Nice work!"),
    "html.src_title": _e("原始碼標註", "Annotated source"),
    "html.src_sub": _e("有問題的行以底色標示（紅：錯誤、黃：警告、灰：建議），說明寫在該行下方",
                       "Lines with issues are highlighted (red: error, yellow: warning, gray: info) "
                       "with notes underneath"),
    "html.src_summary": _e("· {lines} 行 · {n} 個標註", "· {lines} lines · {n} notes"),
    "html.pen_prefix": _e("扣分 ", "penalty "),
    "html.footer1": _e("評分方式：各類別滿分 100，依問題扣分；類別加權平均（{weights}，不適用的類別不計入），"
                       "再扣嚴重度分數（每個錯誤 −{pe}、每個警告 −{pw}，上限 {cap}）。"
                       "等級：A ≥ 90、B ≥ 80、C ≥ 70、D ≥ 60；有「錯誤」等級的問題時，等級最高為 {gcap}。",
                       "Scoring: each category starts at 100 and loses points per issue; the weighted average "
                       "({weights}; categories that do not apply are skipped) is then reduced by a severity "
                       "penalty (−{pe} per error, −{pw} per warning, at most {cap}). "
                       "Grades: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60; any error caps the grade at {gcap}."),
    "html.footer2": _e("時序與 pipeline 依 Yosys 合成後的 netlist 計算邏輯級數，為與製程無關的估計值；"
                       "FPGA 加法器實際使用 carry chain，寬加法器級數會被高估。",
                       "Timing and pipeline figures count logic levels in the Yosys netlist and are "
                       "technology-independent estimates; FPGA adders use carry chains, so wide adders "
                       "are over-estimated."),
    "html.footer3": _e("由 vgrade {ver} 產生", "Generated by vgrade {ver}"),

    # ------------------------------------------------------------ 桌面程式
    "app.title": _e("vgrade · Verilog 程式碼評分", "vgrade · Verilog code grader"),
    "app.subtitle": _e("Verilog 程式碼品質評分", "Verilog code quality grader"),
    "app.drop": _e("把 .v / .sv 檔或 Vivado 專案資料夾拖到這裡", "Drop .v / .sv files or a Vivado project folder here"),
    "app.drop_nodnd": _e("點這裡選擇 .v / .sv 檔案", "Click here to choose .v / .sv files"),
    "app.drop_sub": _e("或點一下選擇檔案（會自動略過模擬檔與 testbench）",
                       "or click to browse (simulation output and testbenches are skipped)"),
    "app.no_files": _e("尚未加入檔案", "No files added"),
    "app.n_files": _e("已加入 {n} 個檔案", "{n} file(s) added"),
    "app.add_folder": _e("加入資料夾…", "Add folder…"),
    "app.remove": _e("移除選取", "Remove selected"),
    "app.clear": _e("清除全部", "Clear all"),
    "app.top": _e("Top module", "Top module"),
    "app.auto": _e("自動判斷", "Auto-detect"),
    "app.target": _e("時序目標", "Timing target"),
    "app.run": _e("開始評分", "Grade"),
    "app.ready": _e("準備中…", "Starting…"),
    "app.done": _e("完成", "Done"),
    "app.done_multi": _e("完成 · 有多個 top，可在選單切換", "Done · several tops found, pick one in the menu"),
    "app.failed": _e("評分失敗", "Grading failed"),
    "app.no_verilog": _e("沒有找到可評分的 Verilog 檔", "No Verilog files found"),
    "app.need_files": _e("請先加入要評分的 Verilog 檔案。", "Add the Verilog files to grade first."),
    "app.crash": _e("評分時發生錯誤：", "An error occurred while grading:"),
    "app.of100": _e("/ 100 分", "/ 100"),
    "app.grade": _e("等級 {g}", "Grade {g}"),
    "app.grade_capped": _e("等級 {g}（有錯誤）", "Grade {g} (errors)"),
    "app.top_is": _e("top：{top}", "top: {top}"),
    "app.col_cat": _e("類別", "Category"),
    "app.col_score": _e("分數", "Score"),
    "app.col_weight": _e("權重", "Weight"),
    "app.col_issues": _e("問題數", "Issues"),
    "app.summary": _e("錯誤 {e} · 警告 {w} · 建議 {i}", "{e} errors · {w} warnings · {i} info"),
    "app.summary_synth": _e("暫存器 {ff} bits · latch {latch} bits · 最長邏輯深度 {d} 級（建議 ≤ {budget}）",
                            "Regs {ff} b · latches {latch} b · max depth {d} (budget ≤ {budget})"),
    "app.open": _e("開啟完整報告", "Open full report"),
    "app.save": _e("另存報告…", "Save report…"),
    "app.folder": _e("報告資料夾", "Reports folder"),
    "app.saved": _e("已另存：{name}", "Saved: {name}"),
    "app.pick_files": _e("選擇 Verilog 檔案", "Choose Verilog files"),
    "app.pick_folder": _e("選擇專案資料夾", "Choose project folder"),
    "app.all_files": _e("所有檔案", "All files"),
    "app.save_title": _e("另存評分報告", "Save grading report"),
    "app.html_report": _e("HTML 報告", "HTML report"),
    "app.iv_ok": _e("iverilog：已找到", "iverilog: found"),
    "app.iv_missing": _e("iverilog：未安裝（語法改由 Yosys 檢查）", "iverilog: not installed (Yosys checks syntax)"),
    "app.ys_ok": _e("Yosys：可用", "Yosys: available"),
    "app.ys_missing": _e("Yosys：找不到（無法做時序與暫存器分析）", "Yosys: not found (no timing/register analysis)"),
    "app.language": _e("語言", "Language"),
}
