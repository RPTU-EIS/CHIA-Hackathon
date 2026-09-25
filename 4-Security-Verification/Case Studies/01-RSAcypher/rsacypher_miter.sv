module rsacypher_miter
  #(parameter KEYSIZE = 32)
  (
  input clk,
  input rst,

  input  [KEYSIZE-1:0] indata1, indata2,
  input  [KEYSIZE-1:0] inExp1, inExp2,
  input  [KEYSIZE-1:0] inMod1, inMod2,

  input  ds
  );

  wire ready1, ready2;

  RSACypher C1
  (
    .clk(clk),
    .reset(rst),
    .indata(indata1),
    .inExp(inExp1),
    .inMod(inMod1),
    .cypher(),
    .ds(ds),
    .ready(ready1)
  );

  RSACypher C2
  (
    .clk(clk),
    .reset(rst),
    .indata(indata2),
    .inExp(inExp2),
    .inMod(inMod2),
    .cypher(),
    .ds(ds),
    .ready(ready2)
  );

  reg init;
  initial init = 1;

  always @(posedge clk) begin
    if (init) begin
      assume(rst);
      init <= 0;
    end else if (!rst) begin
      // === DIT GOAL (do not modify) ===
      assert(ready1 == ready2);
      // === END DIT GOAL ===
      // === AUX INVARIANTS (loop-managed) ===
      assume(indata1 != 0);
      assume(indata2 != 0);
      assume(inExp1 > 1);
      assume(inExp2 > 1);
      // === END AUX INVARIANTS ===
    end
  end

endmodule
