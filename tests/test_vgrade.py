"""vgrade 單元測試。執行：python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vgrade  # noqa: E402

EXAMPLES = ROOT / "examples"
HAS_SYNTH = vgrade.yosys_command() is not None


def source_check(code: str) -> vgrade.Grader:
    """只跑原始碼層級的檢查（不需要 Yosys）。"""
    g = vgrade.Grader()
    src = vgrade.Source(Path("t.v"), "t.v", code)
    mods = vgrade.parse_modules(src)
    vgrade.check_style(g, src, mods, 100)
    for m in mods:
        if not m.is_testbench:
            vgrade.check_coding(g, m)
    return g


def rules(g: vgrade.Grader, cat: str | None = None) -> str:
    return "\n".join(i.message for i in g.issues if cat is None or i.category == cat)


class TestParsing(unittest.TestCase):
    def test_comments_and_strings_removed(self):
        code = 'module m; // always @(a) x <= 1;\n /* q = 1; */ initial $display("a = b;"); endmodule'
        stripped = vgrade.strip_comments(code)
        self.assertNotIn("x <= 1", stripped)
        self.assertNotIn("q = 1", stripped)
        self.assertNotIn("a = b", stripped)
        self.assertEqual(stripped.count("\n"), code.count("\n"))

    def test_statement_parser(self):
        code = """
            if (a <= b) q <= c;
            else begin
              case (s)
                IDLE, WAIT: begin n = 1; end
                2'b10: {x, y[3:0]} <= z[idx];
                default: ;
              endcase
            end"""
        assigns, conds = vgrade.parse_statements(code, 0, len(code))
        ops = [(sorted(a.lhs), a.op) for a in assigns]
        self.assertEqual(ops, [(["q"], "<="), (["n"], "="), (["x", "y"], "<=")])
        self.assertEqual(conds, {"a", "b", "s"})
        self.assertIn("idx", assigns[2].reads)

    def test_declarations(self):
        src = vgrade.Source(Path("t.v"), "t.v",
                            "module m #(parameter W=8) (input [W-1:0] a, output reg q);\n"
                            "reg reg_c, d; wire signed [3:0] e; localparam S_IDLE = 0; endmodule")
        mod = vgrade.parse_modules(src)[0]
        self.assertEqual(set(mod.signals), {"a", "q", "reg_c", "d", "e"})
        self.assertEqual(mod.params, {"W", "S_IDLE"})


class TestSourceChecks(unittest.TestCase):
    def test_blocking_in_sequential(self):
        g = source_check("module m(input clk, input d, output reg q);\n"
                         "always @(posedge clk) q = d;\nendmodule")
        self.assertIn("blocking", rules(g, "coding"))

    def test_nonblocking_in_comb(self):
        g = source_check("module m(input a, output reg y);\nalways @(*) y <= a;\nendmodule")
        self.assertIn("non-blocking", rules(g, "coding"))

    def test_incomplete_sensitivity(self):
        g = source_check("module m(input a, input b, output reg y);\n"
                         "always @(a) y = a & b;\nendmodule")
        self.assertIn("缺少：b", rules(g, "coding"))

    def test_complete_manual_sensitivity_is_only_info(self):
        g = source_check("module m(input a, input b, output reg y);\n"
                         "always @(a or b) y = a & b;\nendmodule")
        self.assertNotIn("缺少", rules(g))
        self.assertTrue(any(i.severity == "info" and "敏感列表" in i.message for i in g.issues))

    def test_multi_driver(self):
        g = source_check("module m(input clk, input a, output reg y);\n"
                         "always @(posedge clk) y <= a;\nalways @(*) y = 1'b0;\nendmodule")
        self.assertIn("多重驅動", rules(g, "coding"))

    def test_gated_clock_and_casex(self):
        g = source_check("module m(input clk, input en, input [1:0] s, output reg q);\n"
                         "wire gclk;\nassign gclk = clk & en;\n"
                         "always @(posedge gclk) casex (s) 2'b1x: q <= 1; default: q <= 0; endcase\n"
                         "endmodule")
        text = rules(g, "coding")
        self.assertIn("gated", text)
        self.assertIn("casex", text)

    def test_comb_case_without_default(self):
        g = source_check("module m(input [1:0] s, output reg y);\n"
                         "always @(*) case (s) 2'd0: y = 1; 2'd1: y = 0; endcase\nendmodule")
        self.assertIn("沒有 default", rules(g, "coding"))

    def test_naming_and_style(self):
        g = source_check("module MyMod(clk, dataIn);\ninput clk;\ninput dataIn;\n"
                         "parameter width = 4;\nendmodule")
        text = rules(g, "style")
        self.assertIn("MyMod", text)
        self.assertIn("dataIn", text)
        self.assertIn("width", text)
        self.assertIn("舊式", text)

    def test_clean_code_has_no_coding_issues(self):
        g = source_check("""module ok (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [3:0] d,
    output reg  [3:0] q
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) q <= 4'd0;
        else        q <= d;
    end
