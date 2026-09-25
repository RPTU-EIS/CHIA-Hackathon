// Translation of fv_hmac_drbg.sv/fv_constraints.sv/fv_coverpoints.sv's
// concurrent-SVA property suite into plain immediate assert/assume/cover
// inside ordinary always blocks, for sby's yosys+smtbmc flow (yosys has no
// concurrent-SVA support at all). Bound directly into hmac_drbg (single
// instance - no P1/P2 miter here, this is a functional/FSM correctness
// suite, not a 2-instance security-property check), so all references to
// hmac_drbg's own ports/registers are unqualified.
//
// $past(x, N) is reimplemented as an explicit N-deep shift register per
// referenced signal (h_*), sampled once per cycle regardless of which
// property needs it - since every property's own $past offset is relative
// to whatever cycle it's being evaluated at, a single shared history bank
// serves all of them; no per-property snapshot-on-trigger is needed here
// (unlike ibex's paired rs1/rs2 properties, no two of these transition
// properties can ever be simultaneously in-flight, since they trigger on
// mutually-exclusive FSM states and the state changes before any
// property's own window could re-trigger the same one).
//
// Each `##1 (cond)[*N] and ... ##N final_state && ...` sequence becomes a
// fixed N-cycle countdown: on trigger, arm a per-property `pending`/
// `countdown` pair; every cycle while pending, check the per-cycle window
// condition (drbg_vld==0, fv_input_data_rdy==0, etc); on the last cycle
// (countdown reaches 0) additionally check the target state and the
// register-equality conditions, referencing the shared history bank at
// the right depth.

