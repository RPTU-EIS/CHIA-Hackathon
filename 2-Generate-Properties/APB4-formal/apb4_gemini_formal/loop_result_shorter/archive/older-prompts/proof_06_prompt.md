DESIGN AND OBJECTIVE (shared by every stage)
Verify this APB4 master/slave system with meaningful properties. Discover and
report RTL bugs, but never modify RTL. Correct faulty formal properties when
needed. A green result alone is not the goal. The supplied RTL is read-only
implementation to inspect, not a specification that must be true.

Interface and intended behavior:
- The external S* inputs request a transfer. APB phases are IDLE
  (PSEL=0, PENABLE=0), one SETUP cycle (1,0), then ACCESS (1,1). Bus control
  and write data remain stable through completion; transfer may request a
  back-to-back transfer.
- PRESETn is active-low. The master resets to IDLE; the slave resets PRDATA
  and PSLVERR, but its memory is uninitialized.
- The 1024-word slave has no wait states: PREADY=PSEL&&PENABLE. Constrain valid
  addresses to 0..1023 and check reads/byte writes with an independent model.
- A legal read has PWRITE=0, PSTRB=0000, an address in 0..1023, one SETUP
  cycle followed by ACCESS, and completes when PREADY=1. Check read data only
  after known writes to that address. A read with nonzero PSTRB is invalid:
  check its error response separately and do not require valid PRDATA from it.
- Treat writes during SETUP as RTL behavior to evaluate, not an assumed
  protocol fact. Do not assume away behavior you cover.
- Instantiate master and slave directly with explicit APB wires because the
  wrapper exposes only PRDATA.

Explain any assumed requester contract on transfer and S* inputs. Assumptions
may constrain the environment, never force DUT outputs to satisfy assertions.
Check protocol sequencing, reset, stability, completion, errors, and data
integrity. Distinguish intended requirements from suspicious RTL behavior.
Idle requires PSEL=PENABLE=0, not zero address/data/control outputs.


STAGE 3: DIAGNOSE BMC, REPAIR PROPERTIES, OR REPORT RTL BUGS
(these response rules override the shared output format)
Cover passed, but BMC did not. Coverage does not establish property correctness.
The prior property review is fallible; recheck the failing property against
the actual counterexample before deciding to modify the RTL.
1. Classify the log as setup ERROR, timeout/UNKNOWN, or assertion FAIL. The
   BMC engine uses --keep-going, so diagnose EVERY reported failed assertion,
   grouping failures with the same root cause. Quote each assertion and step.
   Evaluate its timing, reset,
   assumptions, and DUT connections against the current RTL. Use only supplied
   evidence; if waveform values are absent, do not invent a counterexample.
   Diagnose the assertion named in the tool log first. Comments saying a
   different check is 'expected to fail' are not failure evidence. For early
   failures, check whether the antecedent incorrectly includes a reset cycle.
2. Decide which outcome is justified:
   - Setup/property error: correct the SBY/harness and explain why the old
     check misrepresented the requirement. Preserve the intended requirement.
   - RTL bug: preserve the valid assertion and report the broken requirement,
     failing assertion, counterexample step, faulty RTL location, and smallest
     suggested repair in prose. Do not return or modify any RTL file.
   - Inconclusive: explain the missing evidence; do not suppress the failure.
3. Never weaken assumptions or assertions to make buggy RTL pass.

The first line must contain exactly one of:
PROPERTY_FIX  when only the harness/property is changed; return no RTL files.
BUG_REPORT    when valid properties expose one or more RTL bugs. Report every
              evidenced bug found by this BMC run and return no files.
PROOF_BLOCKED when the evidence cannot justify either repair; explain why.
For PROPERTY_FIX, return the complete corrected SBY. For BUG_REPORT and
PROOF_BLOCKED, return analysis only. Python reruns validation only after a
PROPERTY_FIX. The RTL remains exactly as supplied.

Already reported failures (do not report these again):


Current apb4.sby:
[tasks]
prep
bmc
cover

[options]
prep:
mode prep
--
bmc:
mode bmc
depth 20
--
cover:
mode cover
depth 20
--

[engines]
prep: smtbmc
cover: smtbmc
bmc: smtbmc --keep-going

[script]
read_verilog -formal -sv -noautowire APB_Master.v APB_Slave.v APB_Wrapper.v apb4_formal.sv
prep -top apb4_formal
check -assert

[files]
APB_Master.v
APB_Slave.v
APB_Wrapper.v

