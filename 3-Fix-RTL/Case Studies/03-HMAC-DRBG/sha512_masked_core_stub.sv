// Formal-verification blackbox stand-in for sha512_masked_core.sv.
// Both the OneSpin (onespin_prove.tcl: -black_box_instances) and VC Formal
// (vcf_prove.tcl: set_blackbox -cells) flows treat every instance of this
// module as a black box: the actual SHA-512/masking arithmetic is out of
// scope for the hmac_drbg control-FSM property suite, which only cares
// that the surrounding FSM reacts correctly to *some* ready/digest_valid
// response, not to any particular digest value. Reproduced here the same
// way an unconnected miter port is free in BMC: every output is a plain
// `wire` with no driver, so yosys treats it as a genuinely free input the
// solver can pick independently each cycle.

module sha512_masked_core #(
  parameter [73 : 0] LFSR_INIT_SEED = 74'h23A_A79D_0EC1_1E38_9277
) (
  input  wire            clk,
  input  wire            reset_n,
  input  wire            zeroize,
  input  wire            init_cmd,
  input  wire            next_cmd,
  input  wire [1 : 0]    mode,
  input  wire [73 : 0]   lfsr_seed,
  input  wire [1023 : 0] block_msg,

  output wire           ready,
  output wire [511 : 0] digest,
  output wire           digest_valid
);
endmodule
