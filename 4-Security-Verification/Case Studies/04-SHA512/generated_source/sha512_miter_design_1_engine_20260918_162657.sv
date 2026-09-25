module sha512_miter
  (
  input           clk,
  input           rst,
  input   [31:0]  text_i1, text_i2,
  output  [31:0]  text_o1, text_o2,
  input   [3:0]   cmd_i,
  input           cmd_w_i,
  output  [4:0]   cmd_o1, cmd_o2
  );

  sha512 SHA512_1
  (
    .clk_i(clk),
    .rst_i(rst),
    .text_i(text_i1),
    .text_o(text_o1),
    .cmd_i(cmd_i),
    .cmd_w_i(cmd_w_i),
    .cmd_o(cmd_o1)
  );

  sha512 SHA512_2
  (
    .clk_i(clk),
    .rst_i(rst),
    .text_i(text_i2),
    .text_o(text_o2),
    .cmd_i(cmd_i),
    .cmd_w_i(cmd_w_i),
    .cmd_o(cmd_o2)
  );

  reg init;
  initial init = 1;

  always @(posedge clk) begin
    if (init) begin
      assume(rst);
      init <= 0;
    end else if (!rst) begin
      // === DIT GOAL (do not modify) ===
      assert(cmd_o1 == cmd_o2);
      // === END DIT GOAL ===
      // === AUX INVARIANTS (loop-managed) ===
      // === END AUX INVARIANTS ===
    end
  end

endmodule
