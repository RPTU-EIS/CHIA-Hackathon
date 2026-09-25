// Formal-verification blackbox stand-in for hmac_drbg_lfsr.sv - see
// sha512_masked_core_stub.sv for why: both reference tcl flows black-box
// `lfsr_inst` (this module) alongside the two SHA-512 core instances.

module hmac_drbg_lfsr #(
  parameter                  REG_SIZE  = 148,
  parameter [REG_SIZE-1 : 0] INIT_SEED = 148'h5_60DE_54E3_6AC0_807B_2396_8E54_5475_3CAB_FFB0
) (
  input  wire                    clk,
  input  wire                    reset_n,
  input  wire                    zeroize,
  input  wire                    en,
  input  wire  [REG_SIZE-1 : 0]  seed,

  output wire  [REG_SIZE-1 : 0]  rnd
);
endmodule
