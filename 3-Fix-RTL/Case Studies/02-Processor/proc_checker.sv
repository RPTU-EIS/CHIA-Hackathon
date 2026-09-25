// Translation of proc.tda's OneSpin TDA-format property suite into plain
// immediate assert/assume/cover inside ordinary always blocks, for sby's
// yosys+smtbmc flow (yosys has no concurrent-SVA support at all, and TDA -
// `` `begin_tda``/`` `include "tidal.sv"``/`set_freeze`/`during_o` - is a
// macro layer built on top of concurrent SVA, so it needs the same
// treatment). Bound directly into `proc` (single instance - a small,
// strictly-sequential, one-instruction-at-a-time 5-stage FSM, not
// pipelined), so all references to proc's own signals are unqualified.
//
// Every property here has a FIXED (not data-dependent) window length - 5
// cycles for the ALU/load/store ops (IF->ID->EX->MEM->WB->IF), 2 cycles
// for jump/branch (IF->ID->IF) - so unlike ibex/hmac there is no need for
// a counter/pending register: a simple N-deep delay chain of each
// property's own trigger condition tells us directly which offset (t+1,
// t+2, ...) we are at on any given cycle. `set_freeze(x, expr)` becomes a
// plain register capturing `expr` on the cycle the property file's own
// `t ##N` offset says to (almost always t+0 - the trigger cycle itself -
// except `load`'s `dataIn_t_3`, captured at t+3).

