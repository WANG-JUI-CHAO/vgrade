// 3-stage pipelined multiply-accumulate
//   S1: register inputs
//   S2: multiply
//   S3: accumulate, registered output
module good_mac #(
    parameter DATA_W = 8,
    parameter ACC_W  = 20
) (
    input  wire                     clk,
    input  wire                     rst_n,
    input  wire                     in_valid,
    input  wire signed [DATA_W-1:0] a,
    input  wire signed [DATA_W-1:0] b,
    output reg                      out_valid,
    output reg  signed [ACC_W-1:0]  acc
);

    // stage 1: input registers
    reg                     s1_valid;
    reg signed [DATA_W-1:0] s1_a;
    reg signed [DATA_W-1:0] s1_b;

    // stage 2: product register
    reg                       s2_valid;
    reg signed [2*DATA_W-1:0] s2_prod;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s1_valid  <= 1'b0;
            s2_valid  <= 1'b0;
            out_valid <= 1'b0;
        end else begin
            s1_valid  <= in_valid;
            s2_valid  <= s1_valid;
            out_valid <= s2_valid;
        end
    end

    // datapath registers (no reset needed for pure data)
    always @(posedge clk) begin
        s1_a    <= a;
        s1_b    <= b;
        s2_prod <= s1_a * s1_b;
    end

    // stage 3: accumulate
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            acc <= {ACC_W{1'b0}};
        end else if (s2_valid) begin
            acc <= acc + s2_prod;
        end
    end

endmodule