[file apb4_formal.sv]
module apb4_formal (
    input wire PCLK
);
    // DUT Inputs from the external system
    (* anyseq *) reg SWRITE;
    (* anyseq *) reg [31:0] SADDR;
    (* anyseq *) reg [31:0] SWDATA;
    (* anyseq *) reg [3:0] SSTRB;
    (* anyseq *) reg [2:0] SPROT;
    (* anyseq *) reg transfer;

    // DUT inputs for clock and reset
    reg PRESETn;

    // APB interface wires
    wire PSEL;
    wire PENABLE;
    wire PWRITE;
    wire [31:0] PADDR;
    wire [31:0] PWDATA;
    wire [3:0] PSTRB;
    wire [2:0] PPROT;
    wire PREADY;
    wire PSLVERR;
    wire [31:0] PRDATA;

    // Instantiate Master and Slave directly
    APB_Master master (
        .PCLK(PCLK),
        .PRESETn(PRESETn),
        .SWRITE(SWRITE),
        .SADDR(SADDR),
        .SWDATA(SWDATA),
        .SSTRB(SSTRB),
        .SPROT(SPROT),
        .transfer(transfer),
        .PSEL(PSEL),
        .PENABLE(PENABLE),
        .PWRITE(PWRITE),
        .PADDR(PADDR),
        .PWDATA(PWDATA),
        .PSTRB(PSTRB),
        .PPROT(PPROT),
        .PREADY(PREADY),
        .PSLVERR(PSLVERR)
    );

    APB_Slave slave (
        .PCLK(PCLK),
        .PRESETn(PRESETn),
        .PSEL(PSEL),
        .PENABLE(PENABLE),
        .PWRITE(PWRITE),
        .PADDR(PADDR),
        .PWDATA(PWDATA),
        .PSTRB(PSTRB),
        .PPROT(PPROT),
        .PREADY(PREADY),
        .PSLVERR(PSLVERR),
        .PRDATA(PRDATA)
    );

    // --- Helper signals for protocol phases ---
    wire SETUP_PHASE = PSEL && !PENABLE;
    wire ACCESS_PHASE = PSEL && PENABLE;
    wire XFER_DONE = ACCESS_PHASE && PREADY;

    // Formal verification logic
    reg first_cycle = 1;
    reg past_valid = 0;

    initial begin
        first_cycle = 1;
        past_valid = 0;
    end

    always @(posedge PCLK) begin
        if (first_cycle) begin
            PRESETn <= 0;
            first_cycle <= 0;
        end else begin
            PRESETn <= 1;
            past_valid <= 1;
        end
    end

    // --- Assumptions (Environment Constraints) ---
    // Assumed requester contract: The system driving the master provides inputs
    // that are stable when a transfer is initiated. When `transfer` is asserted,
    // the associated address in `SADDR` is assumed to be within the valid 4KB
    // range of the 1024-word slave memory. Other S* inputs are unconstrained.
    always @(posedge PCLK) begin
        // a_valid_addr: Constrain SADDR for transfers to be in the slave's 4KB range.
        if (transfer) begin
            assume(SADDR[31:12] == 0);
        end
    end

    // --- Data Integrity Checker ---
    (* anyconst *) reg [9:0] watched_addr;
    reg [31:0] expected_data;
    reg [3:0] byte_valid;

    initial begin
        byte_valid = 4'b0000;
    end

    always @(posedge PCLK) begin
        if (!PRESETn) begin
            byte_valid <= 4'b0000;
        end else begin
            // Model a correct byte-wise write to our scoreboard.
            // This happens on the cycle AFTER the write completes.
            if ($past(XFER_DONE && PWRITE && PADDR[11:2] == watched_addr)) begin
                if ($past(PSTRB[0])) begin
                    expected_data[7:0] <= $past(PWDATA[7:0]);
                    byte_valid[0] <= 1'b1;
                end
                if ($past(PSTRB[1])) begin
                    expected_data[15:8] <= $past(PWDATA[15:8]);
                    byte_valid[1] <= 1'b1;
                end
                if ($past(PSTRB[2])) begin
                    expected_data[23:16] <= $past(PWDATA[23:16]);
                    byte_valid[2] <= 1'b1;
                end
                if ($past(PSTRB[3])) begin
                    expected_data[31:24] <= $past(PWDATA[31:24]);
                    byte_valid[3] <= 1'b1;
                end
            end

            // Check readback on a completed legal read transaction.
            // PRDATA is registered, so it is valid on the cycle after the read completes.
            if ($past(XFER_DONE && !PWRITE && PSTRB == 4'b0 && PADDR[11:2] == watched_addr)) begin
                // assert_data_integrity_byte0: Check byte 0 if it has been written
                if ($past(byte_valid[0])) begin
                    assert(PRDATA[7:0] == $past(expected_data[7:0]));
                end
                // assert_data_integrity_byte1: Check byte 1 if it has been written
                if ($past(byte_valid[1])) begin
                    assert(PRDATA[15:8] == $past(expected_data[15:8]));
                end
                // assert_data_integrity_byte2: Check byte 2 if it has been written
                if ($past(byte_valid[2])) begin
                    assert(PRDATA[23:16] == $past(expected_data[23:16]));
                end
                // assert_data_integrity_byte3: Check byte 3 if it has been written
                if ($past(byte_valid[3])) begin
                    assert(PRDATA[31:24] == $past(expected_data[31:24]));
                end
            end
        end
    end

    // --- Helper state for cover properties ---
    reg saw_full_write_to_watched_addr;
    initial begin
        saw_full_write_to_watched_addr = 0;
    end

    // --- Assertions and Covers ---
    always @(posedge PCLK) begin
        // --- Reset Checks ---
        // During the reset cycle (PRESETn is low)
        if (!PRESETn) begin
            // assert_master_reset_outputs: On reset, master control signals are low
            assert(PSEL == 0);
            assert(PENABLE == 0);
            saw_full_write_to_watched_addr <= 0;
            // cover_reset_active: Cover the cycle where reset is active.
            cover(1);
        end

        // On the first clock edge after reset is de-asserted.
        if (past_valid && PRESETn && !$past(PRESETn)) begin
            // assert_slave_reset_outputs: Slave registered outputs are zero after reset.
            assert(PRDATA == 0);
            assert(PSLVERR == 0);
            // cover_reset_release: Cover the first cycle after reset de-assertion.
            cover(1);
        end
        
        if (past_valid && PRESETn) begin
            // --- Protocol Checks ---
            // assert_penable_in_access: PENABLE should only be high when PSEL is also high
            if (PENABLE) begin
                assert(PSEL);
            end

            // assert_addr_ctrl_stable: Address and control must be stable from SETUP to end of ACCESS
            if ($past(SETUP_PHASE) && ACCESS_PHASE) begin
                assert($stable(PADDR));
                assert($stable(PWRITE));
                assert($stable(PPROT));
                assert($stable(PSTRB));
                assert($stable(PWDATA));
            end

            // --- Slave Behavior Checks ---
            // assert_pready: Slave must assert PREADY in ACCESS phase (no wait states)
            if (ACCESS_PHASE) begin
                assert(PREADY);
            end

            // assert_read_error_on_access: A read with non-zero PSTRB in ACCESS must cause PSLVERR
            if ($past(ACCESS_PHASE && !PWRITE && PSTRB != 4'b0)) begin
                assert(PSLVERR);
            end

            // assert_no_read_error_on_setup: A read with non-zero PSTRB in SETUP must NOT cause PSLVERR
            // The slave error logic is only gated by PSEL, which is a bug. This assertion should fail.
            if ($past(SETUP_PHASE && !PWRITE && PSTRB != 4'b0)) begin
                 assert(!PSLVERR);
            end

            // --- Cover Properties ---
            if (SETUP_PHASE) begin
                // cover_setup: Cover the SETUP phase
                cover(1);
            end
            if (ACCESS_PHASE) begin
                // cover_access: Cover the ACCESS phase
                cover(1);
            end
            if ($past(XFER_DONE && PWRITE)) begin
                // cover_write_complete: Cover a completed write transaction
                cover(1);
            end
            if ($past(XFER_DONE && !PWRITE)) begin
                // cover_read_complete: Cover a completed read transaction
                cover(1);
            end
            if ($past(XFER_DONE) && SETUP_PHASE) begin
                // cover_back_to_back: Cover a back-to-back transfer
                cover(1);
            end
            if (ACCESS_PHASE && !PWRITE && PSTRB != 4'b0) begin
                // cover_read_error: Cover the trigger for a read error
                cover(1);
            end
            if ($past(XFER_DONE && PWRITE && PADDR[11:2] == watched_addr)) begin
                // cover_data_write: Cover a write to the watched address
                cover(1);
            end
            if ($past(XFER_DONE && !PWRITE && PADDR[11:2] == watched_addr) && |$past(byte_valid)) begin
                // cover_data_read: Cover a read of known data from the watched address
                cover(1);
            end

            // cover_partial_overwrite: Cover a partial write after a full write to the same address.
            // A partial write to the watched address occurred, and we have previously seen a full write.
            if ($past(XFER_DONE && PWRITE && PSTRB != 4'b1111 && PSTRB != 4'b0000 && PADDR[11:2] == watched_addr) && saw_full_write_to_watched_addr) begin
                cover(1);
                saw_full_write_to_watched_addr <= 0;
            // A full word write to the watched address occurred. Set the flag.
            end else if ($past(XFER_DONE && PWRITE && PSTRB == 4'b1111 && PADDR[11:2] == watched_addr)) begin
                saw_full_write_to_watched_addr <= 1;
            end
        end
    end
endmodule


BMC log:
Key diagnostics:
SBY  3:05:11 [apb4_bmc] engine_0: ##   0:00:00  BMC failed!
SBY  3:05:11 [apb4_bmc] engine_0: ##   0:00:00  Assert failed in apb4_formal: apb4_formal.sv:150.21-150.73 (_witness_.check_assert_apb4_formal_sv_150_342)
SBY  3:05:11 [apb4_bmc] engine_0: ##   0:00:00  Assert failed in apb4_formal: apb4_formal.sv:146.21-146.73 (_witness_.check_assert_apb4_formal_sv_146_340)
SBY  3:05:11 [apb4_bmc] engine_0: ##   0:00:00  Assert failed in apb4_formal: apb4_formal.sv:138.21-138.69 (_witness_.check_assert_apb4_formal_sv_138_336)
SBY  3:05:11 [apb4_bmc] engine_0: ##   0:00:00  Assert failed in apb4_formal: apb4_formal.sv:142.21-142.71 (_witness_.check_assert_apb4_formal_sv_142_338)
SBY  3:05:12 [apb4_bmc] engine_0: ##   0:00:00  Assert failed in apb4_formal: apb4_formal.sv:214.18-214.34 (_witness_.check_assert_apb4_formal_sv_214_404)
SBY  3:05:41 [apb4_bmc] engine_0: ##   0:00:29  Status: failed
SBY  3:05:41 [apb4_bmc] engine_0: Status returned by engine: FAIL
SBY  3:05:41 [apb4_bmc] summary: engine_0 (smtbmc --keep-going) returned FAIL
SBY  3:05:41 [apb4_bmc] summary:   failed assertion apb4_formal._witness_.check_assert_apb4_formal_sv_150_342 at apb4_formal.sv:150.21-150.73 step 1
SBY  3:05:41 [apb4_bmc] summary:   failed assertion apb4_formal._witness_.check_assert_apb4_formal_sv_146_340 at apb4_formal.sv:146.21-146.73 step 1
SBY  3:05:41 [apb4_bmc] summary:   failed assertion apb4_formal._witness_.check_assert_apb4_formal_sv_138_336 at apb4_formal.sv:138.21-138.69 step 1
SBY  3:05:41 [apb4_bmc] summary:   failed assertion apb4_formal._witness_.check_assert_apb4_formal_sv_142_338 at apb4_formal.sv:142.21-142.71 step 1
SBY  3:05:41 [apb4_bmc] summary:   failed assertion apb4_formal._witness_.check_assert_apb4_formal_sv_214_404 at apb4_formal.sv:214.18-214.34 step 5
SBY  3:05:41 [apb4_bmc] DONE (FAIL, rc=2)
SBY  3:05:41 The following tasks failed: ['bmc']
Log tail:
ng for obvious problems).
Checking module APB_Master...
Checking module APB_Slave...
Checking module apb4_formal...
Found and reported 0 problems.

10. Executing SETUNDEF pass (replace undef values with defined constants).

11. Executing OPT pass (performing simple optimizations).

11.1. Executing OPT_EXPR pass (perform const folding).
Optimizing module APB_Master.
Optimizing module APB_Slave.
Optimizing module apb4_formal.

11.2. Executing OPT_MERGE pass (detect identical cells).
Finding identical cells in module `\APB_Master'.
Computing hashes of 43 cells of `\APB_Master'.
Finding duplicate cells in `\APB_Master'.
Finding identical cells in module `\APB_Slave'.
Computing hashes of 342 cells of `\APB_Slave'.
Finding duplicate cells in `\APB_Slave'.
Finding identical cells in module `\apb4_formal'.
Computing hashes of 180 cells of `\apb4_formal'.
Finding duplicate cells in `\apb4_formal'.
Computing hashes of 174 cells of `\apb4_formal'.
Finding duplicate cells in `\apb4_formal'.
<suppressed ~18 debug messages>
Removed a total of 6 cells.

11.3. Executing OPT_DFF pass (perform DFF optimizations).

11.4. Executing OPT_CLEAN pass (remove unused cells and wires).
Finding unused cells or wires in module \APB_Master..
Finding unused cells or wires in module \APB_Slave..
Finding unused cells or wires in module \apb4_formal..
Removed 0 unused cells and 6 unused wires.
<suppressed ~1 debug messages>

11.5. Finished fast OPT passes.

12. Executing OPT_CLEAN pass (remove unused cells and wires).
Finding unused cells or wires in module \APB_Master..
Finding unused cells or wires in module \APB_Slave..
Finding unused cells or wires in module \apb4_formal..
Removed 0 unused cells and 159 unused wires.
<suppressed ~3 debug messages>

13. Executing RTLIL backend.
Output filename: ../model/design_prep.il

End of script. Logfile hash: 26b557fd58, time: 0.05s, user: 0.06s, system: 0.01s, MEM: 21.06 MB peak
Yosys 0.68+130 (git sha1 dd83bbad2-dirty, Release, Clang /usr/bin/clang++ 21.1.8)
Time spent: 45% 4x opt_clean (0 sec), 11% 1x check (0 sec), ...

=== model/design_smt2.log ===

 /----------------------------------------------------------------------------\
 |  yosys -- Yosys Open SYnthesis Suite                                       |
 |  Copyright (C) 2012 - 2026  Claire Xenia Wolf <claire@yosyshq.com>         |
 |  Distributed under an ISC-like license, type "license" to see terms        |
 \----------------------------------------------------------------------------/
 Yosys 0.68+130 (git sha1 dd83bbad2-dirty, Release, Clang /usr/bin/clang++ 21.1.8)

-- Executing script file `design_smt2.ys' --

1. Executing RTLIL frontend.
Input filename: design_prep.il

2. Executing HIERARCHY pass (managing design hierarchy).
Attribute `top' found on module `apb4_formal'. Setting top module to apb4_formal.

2.1. Analyzing design hierarchy..
Top module:  \apb4_formal
Used module:     \APB_Master
Used module:     \APB_Slave

2.2. Analyzing design hierarchy..
Top module:  \apb4_formal
Used module:     \APB_Master
Used module:     \APB_Slave
Removed 0 unused modules.
Module apb4_formal directly or indirectly contains formal properties -> setting "keep" attribute.

3. Executing FORMALFF pass.

4. Executing DFFUNMAP pass (unmap clock enable and synchronous reset from FFs).

5. Printing statistics.

=== APB_Master ===

        +----------Local Count, excluding submodules.
        | 
       54 wires
      328 wire bits
       27 public wires
      231 public wire bits
       17 ports
      151 port bits
       45 cells
        3   $and
        8   $anyinit
        1   $assume
        2   $eq
        2   $logic_and
        2   $logic_not
       21   $mux
        4   $not
        1   $pmux
        1   $reduce_or

=== APB_Slave ===

        +----------Local Count, excluding submodules.
        | 
      352 wires
     7461 wire bits
      136 public wires
     2670 public wire bits
       12 ports
      110 port bits
      344 cells
        2   $anyinit
      124   $anyseq
        1   $assume
       15   $eq
        1   $logic_and
        1   $mem_v2
      197   $mux
        1   $not
        1   $reduce_bool
        1   $reduce_or

=== apb4_formal ===

        +----------Local Count, excluding submodules.
        | 
      160 wires
      581 wire bits
       53 public wires
      384 public wire bits
        1 ports
        1 port bits
      174 cells
        1   $anyconst
       29   $anyinit
        6   $anyseq
       17   $assert
        2   $assume
       10   $eq
       31   $ff
       15   $logic_and
        7   $logic_not
       51   $mux
        4   $not
        1   $reduce_bool
        2 submodules
        1   APB_Master
        1   APB_Slave

=== design hierarchy ===

        +----------Count including submodules.
        | 
      563 apb4_formal
       45 APB_Master
      344 APB_Slave

        +----------Count including submodules.
        | 
      566 wires
     8370 wire bits
      216 public wires
     3285 public wire bits
       30 ports
      262 port bits
        - memories
        - memory bits
        - processes
      563 cells
        3   $and
        1   $anyconst
       39   $anyinit
      130   $anyseq
       17   $assert
        4   $assume
       27   $eq
       31   $ff
       18   $logic_and
        9   $logic_not
        1   $mem_v2
      269   $mux
        9   $not
        1   $pmux
        2   $reduce_bool
        2   $reduce_or
        2 submodules
        1   APB_Master
        1   APB_Slave

6. Executing SMT2 backend.

6.1. Executing BMUXMAP pass.

6.2. Executing DEMUXMAP pass.
Creating SMT-LIBv2 representation of module APB_Slave.
Creating SMT-LIBv2 representation of module APB_Master.
Creating SMT-LIBv2 representation of module apb4_formal.

End of script. Logfile hash: d4cdade530, time: 0.02s, user: 0.02s, system: 0.01s, MEM: 18.62 MB peak
Yosys 0.68+130 (git sha1 dd83bbad2-dirty, Release, Clang /usr/bin/clang++ 21.1.8)
Time spent: 75% 2x write_smt2 (0 sec), 16% 2x read_rtlil (0 sec), ...


Current RTL:
=== APB_Master.v ===
module APB_Master (
    //the followin signals are from the External System 
    //the system signals names will begin with letter S
    //note : we will act as the external system in the testbench
    input SWRITE ,
    input [31:0] SADDR , SWDATA , 
    input [3:0] SSTRB ,
    input [2:0] SPROT  ,
    input transfer ,   //to indicate the begenning of the transfer

    //the followin signals are Mater signals
    output reg PSEL , PENABLE , PWRITE ,
    output reg [31:0] PADDR , PWDATA ,
    output reg [3:0] PSTRB ,
    output reg [2:0] PPROT ,
    input PCLK , PRESETn ,
    input  PREADY ,
    input PSLVERR
);
//defining our states
localparam  IDLE = 2'b00,
            SETUP = 2'b01,
            ACCESS = 2'b10;
(* fsm_encoding = "one_hot" *)
reg [1:0] ns , cs ; //next state , current state

//state memory 
always @(posedge PCLK , negedge PRESETn)
begin
    if(~PRESETn) 
        cs <= IDLE;
    else 
        cs <= ns ;
end

//next state logic
always @(*) begin
    case(cs)
        IDLE : begin
            if(transfer)
                ns = SETUP;
            else
                ns = IDLE;
        end
        SETUP : ns = ACCESS ; //The bus only remains in the SETUP state for one clock cycle and always moves to the ACCESS state on the next rising edge of the clock
        ACCESS : begin
            if(PREADY && !transfer)
                ns = IDLE ;
            else if(PREADY && transfer)
                ns = SETUP ;
            else
                ns = ACCESS ;
        end
        default : ns = IDLE;
    endcase
end

//output logic
always @(*) begin
    if(~PRESETn)
        begin
            PSEL = 0;
            PENABLE = 0;
            PWRITE = 0;
            PADDR = 0;
            PWDATA = 0;
            PSTRB = 0;
            PPROT = 0;
        end
    else begin
        case(cs)
            IDLE : begin
                PSEL = 0;
                PENABLE = 0;
            end
            SETUP : begin
                PSEL = 1;
                PENABLE = 0;   //signals are sent to slave in setup state
                PWRITE = SWRITE ;
                PADDR = SADDR ;
                PWDATA = SWDATA ;
                PSTRB = SSTRB ;
                PPROT = SPROT ;
            end
            ACCESS : begin
                PSEL = 1;
                PENABLE = 1;
            end
        endcase
    end
end
endmodule

=== APB_Slave.v ===
module APB_Slave  #(
    parameter MEM_WIDTH = 32 , parameter MEM_DEPTH = 1024
) (
    input PSEL , PENABLE , PWRITE ,  
    input [31:0] PADDR , PWDATA ,
    input [3:0] PSTRB ,
    input [2:0] PPROT ,
    input PCLK , PRESETn ,
    output reg [31:0] PRDATA ,
    output PREADY ,
    output reg PSLVERR
);
//we will pretend that the slave has a Cashe memory inside it,
//we did this to test the read and write functionality
//defining our memory
reg [MEM_WIDTH-1:0] Cache [MEM_DEPTH-1:0];

always @ (posedge PCLK) begin
    if (~PRESETn) begin
        PSLVERR <= 0;
        PRDATA <= 0;
    end
    else if (PSEL) begin  //here we have just one slave so the PSEL signal is one bit, PSEL only as we will go to the ACCESS state anyway
        //writing stage
        if (PWRITE) begin     
            case (PSTRB)
                4'b0001: Cache[PADDR] <= {{24{PWDATA[7]}}, PWDATA[7:0]}; // Store the least significant byte (sb)
                4'b0010: Cache[PADDR] <= {{24{PWDATA[15]}}, PWDATA[15:8], 8'h00}; // Store the second byte with zeroes in the least 8 bits
                4'b0011: Cache[PADDR] <= {{16{PWDATA[15]}}, PWDATA[15:0]}; // Store the least significant half-word (sh)
                4'b0100: Cache[PADDR] <= {{24{PWDATA[23]}}, PWDATA[23:16], 8'h00}; // Store the third byte with zeroes in the least 8 bits
                4'b0101: Cache[PADDR] <= {{16{PWDATA[23]}}, PWDATA[23:16], 8'h00, PWDATA[7:0]}; // Store the third and least significant bytes with zeroes
                4'b0110: Cache[PADDR] <= {{8{PWDATA[23]}}, PWDATA[23:8], 8'h00}; // Store the second and third bytes with zeroes in the least 8 bits
                4'b0111: Cache[PADDR] <= {{8{PWDATA[23]}}, PWDATA[23:0]}; // Store the least significant three bytes (sh)
                4'b1000: Cache[PADDR] <= {PWDATA[31:24], 24'h000000}; // Store the most significant byte without sign extension
                4'b1001: Cache[PADDR] <= {PWDATA[31:24], 16'h0000, PWDATA[7:0]}; // Store the most and least significant bytes without sign extension
                4'b1010: Cache[PADDR] <= {PWDATA[31:23], 8'h00, PWDATA[15:8], 8'h00}; // Store the most significant half-word with zeroes in the least significant byte if PSTRB[0] == 0
                4'b1011: Cache[PADDR] <= {PWDATA[31:23], 8'h00, PWDATA[15:0]}; // Store the most significant half-word and the least significant byte, zeroing the middle byte if necessary
                4'b1100: Cache[PADDR] <= {PWDATA[31:16], 16'h0000}; // Store the most significant and second bytes without sign extension
                4'b1101: Cache[PADDR] <= {PWDATA[31:16], 8'h00, PWDATA[7:0]}; // Store the most significant three bytes with zeroes in the least 8 bits
                4'b1110: Cache[PADDR] <= {PWDATA[31:8], 8'h00}; // Store the most significant three bytes with zeroes in the least 8 bits
                4'b1111: Cache[PADDR] <= PWDATA[31:0]; // Store the full word without sign extension
                default: Cache[PADDR] <= 32'h00000000; // Default case to handle invalid PSTRB values
            endcase
            PSLVERR <= 0;
        end
        //reading stage
        else begin
            if (PSTRB != 0)
                PSLVERR <= 1;  //PSTRB must remain low when reading
            else begin
                PRDATA <= Cache[PADDR];
                PSLVERR <= 0;
            end
        end
    end
