// Intentionally bad design used to exercise every vgrade check
module BadDesign(clk, rst, en, a, b, sel, q, r, flag);
input clk, rst, en;
input [15:0] a, b;
input [1:0] sel;
output [15:0] q;
output [15:0] r;
output flag;
reg [15:0] q;
reg [15:0] tmp;
reg flag;
reg [7:0] unused_cnt;
wire gclk;

assign gclk = clk & en;            // gated clock

always @(a or sel)                 // incomplete sensitivity list (b missing)
    case (sel)                     // no default -> latch
        2'd0: tmp = a + b;
        2'd1: tmp = a - b;
    endcase

always @(posedge gclk) begin
    q = tmp / b;                   // blocking in sequential + divider
    unused_cnt <= unused_cnt + 1;  // never used
end

always @(*) begin
    if (a[0]) flag <= 1'b1;        // nonblocking in comb + latch
end

assign r = a * b + tmp;            // input -> output combinational path
endmodule
