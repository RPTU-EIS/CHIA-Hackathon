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


STAGE 2: DIAGNOSE AND REPAIR COVERAGE
The cover task did not pass. BMC has not been accepted for this candidate.
1. Read the log first. Distinguish a setup/parse/tool error, timeout, and a
   completed cover search with unreached goals. An ERROR is not an RTL bug;
   an unreached goal at depth 20 does not prove it is unreachable forever.
2. Identify the exact affected cover or setup line. Check reset release,
   signal connections, history guards, and contradictory assumptions.
3. Correct only the faulty harness/configuration/cover definition or an
   unjustified environment assumption. Explain each change. Preserve valid
   goals, assertions, and depth 20. If a goal needs more cycles or appears
   blocked by the RTL, report that limitation instead of forcing a pass.

Allowed changes: generated SBY only. Return the complete SBY, including the
embedded harness. Next: Python retries cover before permitting BMC.

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
module apb4_formal(
    input wire PCLK,
    // DUT Inputs from environment
    input wire PRESETn,
    // Symbolic inputs from testbench to drive the master
    input wire SWRITE,
    input wire [31:0] SADDR,
    input wire [31:0] SWDATA,
    input wire [3:0] SSTRB,
    input wire [2:0] SPROT,
    input wire transfer
);
    // Wires connecting Master and Slave
    wire PSEL, PENABLE, PWRITE;
    wire [31:0] PADDR, PWDATA, PRDATA;
    wire [3:0] PSTRB;
    wire [2:0] PPROT;
    wire PREADY, PSLVERR;

    // Instantiate Master and Slave directly
    APB_Master master (
        .PCLK(PCLK), .PRESETn(PRESETn), .SWRITE(SWRITE), .SADDR(SADDR), .SWDATA(SWDATA),
        .SSTRB(SSTRB), .SPROT(SPROT), .transfer(transfer), .PSEL(PSEL), .PENABLE(PENABLE),
        .PWRITE(PWRITE), .PADDR(PADDR), .PWDATA(PWDATA), .PSTRB(PSTRB), .PPROT(PPROT),
        .PREADY(PREADY), .PSLVERR(PSLVERR)
    );

    APB_Slave slave (
        .PCLK(PCLK), .PRESETn(PRESETn), .PSEL(PSEL), .PENABLE(PENABLE), .PWRITE(PWRITE),
        .PADDR(PADDR), .PWDATA(PWDATA), .PSTRB(PSTRB), .PPROT(PPROT), .PREADY(PREADY),
        .PSLVERR(PSLVERR), .PRDATA(PRDATA)
    );

    // Formal verification logic
    reg [4:0] f_clk_count = 0;
    reg f_past_valid = 0;

    // Data integrity checker model
    (* anyconst *) reg [9:0] watched_addr;
    reg [31:0] expected_data;
    reg [3:0] byte_valid;
    reg f_full_write_done_for_cover;
    
    initial begin
        f_clk_count = 0;
        f_past_valid = 0;
        byte_valid = 4'b0;
        f_full_write_done_for_cover = 1'b0;
    end

    always @(posedge PCLK) begin
        // --- Formal Harness State ---
        if (f_clk_count < 31) begin
            f_clk_count <= f_clk_count + 1;
        end
        // Enable $past checks after the first cycle
        if (f_clk_count == 1) begin
            f_past_valid <= 1;
        end

        // A_PRESETn: Assume reset is asserted low for one cycle at the beginning.
        assume(PRESETn == (f_clk_count == 0 ? 1'b0 : 1'b1));

        // --- DUT Reset Behavior ---
        if (!PRESETn) begin
            // C_master_reset_outputs: Master outputs are zero during reset.
            assert(PSEL == 0);
            assert(PENABLE == 0);
            // C_slave_reset_outputs: Slave outputs are zero during reset.
            assert(PSLVERR == 0);
            assert(PRDATA == 0);
        end

        // --- Data Integrity Model Update ---
        if (!PRESETn) begin
            expected_data <= 32'b0;
            byte_valid <= 4'b0;
            f_full_write_done_for_cover <= 1'b0;
        end else if (f_past_valid) begin
            // On a completed write to the watched address, update our model.
            if ($past(PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr)) begin
                expected_data <= {
                    ($past(PSTRB[3])) ? $past(PWDATA[31:24]) : expected_data[31:24],
                    ($past(PSTRB[2])) ? $past(PWDATA[23:16]) : expected_data[23:16],
                    ($past(PSTRB[1])) ? $past(PWDATA[15:8])  : expected_data[15:8],
                    ($past(PSTRB[0])) ? $past(PWDATA[7:0])   : expected_data[7:0]
                };
                byte_valid <= byte_valid | $past(PSTRB);
            end

            // Set flag one cycle after a completed FULL write to the watched address.
            if ($past(PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PSTRB == 4'b1111)) begin
                f_full_write_done_for_cover <= 1'b1;
            end
        end

        // --- Protocol Assertions (post-reset) ---
        if (f_past_valid && $past(PRESETn)) begin
            // A_SADDR_in_range: Assume requester provides a valid address.
            assume(SADDR < 1024);

            // A_S_inputs_stable_during_setup: Requester must hold inputs stable during SETUP phase.
            if (PSEL && !PENABLE) begin
                assume($stable(SADDR));
                assume($stable(SWDATA));
                assume($stable(SWRITE));
                assume($stable(SSTRB));
                assume($stable(SPROT));
            end

            // C_pready_is_correct: Slave asserts PREADY in ACCESS phase (no wait states).
            assert(PREADY == (PSEL && PENABLE));

            // C_control_stability: APB signals must be stable from SETUP to ACCESS transition.
            if ($past(PSEL && !PENABLE) && (PSEL && PENABLE)) begin
                assert($stable(PADDR));
                assert($stable(PWRITE));
                assert($stable(PSTRB));
                assert($stable(PPROT));
                if ($past(PWRITE)) begin
                    assert($stable(PWDATA));
                end
            end

            // C_pslverr_on_invalid_read: Slave must error on read with non-zero PSTRB.
            // PSLVERR is registered, so it is asserted one cycle after the error condition.
            if ($past(PSEL && PENABLE && !PWRITE && PSTRB != 4'b0)) begin
                assert(PSLVERR == 1);
            end

            // C_no_pslverr_on_valid_access: Slave must not error on valid accesses.
            if ($past(PSEL && PENABLE && (PWRITE || (!PWRITE && PSTRB == 4'b0)))) begin
                assert(PSLVERR == 0);
            end

            // C_data_integrity: On a legal read, PRDATA must match our model for known bytes.
            // This assertion is expected to FAIL, exposing the slave's write logic bug.
            if ($past(PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0 && PADDR[9:0] == watched_addr)) begin
                if (byte_valid[0]) assert(PRDATA[7:0]   == expected_data[7:0]);
                if (byte_valid[1]) assert(PRDATA[15:8]  == expected_data[15:8]);
                if (byte_valid[2]) assert(PRDATA[23:16] == expected_data[23:16]);
                if (byte_valid[3]) assert(PRDATA[31:24] == expected_data[31:24]);
            end

            // --- Cover Properties ---
            cover(!PRESETn); // COV_reset
            cover(PSEL && !PENABLE); // COV_setup_phase
            cover(PSEL && PENABLE);  // COV_access_phase
            cover(PSEL && PENABLE && PREADY && PWRITE); // COV_write_complete
            cover(PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0); // COV_read_complete
            cover($past(PSEL && PENABLE && PREADY) && (PSEL && !PENABLE)); // COV_back_to_back
            cover(PSLVERR == 1); // COV_pslverr
            cover($past(PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 0 && PADDR[9:0] == watched_addr) && |byte_valid); // COV_data_readback
            
            // COV_partial_overwrite: Cover a partial write that happens *after* a full write.
            // This requires a partial write to complete in the current cycle, and for the
            // f_full_write_done_for_cover flag (which has a 2-cycle latency) to be set.
            // This requires a sequence of FULL_WRITE(at T-2 or earlier) -> PARTIAL_WRITE(at T).
            cover((PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PSTRB != 4'b1111) && f_full_write_done_for_cover);
        end
    end
endmodule


Cover log:
Key diagnostics:
SBY  4:55:36 [apb4_cover] engine_0: ##   0:00:01  Unreached cover statement at apb4_formal: apb4_formal.sv:146.13-146.28 (_witness_.check_cover_apb4_formal_sv_146_390)
SBY  4:55:36 [apb4_cover] engine_0: ##   0:00:01  Status: failed
SBY  4:55:36 [apb4_cover] engine_0: Status returned by engine: FAIL
SBY  4:55:36 [apb4_cover] summary: engine_0 (smtbmc) returned FAIL
SBY  4:55:36 [apb4_cover] summary: unreached cover statements:
SBY  4:55:36 [apb4_cover] summary: see apb4_cover/FAIL for a complete summary
SBY  4:55:36 [apb4_cover] DONE (FAIL, rc=2)
SBY  4:55:36 The following tasks failed: ['cover']
  Set init value: \f_full_write_done_for_cover = 1'0
    32/36: $0\f_full_write_done_for_cover[0:0]
Creating register for signal `\apb4_formal.\f_full_write_done_for_cover' using process `\apb4_formal.$proc$apb4_formal.sv:51$296'.
Log tail:
4_formal...
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
Computing hashes of 98 cells of `\apb4_formal'.
Finding duplicate cells in `\apb4_formal'.
Computing hashes of 90 cells of `\apb4_formal'.
Finding duplicate cells in `\apb4_formal'.
<suppressed ~24 debug messages>
Removed a total of 8 cells.

11.3. Executing OPT_DFF pass (perform DFF optimizations).

11.4. Executing OPT_CLEAN pass (remove unused cells and wires).
Finding unused cells or wires in module \APB_Master..
Finding unused cells or wires in module \APB_Slave..
Finding unused cells or wires in module \apb4_formal..
Removed 0 unused cells and 8 unused wires.
<suppressed ~1 debug messages>

11.5. Finished fast OPT passes.

12. Executing OPT_CLEAN pass (remove unused cells and wires).
Finding unused cells or wires in module \APB_Master..
Finding unused cells or wires in module \APB_Slave..
Finding unused cells or wires in module \apb4_formal..
Removed 0 unused cells and 140 unused wires.
<suppressed ~3 debug messages>

13. Executing RTLIL backend.
Output filename: ../model/design_prep.il

End of script. Logfile hash: 33e191150b, time: 0.05s, user: 0.05s, system: 0.01s, MEM: 21.01 MB peak
Yosys 0.68+130 (git sha1 dd83bbad2-dirty, Release, Clang /usr/bin/clang++ 21.1.8)
Time spent: 45% 4x opt_clean (0 sec), 12% 1x check (0 sec), ...

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
       91 wires
      362 wire bits
       31 public wires
      279 public wire bits
        8 ports
       75 port bits
       90 cells
        1   $add
        1   $anyconst
        8   $anyinit
        8   $assume
        9   $cover
        9   $eq
       17   $ff
       14   $logic_and
        5   $logic_not
        2   $lt
       12   $mux
        1   $ne
        1   $not
        1   $or
        1   $reduce_or
        2 submodules
        1   APB_Master
        1   APB_Slave

=== design hierarchy ===

        +----------Count including submodules.
        | 
      479 apb4_formal
       45 APB_Master
      344 APB_Slave

        +----------Count including submodules.
        | 
      497 wires
     8151 wire bits
      194 public wires
     3180 public wire bits
       37 ports
      336 port bits
        - memories
        - memory bits
        - processes
      479 cells
        1   $add
        3   $and
        1   $anyconst
       18   $anyinit
      124   $anyseq
       10   $assume
        9   $cover
       26   $eq
       17   $ff
       17   $logic_and
        7   $logic_not
        2   $lt
        1   $mem_v2
      230   $mux
        1   $ne
        6   $not
        1   $or
        1   $pmux
        1   $reduce_bool
        3   $reduce_or
        2 submodules
        1   APB_Master
        1   APB_Slave

6. Executing SMT2 backend.

6.1. Executing BMUXMAP pass.

6.2. Executing DEMUXMAP pass.
Creating SMT-LIBv2 representation of module APB_Slave.
Creating SMT-LIBv2 representation of module APB_Master.
Creating SMT-LIBv2 representation of module apb4_formal.

End of script. Logfile hash: 64a523c7e2, time: 0.02s, user: 0.02s, system: 0.01s, MEM: 17.88 MB peak
Yosys 0.68+130 (git sha1 dd83bbad2-dirty, Release, Clang /usr/bin/clang++ 21.1.8)
Time spent: 77% 2x write_smt2 (0 sec), 14% 2x read_rtlil (0 sec), ...


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