end
assign PREADY = (PSEL && PENABLE) ? 1 : 0; 
endmodule

=== APB_Wrapper.v ===
module APB_Wrapper (
    input PCLK , PRESETn ,
    input SWRITE ,
    input [31:0] SADDR , SWDATA , 
    input [3:0] SSTRB ,
    input [2:0] SPROT  ,
    input transfer ,
    output [31:0] PRDATA 
);
wire PSEL , PENABLE , PWRITE ;
wire [31:0] PADDR , PWDATA ;
wire [3:0] PSTRB ;
wire [2:0] PPROT ;
wire PREADY , PSLVERR ;

//instantiating our master
APB_Master Master (
    .PCLK (PCLK),
    .PRESETn(PRESETn),
    .SWRITE(SWRITE),
    .SADDR(SADDR),
    .SWDATA(SWDATA),
    .SSTRB(SSTRB),
    .SPROT(SPROT),
    .transfer(transfer),
    .PSEL(PSEL),
    .PENABLE(PENABLE),
    .PWRITE(PWRITE),
    .PADDR(PADDR),
    .PWDATA(PWDATA),
    .PSTRB(PSTRB),
    .PPROT(PPROT),
    .PREADY(PREADY),
    .PSLVERR(PSLVERR)
);

//instantiating our slave
APB_Slave Slave (
    .PCLK(PCLK),
    .PRESETn(PRESETn),
    .PSEL(PSEL),
    .PENABLE(PENABLE),
    .PWRITE(PWRITE),
    .PADDR(PADDR),
    .PWDATA(PWDATA),
    .PSTRB(PSTRB),
    .PPROT(PPROT),
    .PREADY(PREADY),
    .PSLVERR(PSLVERR),
    .PRDATA(PRDATA)
);
endmodule