endmodule""")
        self.assertEqual(rules(g, "coding"), "")


class TestGradeLetter(unittest.TestCase):
    def test_error_caps_grade_at_c(self):
        self.assertEqual(vgrade.grade_letter(95), "A")
        self.assertEqual(vgrade.grade_letter(95, has_error=True), "C")
        self.assertEqual(vgrade.grade_letter(85, has_error=True), "C")
        self.assertEqual(vgrade.grade_letter(65, has_error=True), "D")

    def test_severity_penalty(self):
        g = vgrade.Grader()
        g.add("coding", "error", "e1")
        g.add("coding", "warning", "w1")
        g.add("style", "info", "i1")
        self.assertEqual(g.severity_penalty(), 5)
        self.assertEqual(g.total(), 95)
        for k in range(20):
            g.add("coding", "error", f"e{k + 2}")
        self.assertEqual(g.severity_penalty(), vgrade.SEVERITY_CAP)


class TestControlNames(unittest.TestCase):
    def test_control_detection(self):
        for n in ("out_valid", "state", "cnt", "rd_ptr", "busy", "u0.s1_valid"):
            self.assertTrue(vgrade._is_control(n), n)
        for n in ("s1_a", "s2_prod", "data_q", "acc"):
            self.assertFalse(vgrade._is_control(n), n)


@unittest.skipUnless(HAS_SYNTH, "需要 Yosys（pip install yowasp-yosys）")
class TestSynthesis(unittest.TestCase):
    def run_grade(self, name):
        g, _ = vgrade.grade([EXAMPLES / name], None, "fpga", None)
        return g

    def test_good_design_scores_high(self):
        g = self.run_grade("good_mac.v")
        self.assertGreaterEqual(g.total(), 90)
        self.assertEqual(g.stats["latch_bits"], 0)
        self.assertEqual(g.stats["latency"], {"acc": 3, "out_valid": 3})

    def test_bad_design_findings(self):
        g = self.run_grade("bad_design.v")
        self.assertLess(g.total(), 70)
        text = rules(g)
        for expected in ("latch：tmp", "latch：flag", "unused_cnt", "div", "輸出 'r'", "gated"):
            self.assertIn(expected, text)

    def test_html_report(self):
        from report_html import render_html
        g, top = vgrade.grade([EXAMPLES / "bad_design.v"], None, "fpga", None)
        page = render_html(g, top, vgrade.__version__)
        self.assertIn("<title>vgrade 評分報告 · BadDesign</title>", page)
        self.assertIn('id="L-0-17"', page)                 # 原始碼行錨點
        self.assertIn('href="#L-0-17"', page)              # 問題清單連到原始碼
        self.assertIn("&lt;=", page)                       # 原始碼有被跳脫
        self.assertNotIn("<= b", page)
        self.assertEqual(page.count('<tr data-sev='), len(g.issues))

    def test_combinational_design_pipeline_na(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "add.v"
            f.write_text("module add(input [3:0] a, b, output [4:0] y);\nassign y = a + b;\nendmodule\n")
            g, _ = vgrade.grade([f], None, "fpga", None)
        self.assertIn("pipeline", g.na)


if __name__ == "__main__":
    unittest.main()