module hmac_checker (
  input logic clk,
  input logic reset_n,
  input logic zeroize,
  input logic init_cmd,
  input logic next_cmd,
  input logic [383:0] entropy,
  input logic [383:0] nonce,
  input logic ready,
  input logic valid,
  input logic [383:0] drbg,
  input logic HMAC_init,
  input logic HMAC_next,
  input logic [383:0] HMAC_key,
  input logic [1023:0] HMAC_block,
  input logic HMAC_ready,
  input logic HMAC_tag_valid,
  input logic [383:0] HMAC_tag,
  input logic [7:0] cnt_reg,
  input logic [383:0] K_reg,
  input logic [383:0] V_reg,
  input logic [4:0] drbg_st_reg
);
  import fv_hmac_drbg_pkg::*;

  localparam [4:0] ST_IDLE = 5'd0, ST_K10 = 5'd3, ST_K11 = 5'd4, ST_V1 = 5'd5,
                   ST_K20  = 5'd7, ST_K21 = 5'd8, ST_V2  = 5'd9, ST_T  = 5'd10,
                   ST_K3   = 5'd12, ST_V3 = 5'd13, ST_DONE = 5'd14;

  wire idle = (drbg_st_reg == ST_IDLE);
  wire K10  = (drbg_st_reg == ST_K10);
  wire K11  = (drbg_st_reg == ST_K11);
  wire V1   = (drbg_st_reg == ST_V1);
  wire K20  = (drbg_st_reg == ST_K20);
  wire K21  = (drbg_st_reg == ST_K21);
  wire V2   = (drbg_st_reg == ST_V2);
  wire T    = (drbg_st_reg == ST_T);
  wire K3   = (drbg_st_reg == ST_K3);
  wire V3   = (drbg_st_reg == ST_V3);
  wire Done = (drbg_st_reg == ST_DONE);

  wire rst = !reset_n || zeroize;

  // Checker-local names matching the original property file's port names.
  wire hmac_tag_vld_sig      = valid;      // hmac_drbg.valid -> drbg_vld
  wire fv_input_data_rdy_sig = ready;       // hmac_drbg.ready -> fv_input_data_rdy
  wire fv_input_data_vld_sig = (init_cmd || next_cmd) && HMAC_ready;

  // -----------------------------------------------------------
  // - Shared $past history bank (depth 3, the deepest offset   -
  // - any property needs)                                      -
  // -----------------------------------------------------------
  reg [7:0]   h_cnt   [1:3];
  reg [383:0] h_key   [1:3];
  reg [383:0] h_v     [1:3];
  reg [383:0] h_tag   [1:3];
  reg [383:0] h_entropy [1:3];
  reg [383:0] h_nonce   [1:3];
  reg [1023:0] h_msg;
  reg          h_drbgvld;
  reg          h_hmactagvalid;

  always @(posedge clk) begin
    h_cnt[3] <= h_cnt[2]; h_cnt[2] <= h_cnt[1]; h_cnt[1] <= cnt_reg;
    h_key[3] <= h_key[2]; h_key[2] <= h_key[1]; h_key[1] <= K_reg;
    h_v[3]   <= h_v[2];   h_v[2]   <= h_v[1];   h_v[1]   <= V_reg;
    h_tag[3] <= h_tag[2]; h_tag[2] <= h_tag[1]; h_tag[1] <= HMAC_tag;
    h_entropy[3] <= h_entropy[2]; h_entropy[2] <= h_entropy[1]; h_entropy[1] <= entropy;
    h_nonce[3]   <= h_nonce[2];   h_nonce[2]   <= h_nonce[1];   h_nonce[1]   <= nonce;
    h_msg          <= HMAC_block;
    h_drbgvld      <= valid;
    h_hmactagvalid <= HMAC_tag_valid;
  end

  // Force exactly one reset pulse at the very start of every BMC trace, so
  // every property is checked against a genuinely reachable post-reset
  // state, not an arbitrary unconstrained initial one.
  reg f_init;
  initial f_init = 1'b1;
  always @(posedge clk) begin
    if (f_init) assume(!reset_n);
    f_init <= 1'b0;
  end

  wire hmac_tag_vld = HMAC_tag_valid && !h_hmactagvalid;

  // ---------------
  // - reset check -
  // ---------------
  reg rst_q;
  always @(posedge clk) rst_q <= rst;
  always @(posedge clk) begin
    if (!rst && rst_q) begin
      assert_reset: assert(
        idle && (cnt_reg == 8'd0) && (HMAC_init == 1'b0) &&
        (HMAC_key == 384'd0) && (HMAC_block == 1024'd0) && (HMAC_next == 1'b0) &&
        (K_reg == 384'd0) && (V_reg == 384'd0) &&
        (valid == 1'b0) && (ready == 1'b0)
      );
    end
  end

  // --------------------------
  // - Done -> idle (N=1)     -
  // --------------------------
  reg pend_Done_to_idle;
  always @(posedge clk) begin
    if (rst) begin
      pend_Done_to_idle <= 1'b0;
    end else begin
      pend_Done_to_idle <= Done;
      if (pend_Done_to_idle) begin
        assert_Done_to_idle: assert(
          idle && (cnt_reg == h_cnt[1]) && (drbg == h_tag[1]) &&
          (HMAC_block == h_msg) && (K_reg == h_key[1]) && (V_reg == h_tag[1]) &&
          (valid == 1'b1) && (ready == 1'b0)
        );
      end
    end
  end

  // ------------------------------------------------------------
  // - "wait" properties (N=1: stay in state while no response) -
  // ------------------------------------------------------------
  reg pend_K10_wait, pend_K11_wait, pend_K20_wait, pend_K21_wait, pend_K3_wait,
      pend_T_wait, pend_V1_wait, pend_V2_wait, pend_V3_wait, pend_idle_wait;
  always @(posedge clk) begin
    if (rst) begin
      pend_K10_wait <= 1'b0; pend_K11_wait <= 1'b0; pend_K20_wait <= 1'b0;
      pend_K21_wait <= 1'b0; pend_K3_wait  <= 1'b0; pend_T_wait   <= 1'b0;
      pend_V1_wait  <= 1'b0; pend_V2_wait  <= 1'b0; pend_V3_wait  <= 1'b0;
      pend_idle_wait <= 1'b0;
    end else begin
      pend_K10_wait <= K10 && !hmac_tag_vld;
      pend_K11_wait <= K11 && !hmac_tag_vld;
      pend_K20_wait <= K20 && !hmac_tag_vld;
      pend_K21_wait <= K21 && !hmac_tag_vld;
      pend_K3_wait  <= K3  && !hmac_tag_vld;
      pend_T_wait   <= T   && !hmac_tag_vld;
      pend_V1_wait  <= V1  && !hmac_tag_vld;
      pend_V2_wait  <= V2  && !hmac_tag_vld;
      pend_V3_wait  <= V3  && !hmac_tag_vld;
      pend_idle_wait <= idle && !fv_input_data_vld_sig;

      if (pend_K10_wait) assert_K10_wait: assert(
        K10 && (cnt_reg == h_cnt[1]) && (K_reg == h_key[1]) && (V_reg == h_v[1]) &&
        (valid == 1'b0) && (ready == 1'b0));
      if (pend_K11_wait) assert_K11_wait: assert(
        K11 && (cnt_reg == h_cnt[1]) && (K_reg == h_key[1]) &&
        (valid == 1'b0) && (ready == 1'b0));
      if (pend_K20_wait) assert_K20_wait: assert(
        K20 && (cnt_reg == h_cnt[1]) && (K_reg == h_key[1]) &&
        (valid == 1'b0) && (ready == 1'b0));
      if (pend_K21_wait) assert_K21_wait: assert(
        K21 && (cnt_reg == h_cnt[1]) && (K_reg == h_key[1]) && (V_reg == h_v[1]) &&
        (valid == 1'b0) && (ready == 1'b0));
      if (pend_K3_wait) assert_K3_wait: assert(
        K3 && (cnt_reg == h_cnt[1]) && (K_reg == h_key[1]) &&
        (valid == 1'b0) && (ready == 1'b0));
      if (pend_T_wait) assert_T_wait: assert(
        T && (cnt_reg == h_cnt[1]) && (K_reg == h_key[1]) &&
        (valid == 1'b0) && (ready == 1'b0));
      if (pend_V1_wait) assert_V1_wait: assert(
        V1 && (cnt_reg == h_cnt[1]) && (V_reg == h_v[1]) &&
        (valid == 1'b0) && (ready == 1'b0));
      if (pend_V2_wait) assert_V2_wait: assert(
        V2 && (cnt_reg == h_cnt[1]) && (V_reg == h_v[1]) &&
        (valid == 1'b0) && (ready == 1'b0));
      if (pend_V3_wait) assert_V3_wait: assert(
        V3 && (cnt_reg == h_cnt[1]) && (V_reg == h_v[1]) &&
        (valid == 1'b0) && (ready == 1'b0));
      if (pend_idle_wait) assert_idle_wait: assert(
        idle && (cnt_reg == h_cnt[1]) && (K_reg == h_key[1]) && (V_reg == h_v[1]) &&
        (valid == h_drbgvld) && (ready == 1'b1));
    end
  end

  // ------------------------------------------------------------------
  // - Multi-cycle transition properties (N=2 or N=3 fixed countdown) -
  // ------------------------------------------------------------------
  reg pend_K10_to_K11, pend_K11_to_V1, pend_K20_to_K21, pend_K21_to_V2,
      pend_K3_to_V3, pend_T_to_Done, pend_T_to_K3, pend_V1_to_K20,
      pend_V2_to_T, pend_V3_to_T, pend_idle_to_K10, pend_idle_to_K10_1;
  reg [1:0] cnt_K10_to_K11, cnt_K11_to_V1, cnt_K20_to_K21, cnt_K21_to_V2,
            cnt_K3_to_V3, cnt_T_to_Done, cnt_T_to_K3, cnt_V1_to_K20,
            cnt_V2_to_T, cnt_V3_to_T, cnt_idle_to_K10, cnt_idle_to_K10_1;

  wire trig_K10_to_K11  = K10 && hmac_tag_vld;
  wire trig_K11_to_V1   = K11 && hmac_tag_vld;
  wire trig_K20_to_K21  = K20 && hmac_tag_vld;
  wire trig_K21_to_V2   = K21 && hmac_tag_vld;
  wire trig_K3_to_V3    = K3  && hmac_tag_vld;
  wire trig_T_to_Done   = T   && hmac_tag_vld && !((HMAC_tag == 384'd0) || (HMAC_tag >= HMAC_DRBG_PRIME));
  wire trig_T_to_K3     = T   && hmac_tag_vld &&  ((HMAC_tag == 384'd0) || (HMAC_tag >= HMAC_DRBG_PRIME));
  wire trig_V1_to_K20   = V1  && hmac_tag_vld;
  wire trig_V2_to_T     = V2  && hmac_tag_vld;
  wire trig_V3_to_T     = V3  && hmac_tag_vld;
  wire trig_idle_to_K10   = idle && fv_input_data_vld_sig &&  init_cmd;
  wire trig_idle_to_K10_1 = idle && fv_input_data_vld_sig && !init_cmd;

  always @(posedge clk) begin
    if (rst) begin
      pend_K10_to_K11 <= 1'b0; pend_K11_to_V1 <= 1'b0; pend_K20_to_K21 <= 1'b0;
      pend_K21_to_V2 <= 1'b0;  pend_K3_to_V3 <= 1'b0;  pend_T_to_Done <= 1'b0;
      pend_T_to_K3 <= 1'b0;    pend_V1_to_K20 <= 1'b0; pend_V2_to_T <= 1'b0;
      pend_V3_to_T <= 1'b0;    pend_idle_to_K10 <= 1'b0; pend_idle_to_K10_1 <= 1'b0;
    end else begin

      // --- N=2 transitions ---
      if (trig_K10_to_K11) begin
        pend_K10_to_K11 <= 1'b1; cnt_K10_to_K11 <= 2'd2;
      end else if (pend_K10_to_K11) begin
        assert_K10_to_K11_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_K10_to_K11 == 2'd1) begin
          assert_K10_to_K11: assert(
            K11 && (cnt_reg == h_cnt[2]) && (HMAC_init == 1'b0) &&
            (HMAC_key == h_key[2]) && (HMAC_block == k11_func(h_nonce[2])) &&
            (HMAC_next == 1'b1) && (K_reg == h_key[2]) && (V_reg == h_v[2]));
          pend_K10_to_K11 <= 1'b0;
        end else cnt_K10_to_K11 <= cnt_K10_to_K11 - 2'd1;
      end

      if (trig_K11_to_V1) begin
        pend_K11_to_V1 <= 1'b1; cnt_K11_to_V1 <= 2'd2;
      end else if (pend_K11_to_V1) begin
        assert_K11_to_V1_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_K11_to_V1 == 2'd1) begin
          assert_K11_to_V1: assert(
            V1 && (cnt_reg == h_cnt[2]) && (HMAC_init == 1'b1) &&
            (HMAC_key == h_tag[2]) && (HMAC_block == v1_func(h_v[2])) &&
            (HMAC_next == 1'b0) && (K_reg == h_tag[2]) && (V_reg == h_v[2]));
          pend_K11_to_V1 <= 1'b0;
        end else cnt_K11_to_V1 <= cnt_K11_to_V1 - 2'd1;
      end

      if (trig_K20_to_K21) begin
        pend_K20_to_K21 <= 1'b1; cnt_K20_to_K21 <= 2'd2;
      end else if (pend_K20_to_K21) begin
        assert_K20_to_K21_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_K20_to_K21 == 2'd1) begin
          assert_K20_to_K21: assert(
            K21 && (cnt_reg == h_cnt[2]) && (HMAC_init == 1'b0) &&
            (HMAC_key == h_key[2]) && (HMAC_block == k11_func(h_nonce[2])) &&
            (HMAC_next == 1'b1) && (K_reg == h_key[2]) && (V_reg == h_v[2]));
          pend_K20_to_K21 <= 1'b0;
        end else cnt_K20_to_K21 <= cnt_K20_to_K21 - 2'd1;
      end

      if (trig_K21_to_V2) begin
        pend_K21_to_V2 <= 1'b1; cnt_K21_to_V2 <= 2'd2;
      end else if (pend_K21_to_V2) begin
        assert_K21_to_V2_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_K21_to_V2 == 2'd1) begin
          assert_K21_to_V2: assert(
            V2 && (cnt_reg == h_cnt[2]) && (HMAC_init == 1'b1) &&
            (HMAC_key == h_tag[2]) && (HMAC_block == v1_func(h_v[2])) &&
            (HMAC_next == 1'b0) && (K_reg == h_tag[2]) && (V_reg == h_v[2]));
          pend_K21_to_V2 <= 1'b0;
        end else cnt_K21_to_V2 <= cnt_K21_to_V2 - 2'd1;
      end

      if (trig_K3_to_V3) begin
        pend_K3_to_V3 <= 1'b1; cnt_K3_to_V3 <= 2'd2;
      end else if (pend_K3_to_V3) begin
        assert_K3_to_V3_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_K3_to_V3 == 2'd1) begin
          assert_K3_to_V3: assert(
            V3 && (cnt_reg == h_cnt[2]) && (HMAC_init == 1'b1) &&
            (HMAC_key == h_tag[2]) && (HMAC_block == v1_func(h_v[2])) &&
            (HMAC_next == 1'b0) && (K_reg == h_tag[2]) && (V_reg == h_v[2]));
          pend_K3_to_V3 <= 1'b0;
        end else cnt_K3_to_V3 <= cnt_K3_to_V3 - 2'd1;
      end

      if (trig_T_to_Done) begin
        pend_T_to_Done <= 1'b1; cnt_T_to_Done <= 2'd2;
      end else if (pend_T_to_Done) begin
        assert_T_to_Done_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_T_to_Done == 2'd1) begin
          assert_T_to_Done: assert(
            Done && (cnt_reg == h_cnt[2]) && (HMAC_init == 1'b0) &&
            (HMAC_key == h_key[2]) && (HMAC_block == 1024'd0) &&
            (HMAC_next == 1'b0) && (K_reg == h_key[2]) && (V_reg == h_v[2]));
          pend_T_to_Done <= 1'b0;
        end else cnt_T_to_Done <= cnt_T_to_Done - 2'd1;
      end

      if (trig_V2_to_T) begin
        pend_V2_to_T <= 1'b1; cnt_V2_to_T <= 2'd2;
      end else if (pend_V2_to_T) begin
        assert_V2_to_T_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_V2_to_T == 2'd1) begin
          assert_V2_to_T: assert(
            T && (cnt_reg == h_cnt[2]) && (HMAC_init == 1'b1) &&
            (HMAC_key == h_key[2]) && (HMAC_block == v1_func(h_tag[2])) &&
            (HMAC_next == 1'b0) && (K_reg == h_key[2]) && (V_reg == h_tag[2]));
          pend_V2_to_T <= 1'b0;
        end else cnt_V2_to_T <= cnt_V2_to_T - 2'd1;
      end

      if (trig_V3_to_T) begin
        pend_V3_to_T <= 1'b1; cnt_V3_to_T <= 2'd2;
      end else if (pend_V3_to_T) begin
        assert_V3_to_T_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_V3_to_T == 2'd1) begin
          assert_V3_to_T: assert(
            T && (cnt_reg == h_cnt[2]) && (HMAC_init == 1'b1) &&
            (HMAC_key == h_key[2]) && (HMAC_block == v1_func(h_tag[2])) &&
            (HMAC_next == 1'b0) && (K_reg == h_key[2]) && (V_reg == h_tag[2]));
          pend_V3_to_T <= 1'b0;
        end else cnt_V3_to_T <= cnt_V3_to_T - 2'd1;
      end

      // --- N=3 transitions ---
      if (trig_T_to_K3) begin
        pend_T_to_K3 <= 1'b1; cnt_T_to_K3 <= 2'd3;
      end else if (pend_T_to_K3) begin
        assert_T_to_K3_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_T_to_K3 == 2'd1) begin
          assert_T_to_K3: assert(
            K3 && (cnt_reg == h_cnt[3]) && (HMAC_init == 1'b1) &&
            (HMAC_key == h_key[3]) && (HMAC_block == k3_func(h_v[3])) &&
            (HMAC_next == 1'b0) && (K_reg == h_key[3]) && (V_reg == h_v[3]));
          pend_T_to_K3 <= 1'b0;
        end else cnt_T_to_K3 <= cnt_T_to_K3 - 2'd1;
      end

      if (trig_V1_to_K20) begin
        pend_V1_to_K20 <= 1'b1; cnt_V1_to_K20 <= 2'd3;
      end else if (pend_V1_to_K20) begin
        assert_V1_to_K20_window: assert((valid == 1'b0) && (ready == 1'b0));
        if (cnt_V1_to_K20 == 2'd1) begin
          assert_V1_to_K20: assert(
            K20 && (cnt_reg == (8'd1 + h_cnt[3])) && (HMAC_init == 1'b1) &&
            (HMAC_key == h_key[3]) &&
            (HMAC_block == k10_func(h_tag[3], (h_cnt[3] + 8'd1), h_entropy[3], h_nonce[3])) &&
            (HMAC_next == 1'b0) && (K_reg == h_key[3]) && (V_reg == h_tag[3]));
          pend_V1_to_K20 <= 1'b0;
        end else cnt_V1_to_K20 <= cnt_V1_to_K20 - 2'd1;
      end

      // idle -> K10 (init): drbg_vld==0 held for all 3 cycles; ready
      // pulses to 1 ONLY on the first window cycle (t+1), not every
      // cycle - matches the reference's single-cycle (no `[*3]`) ready
      // conjunct, unlike drbg_vld's `[*3]`.
      if (trig_idle_to_K10) begin
        pend_idle_to_K10 <= 1'b1; cnt_idle_to_K10 <= 2'd3;
      end else if (pend_idle_to_K10) begin
        assert_idle_to_K10_vld_window: assert(valid == 1'b0);
        if (cnt_idle_to_K10 == 2'd3) assert_idle_to_K10_rdy_pulse: assert(ready == 1'b1);
        if (cnt_idle_to_K10 == 2'd1) begin
          assert_idle_to_K10: assert(
            K10 && (cnt_reg == 8'd0) && (HMAC_init == 1'b1) && (HMAC_key == K_INIT) &&
            (HMAC_block == k10_func(V_INIT, 8'd0, h_entropy[3], h_nonce[3])) &&
            (HMAC_next == 1'b0) && (K_reg == K_INIT) && (ready == 1'b0) && (V_reg == V_INIT));
          pend_idle_to_K10 <= 1'b0;
        end else cnt_idle_to_K10 <= cnt_idle_to_K10 - 2'd1;
      end

      if (trig_idle_to_K10_1) begin
        pend_idle_to_K10_1 <= 1'b1; cnt_idle_to_K10_1 <= 2'd3;
      end else if (pend_idle_to_K10_1) begin
        assert_idle_to_K10_1_vld_window: assert(valid == 1'b0);
        if (cnt_idle_to_K10_1 == 2'd3) assert_idle_to_K10_1_rdy_pulse: assert(ready == 1'b1);
        if (cnt_idle_to_K10_1 == 2'd1) begin
          assert_idle_to_K10_1: assert(
            K10 && (cnt_reg == (8'd1 + h_cnt[3])) && (HMAC_init == 1'b1) &&
            (HMAC_key == h_key[3]) &&
            (HMAC_block == k10_func(h_v[3], (h_cnt[3] + 8'd1), h_entropy[3], h_nonce[3])) &&
            (HMAC_next == 1'b0) && (K_reg == h_key[3]) && (V_reg == h_v[3]) && (ready == 1'b0));
          pend_idle_to_K10_1 <= 1'b0;
        end else cnt_idle_to_K10_1 <= cnt_idle_to_K10_1 - 2'd1;
      end
    end
  end

  // -----------------
  // - fv_constraints -
  // -----------------
  // Rule 6-style note: these are ORDINARY functional assumptions about a
  // SINGLE instance's environment, not an environment tie between two
  // miter instances, so gating them by `disable iff(rst)` (skip while in
  // reset) is faithful to the reference and does NOT have the ibex
  // reset-boundary desync failure mode (there is nothing to desync here).
  reg fv_init_reg;
  always @(posedge clk) begin
    if (rst) fv_init_reg <= 1'b0;
    else if (init_cmd) fv_init_reg <= 1'b1;
  end

  always @(posedge clk) begin
    if (!rst) begin
      assume_stable_nonce_lastcycle: assume((nonce == h_nonce[1]) || valid);
      assume_stable_entropy_lastcycle: assume((entropy == h_entropy[1]) || valid);
      assume_drbg_init_and_next_not_high_same: assume(!(init_cmd && next_cmd));
      if (!fv_init_reg) assume_drbg_first_init_then_next: assume(!next_cmd);
      // NOTE: binds to the INNER hmac_core's HMAC_tag_valid/HMAC_tag, not
      // hmac_drbg's own top-level valid/drbg - confirmed via the original
      // bind: `.hmac_valid(HMAC_tag_valid), .hmac_tag(HMAC_tag)`. Without
      // this (an earlier version of this file wrongly used valid/drbg),
      // HMAC_tag - driven by the blackboxed sha512_masked_core, hence
      // otherwise free every cycle - can change between the trigger cycle
      // and the cycle K_reg/V_reg capture it, even while HMAC_tag_valid
      // stays asserted across both; confirmed via counterexample trace on
      // assert_K11_to_V1 (HMAC_tag differed 1 cycle apart while
      // HMAC_tag_valid was high throughout).
      if (HMAC_tag_valid) assume_hmac_tag_stable_when_valid: assume(HMAC_tag == h_tag[1]);
    end
  end

  // ------------------------------------------------------------
  // - Non-vacuity: reachability cover for every surviving assert's own -
  // - trigger/window condition (rule 10-equivalent)                    -
  // ------------------------------------------------------------
  always @(posedge clk) begin
    if (!rst) begin
      cover_trig_reset: cover(rst_q);
      cover_trig_Done_to_idle: cover(Done);
      cover_trig_K10_wait: cover(K10 && !hmac_tag_vld);
      cover_trig_K11_wait: cover(K11 && !hmac_tag_vld);
      cover_trig_K20_wait: cover(K20 && !hmac_tag_vld);
      cover_trig_K21_wait: cover(K21 && !hmac_tag_vld);
      cover_trig_K3_wait:  cover(K3  && !hmac_tag_vld);
      cover_trig_T_wait:   cover(T   && !hmac_tag_vld);
      cover_trig_V1_wait:  cover(V1  && !hmac_tag_vld);
      cover_trig_V2_wait:  cover(V2  && !hmac_tag_vld);
      cover_trig_V3_wait:  cover(V3  && !hmac_tag_vld);
      cover_trig_idle_wait: cover(idle && !fv_input_data_vld_sig);
      cover_trig_K10_to_K11: cover(trig_K10_to_K11);
      cover_trig_K11_to_V1:  cover(trig_K11_to_V1);
      cover_trig_K20_to_K21: cover(trig_K20_to_K21);
      cover_trig_K21_to_V2:  cover(trig_K21_to_V2);
      cover_trig_K3_to_V3:   cover(trig_K3_to_V3);
      cover_trig_T_to_Done:  cover(trig_T_to_Done);
      cover_trig_T_to_K3:    cover(trig_T_to_K3);
      cover_trig_V1_to_K20:  cover(trig_V1_to_K20);
      cover_trig_V2_to_T:    cover(trig_V2_to_T);
      cover_trig_V3_to_T:    cover(trig_V3_to_T);
      cover_trig_idle_to_K10:   cover(trig_idle_to_K10);
      cover_trig_idle_to_K10_1: cover(trig_idle_to_K10_1);
    end
  end

  // -----------------
  // - fv_coverpoints -
  // -----------------
  reg prev_next_and_ready;
  always @(posedge clk) begin
    if (!reset_n) prev_next_and_ready <= 1'b0;
    else prev_next_and_ready <= next_cmd && ready;
  end

  always @(posedge clk) begin
    if (reset_n) begin
      cover_zeroize: cover(zeroize);
      cover_zeroize_while_next: cover(zeroize && ready && next_cmd);
      cover_init_and_next_ready_low: cover((init_cmd || next_cmd) && !ready);
      if (!zeroize) cover_multiple_next: cover(prev_next_and_ready && next_cmd && ready);
    end
  end

endmodule

bind hmac_drbg hmac_checker hmac_checker_i (
  .clk(clk),
  .reset_n(reset_n),
  .zeroize(zeroize),
  .init_cmd(init_cmd),
  .next_cmd(next_cmd),
  .entropy(entropy),
  .nonce(nonce),
  .ready(ready),
  .valid(valid),
  .drbg(drbg),
  .HMAC_init(HMAC_init),
  .HMAC_next(HMAC_next),
  .HMAC_key(HMAC_key),
  .HMAC_block(HMAC_block),
  .HMAC_ready(HMAC_ready),
  .HMAC_tag_valid(HMAC_tag_valid),
  .HMAC_tag(HMAC_tag),
  .cnt_reg(cnt_reg),
  .K_reg(K_reg),
  .V_reg(V_reg),
  .drbg_st_reg(drbg_st_reg)
);