SHARED TOOL AND OUTPUT RULES
This flow uses the native open-source Yosys frontend, not a commercial SVA
frontend. These syntax rules are mandatory:
Python first runs the prep task as a build check. All three tasks must use
the SAME script, DUT, harness, and assumptions. Keep -noautowire and
check -assert: undeclared/disconnected signals must be diagnosed, not ignored.
Use begin/end for procedural blocks, never C-style braces. Declare state
and initial blocks at module scope; do not nest initial inside always.

ALLOWED:
  always @(posedge PCLK) begin
      if (trigger) assert(result);
      if (environment_condition) assume(input_constraint);
      cover(reachable_event);
  end
  $past(...), $stable(...), $rose(...), and $fell(...) only inside that
  clocked block and only after valid history exists.

DO NOT USE these constructs in harness code (our restricted syntax subset):
  |->  |=>  ##  property  endproperty  sequence  endsequence
  assert property  assume property  cover property  always_ff  bind
  hierarchical internal references such as master.cs or slave.Cache

Translate every temporal implication mechanically. For example, never write
`A |-> B`; write `if (A) assert(B);`. Never put an implication inside assume.
For next-cycle checks, use a history/reset-guarded `if ($past(A)) assert(B);`.
Use plain immediate assert(...), assume(...), and cover(...) statements only.