module proc_checker (
  input logic clk,
  input logic reset,
  input logic [15:0] instrIn,
  input logic [15:0] instrAddr,
  input logic [7:0] dataIn,
  input logic [7:0] dataOut,
  input logic [7:0] dataAddr,
  input logic writeEnable,
  input logic [7:0][7:0] REG_FILE,
  input logic [31:0] CONTROL_STATE
);
  parameter [2:0] c_IF = 3'b001, c_ID = 3'b010, c_EX = 3'b011,
                  c_MEM = 3'b100, c_WB = 3'b101;

  parameter [3:0] c_ALU_REG = 4'b0001;
  parameter [3:0] c_ADD_IMM = 4'b0010, c_OR_IMM = 4'b0011;
  parameter [3:0] c_LOAD = 4'b0100, c_STORE = 4'b0101;
  parameter [3:0] c_JUMP = 4'b0110, c_BRANCH = 4'b0111;
  parameter [2:0] c_ADD = 3'b001, c_OR = 3'b010;

  function automatic [7:0] read_register(logic [2:0] rs);
    read_register = (rs == 3'b000) ? 8'h0 : REG_FILE[rs];
  endfunction

  wire [2:0] state = CONTROL_STATE;

  // Force exactly one reset pulse at the very start of every BMC trace -
  // without it, this checker's own delay-chain/snapshot registers start
  // from an unconstrained (garbage) initial state, producing spurious
  // failures that have nothing to do with the design's real behavior.
  reg f_init;
  initial f_init = 1'b1;
  always @(posedge clk) begin
    if (f_init) assume(reset);
    f_init <= 1'b0;
  end

  // ---------------------------------------------------------------
  // - or_imm / add_imm (5-cycle, opcode-only trigger)              -
  // ---------------------------------------------------------------
  wire trig_or_imm  = (state == c_IF) && (instrIn[15:12] == c_OR_IMM);
  wire trig_add_imm = (state == c_IF) && (instrIn[15:12] == c_ADD_IMM);

  reg [4:0] d_or_imm, d_add_imm;
  reg [2:0] snap_rs1_ori, snap_rd_ori; reg [5:0] snap_imm_ori; reg [7:0] snap_crs1_ori;
  reg [2:0] snap_rs1_adi, snap_rd_adi; reg [5:0] snap_imm_adi; reg [7:0] snap_crs1_adi;

  always @(posedge clk) begin
    if (reset) begin
      d_or_imm <= 5'b0; d_add_imm <= 5'b0;
    end else begin
      d_or_imm  <= {d_or_imm[3:0], trig_or_imm};
      d_add_imm <= {d_add_imm[3:0], trig_add_imm};

      if (trig_or_imm) begin
        snap_rs1_ori <= instrIn[11:9]; snap_rd_ori <= instrIn[8:6];
        snap_imm_ori <= instrIn[5:0]; snap_crs1_ori <= read_register(instrIn[11:9]);
      end
      if (trig_add_imm) begin
        snap_rs1_adi <= instrIn[11:9]; snap_rd_adi <= instrIn[8:6];
        snap_imm_adi <= instrIn[5:0]; snap_crs1_adi <= read_register(instrIn[11:9]);
      end

      if (d_or_imm[0])  assert_or_imm_id:  assert(state == c_ID);
      if (d_or_imm[1])  assert_or_imm_ex:  assert(state == c_EX);
      if (d_or_imm[2])  assert_or_imm_mem: assert(state == c_MEM);
      if (d_or_imm[3])  assert_or_imm_wb:  assert(state == c_WB);
      if (d_or_imm[4]) begin
        assert_or_imm_if: assert(state == c_IF);
        assert_or_imm_result: assert((snap_rd_ori == 3'b0) ||
          (REG_FILE[snap_rd_ori] == (snap_crs1_ori | {{2{snap_imm_ori[5]}}, snap_imm_ori})));
      end

      if (d_add_imm[0]) assert_add_imm_id:  assert(state == c_ID);
      if (d_add_imm[1]) assert_add_imm_ex:  assert(state == c_EX);
      if (d_add_imm[2]) assert_add_imm_mem: assert(state == c_MEM);
      if (d_add_imm[3]) assert_add_imm_wb:  assert(state == c_WB);
      if (d_add_imm[4]) begin
        assert_add_imm_if: assert(state == c_IF);
        assert_add_imm_result: assert((snap_rd_adi == 3'b0) ||
          (REG_FILE[snap_rd_adi] == (snap_crs1_adi + {{2{snap_imm_adi[5]}}, snap_imm_adi})));
      end
    end
  end

  // ---------------------------------------------------------------
  // - or_reg / add_reg (5-cycle, opcode+ALU-op trigger)            -
  // ---------------------------------------------------------------
  wire trig_or_reg  = (state == c_IF) && (instrIn[15:12] == c_ALU_REG) && (instrIn[2:0] == c_OR);
  wire trig_add_reg = (state == c_IF) && (instrIn[15:12] == c_ALU_REG) && (instrIn[2:0] == c_ADD);

  reg [4:0] d_or_reg, d_add_reg;
  reg [2:0] snap_rd_orr; reg [7:0] snap_crs1_orr, snap_crs2_orr;
  reg [2:0] snap_rd_adr; reg [7:0] snap_crs1_adr, snap_crs2_adr;

  always @(posedge clk) begin
    if (reset) begin
      d_or_reg <= 5'b0; d_add_reg <= 5'b0;
    end else begin
      d_or_reg  <= {d_or_reg[3:0], trig_or_reg};
      d_add_reg <= {d_add_reg[3:0], trig_add_reg};

      if (trig_or_reg) begin
        snap_rd_orr <= instrIn[5:3];
        snap_crs1_orr <= read_register(instrIn[11:9]); snap_crs2_orr <= read_register(instrIn[8:6]);
      end
      if (trig_add_reg) begin
        snap_rd_adr <= instrIn[5:3];
        snap_crs1_adr <= read_register(instrIn[11:9]); snap_crs2_adr <= read_register(instrIn[8:6]);
      end

      if (d_or_reg[0])  assert_or_reg_id:  assert(state == c_ID);
      if (d_or_reg[1])  assert_or_reg_ex:  assert(state == c_EX);
      if (d_or_reg[2])  assert_or_reg_mem: assert(state == c_MEM);
      if (d_or_reg[3])  assert_or_reg_wb:  assert(state == c_WB);
      if (d_or_reg[4]) begin
        assert_or_reg_if: assert(state == c_IF);
        assert_or_reg_result: assert((snap_rd_orr == 3'b0) ||
          (REG_FILE[snap_rd_orr] == (snap_crs1_orr | snap_crs2_orr)));
      end

      if (d_add_reg[0]) assert_add_reg_id:  assert(state == c_ID);
      if (d_add_reg[1]) assert_add_reg_ex:  assert(state == c_EX);
      if (d_add_reg[2]) assert_add_reg_mem: assert(state == c_MEM);
      if (d_add_reg[3]) assert_add_reg_wb:  assert(state == c_WB);
      if (d_add_reg[4]) begin
        assert_add_reg_if: assert(state == c_IF);
        assert_add_reg_result: assert((snap_rd_adr == 3'b0) ||
          (REG_FILE[snap_rd_adr] == (snap_crs1_adr + snap_crs2_adr)));
      end
    end
  end

  // ---------------------------------------------------------------
  // - load (5-cycle; dataIn frozen at t+3, not t+0)                -
  // ---------------------------------------------------------------
  wire trig_load = (state == c_IF) && (instrIn[15:12] == c_LOAD);
  reg [4:0] d_load;
  reg [2:0] snap_rd_ld; reg [5:0] snap_imm_ld; reg [7:0] snap_crs1_ld; reg [7:0] snap_dataIn_ld;

  always @(posedge clk) begin
    if (reset) begin
      d_load <= 5'b0;
    end else begin
      d_load <= {d_load[3:0], trig_load};

      if (trig_load) begin
        snap_rd_ld <= instrIn[8:6]; snap_imm_ld <= instrIn[5:0];
        snap_crs1_ld <= read_register(instrIn[11:9]);
      end
      if (d_load[2]) snap_dataIn_ld <= dataIn; // captured at t+3 (d_load[2] true means we are AT t+3)

      if (d_load[0]) begin
        assert_load_id: assert(state == c_ID);
        assert_load_we1: assert(writeEnable == 1'b0);
      end
      if (d_load[1]) begin
        assert_load_ex: assert(state == c_EX);
        assert_load_we2: assert(writeEnable == 1'b0);
      end
      if (d_load[2]) begin
        assert_load_mem: assert(state == c_MEM);
        assert_load_addr: assert(dataAddr == (snap_crs1_ld + {{2{snap_imm_ld[5]}}, snap_imm_ld}));
        assert_load_we3: assert(writeEnable == 1'b0);
      end
      if (d_load[3]) begin
        assert_load_wb: assert(state == c_WB);
        assert_load_we4: assert(writeEnable == 1'b0);
      end
      if (d_load[4]) begin
        assert_load_if: assert(state == c_IF);
        assert_load_result: assert((snap_rd_ld == 3'b0) || (REG_FILE[snap_rd_ld] == snap_dataIn_ld));
        assert_load_we5: assert(writeEnable == 1'b0);
      end
    end
  end

  // ---------------------------------------------------------------
  // - store (5-cycle)                                              -
  // ---------------------------------------------------------------
  wire trig_store = (state == c_IF) && (instrIn[15:12] == c_STORE);
  reg [4:0] d_store;
  reg [5:0] snap_imm_st; reg [7:0] snap_crs1_st, snap_crs2_st;

  always @(posedge clk) begin
    if (reset) begin
      d_store <= 5'b0;
    end else begin
      d_store <= {d_store[3:0], trig_store};

      if (trig_store) begin
        snap_imm_st <= instrIn[5:0];
        snap_crs1_st <= read_register(instrIn[11:9]); snap_crs2_st <= read_register(instrIn[8:6]);
      end

      if (d_store[0]) begin
        assert_store_id: assert(state == c_ID);
        assert_store_we1: assert(writeEnable == 1'b0);
      end
      if (d_store[1]) begin
        assert_store_ex: assert(state == c_EX);
        assert_store_we2: assert(writeEnable == 1'b0);
      end
      if (d_store[2]) begin
        assert_store_mem: assert(state == c_MEM);
        assert_store_addr: assert(dataAddr == (snap_crs1_st + {{2{snap_imm_st[5]}}, snap_imm_st}));
        assert_store_dout: assert(dataOut == snap_crs2_st);
        assert_store_we3: assert(writeEnable == 1'b1);
      end
      if (d_store[3]) begin
        assert_store_wb: assert(state == c_WB);
        assert_store_we4: assert(writeEnable == 1'b0);
      end
      if (d_store[4]) begin
        assert_store_if: assert(state == c_IF);
        assert_store_we5: assert(writeEnable == 1'b0);
      end
    end
  end

  // ---------------------------------------------------------------
  // - jump (2-cycle: IF -> ID -> IF)                                -
  // ---------------------------------------------------------------
  wire trig_jump = (state == c_IF) && (instrIn[15:12] == c_JUMP);
  reg [1:0] d_jump;
  reg [15:0] snap_addr_jmp, snap_instr_jmp;

  always @(posedge clk) begin
    if (reset) begin
      d_jump <= 2'b0;
    end else begin
      d_jump <= {d_jump[0], trig_jump};

      if (trig_jump) begin
        snap_addr_jmp <= instrAddr; snap_instr_jmp <= instrIn;
      end

      if (d_jump[0]) assert_jump_id: assert(state == c_ID);
      if (d_jump[1]) begin
        assert_jump_if: assert(state == c_IF);
        assert_jump_target: assert(instrAddr == (snap_addr_jmp + 16'd2 +
          {{4{snap_instr_jmp[11]}}, snap_instr_jmp[11:0]}));
      end
    end
  end

  // ---------------------------------------------------------------
  // - branch (2-cycle: IF -> ID -> IF)                              -
  // ---------------------------------------------------------------
  wire trig_branch = (state == c_IF) && (instrIn[15:12] == c_BRANCH);
  reg [1:0] d_branch;
  reg [15:0] snap_addr_br, snap_instr_br; reg [7:0] snap_crs1_br;

  always @(posedge clk) begin
    if (reset) begin
      d_branch <= 2'b0;
    end else begin
      d_branch <= {d_branch[0], trig_branch};

      if (trig_branch) begin
        snap_addr_br <= instrAddr; snap_instr_br <= instrIn;
        snap_crs1_br <= read_register(instrIn[11:9]);
      end

      if (d_branch[0]) assert_branch_id: assert(state == c_ID);
      if (d_branch[1]) begin
        assert_branch_if: assert(state == c_IF);
        assert_branch_target: assert(
          (snap_crs1_br == 8'h00)
            ? (instrAddr == (snap_addr_br + 16'd2 + {{7{snap_instr_br[8]}}, snap_instr_br[8:0]}))
            : (instrAddr == (snap_addr_br + 16'd2)));
      end
    end
  end

  // ---------------------------------------------------------------
  // - Non-vacuity: reachability cover for every trigger             -
  // ---------------------------------------------------------------
  always @(posedge clk) begin
    if (!reset) begin
      cover_trig_or_imm:  cover(trig_or_imm);
      cover_trig_add_imm: cover(trig_add_imm);
      cover_trig_or_reg:  cover(trig_or_reg);
      cover_trig_add_reg: cover(trig_add_reg);
      cover_trig_load:    cover(trig_load);
      cover_trig_store:   cover(trig_store);
      cover_trig_jump:    cover(trig_jump);
      cover_trig_branch:  cover(trig_branch);
    end
  end

endmodule

bind proc proc_checker proc_checker_i (
  .clk(clk),
  .reset(reset),
  .instrIn(instrIn),
  .instrAddr(instrAddr),
  .dataIn(dataIn),
  .dataOut(dataOut),
  .dataAddr(dataAddr),
  .writeEnable(writeEnable),
  .REG_FILE(REG_FILE),
  .CONTROL_STATE(CONTROL_STATE)
);