Use `module apb4_formal(input wire PCLK);` for this single-clock flow.
Connect PCLK to both DUTs and use always @(posedge PCLK) for checker state.
Do not use gclk, multiclock, or initialize/assign/toggle PCLK. This ordinary
input-clock pattern was smoke-tested with cover, BMC, and memory writes on
the worker. Do not use `always #...` or any simulation clock generator.
Use symbolic requester inputs. Declare and connect every checked bus signal.
Do not use simulation delays or event waits in initial blocks.

Assume PRESETn=0 on the first clock and 1 afterward. Use an initialized
first-cycle flag that advances even during reset; do not reset that flag with
PRESETn. Initialize checker state and guard $past() with valid history and
reset release. Sample registered slave outputs at the appropriate clock.
Normal protocol checks need PRESETn and $past(PRESETn), as well as valid
history. A request during reset is ignored: it must not trigger a next-cycle
SETUP requirement. Keep reset assertions separate so reset is still checked.

Keep the checker small: use one `(* anyconst *) reg [9:0] watched_addr`,
one 32-bit expected word, and four initially-clear byte-valid bits, NOT a
1024-entry shadow memory. Update selected bytes on completed writes to that
address; preserve other bytes, including across partial/intervening writes.
Compare only known bytes on the correctly delayed read response. Do not
constrain all traffic to watched_addr or assume unwritten memory is zero.
Cover a known-byte readback and a partial overwrite after a full write.
Gate data-integrity checks to legal reads with PSTRB==0. An invalid read with
nonzero PSTRB checks PSLVERR instead; it need not update PRDATA and must not
be treated as a successful readback.

Use the same DUT, reset, and environment assumptions for cover and bmc.
Give checks stable labels and brief requirement comments. Preserve valid
checks across repairs; never replace assertions with assumptions or remove
unreached covers just to get PASS. Cover PASS establishes reachability only;
BMC PASS checks assertions only within depth 20, not an unbounded proof.

Reply with a short explanation of the evidence, proposed changes, and any
remaining uncertainty, followed by one COMPLETE apb4.sby in the format below.
Fill the embedded module with your generated harness and properties. Return
no JSON, patches, nested fences, or separate property .sv file. On repair,
retain the valid existing checks and edit only what the diagnosis justifies.
Use this configuration layout (never [options bmc] or [options cover]):

=== apb4.sby ===
```text
[tasks]
prep
bmc
cover

[options]
prep:
mode prep
--
bmc:
mode bmc
depth 20
--
cover:
mode cover
depth 20
--

[engines]
prep: smtbmc
cover: smtbmc
bmc: smtbmc --keep-going

[script]
read_verilog -formal -sv -noautowire APB_Master.v APB_Slave.v APB_Wrapper.v apb4_formal.sv
prep -top apb4_formal
check -assert

[files]
APB_Master.v
APB_Slave.v
APB_Wrapper.v

[file apb4_formal.sv]
<complete module apb4_formal with connected DUT instances and properties>
```

The embedded harness must not also appear under [files]. Never return modified
RTL files. Before answering, scan the generated harness for every forbidden
construct listed above and remove it. Python runs the tools; do not claim you
ran them yourself.