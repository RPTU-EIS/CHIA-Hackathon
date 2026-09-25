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
- Treat writes during SETUP and nonzero read-strobe errors as RTL behavior to
  evaluate, not assumed protocol facts. Do not assume away behavior you cover.
- Instantiate master and slave directly with explicit APB wires because the
  wrapper exposes only PRDATA.

Explain any assumed requester contract on transfer and S* inputs. Assumptions
may constrain the environment, never force DUT outputs to satisfy assertions.
Check protocol sequencing, reset, stability, completion, errors, and data
integrity. Distinguish intended requirements from suspicious RTL behavior.
Idle requires PSEL=PENABLE=0, not zero address/data/control outputs.


BUILD REPAIR: MAKE THE CURRENT CANDIDATE EXECUTABLE
No property verdict is available. Read the detailed Yosys logs, not just
'base: task failed'. Quote the first actual error and its source location.
Repair only the SBY configuration, syntax, or harness wiring/clocking.
For unsupported process-memory sync, check for gclk and use the ordinary
input clock and single-word checker described below. For a timeout, identify
the slow stage; simplify checker implementation without weakening checks.
For a malformed response, return the complete required embedded-file format.
Do not change RTL, requirements, assertions, covers, reset, or depth merely
to silence an error. If the tool installation is broken, explain the blocker.
Return a complete corrected SBY. Preserve working code outside the diagnosed
error. Python checks the build before accepting the candidate and running cover.

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
    input wire PCLK
);

// DUT interface signals
reg PRESETn;
// Requester (System) side inputs
(* anyseq *) reg SWRITE;
(* anyseq *) reg [31:0] SADDR, SWDATA;
(* anyseq *) reg [3:0] SSTRB;
(* anyseq *) reg [2:0] SPROT;
(* anyseq *) reg transfer;

// DUT outputs and internal APB signals
wire [31:0] PRDATA;
wire PSEL, PENABLE, PWRITE;
wire [31:0] PADDR, PWDATA;
wire [3:0] PSTRB;
wire [2:0] PPROT;
wire PREADY, PSLVERR;

// Instantiate Master and Slave directly as per instructions
APB_Master Master (
    .PCLK(PCLK), .PRESETn(PRESETn), .SWRITE(SWRITE), .SADDR(SADDR),
    .SWDATA(SWDATA), .SSTRB(SSTRB), .SPROT(SPROT), .transfer(transfer),
    .PSEL(PSEL), .PENABLE(PENABLE), .PWRITE(PWRITE), .PADDR(PADDR),
    .PWDATA(PWDATA), .PSTRB(PSTRB), .PPROT(PPROT), .PREADY(PREADY),
    .PSLVERR(PSLVERR)
);

APB_Slave Slave (
    .PCLK(PCLK), .PRESETn(PRESETn), .PSEL(PSEL), .PENABLE(PENABLE),
    .PWRITE(PWRITE), .PADDR(PADDR), .PWDATA(PWDATA), .PSTRB(PSTRB),
    .PPROT(PPROT), .PREADY(PREADY), .PSLVERR(PSLVERR), .PRDATA(PRDATA)
);

// --- Formal Framework ---
reg f_past_valid;
// Data integrity checker state
(* anyconst *) reg [9:0] watched_addr;
reg [31:0] expected_data;
reg [3:0] byte_valid;
reg wrote_full_word;

initial begin
    // Start with reset asserted. It will be de-asserted on the first clock edge.
    PRESETn = 1'b0;
    // f_past_valid is false only during the first cycle (time 0 to first posedge).
    f_past_valid = 1'b0;
    // Initialize checker state.
    byte_valid = 4'b0;
    wrote_full_word = 1'b0;
end

always @(posedge PCLK) begin
    // De-assert reset after the first cycle. It will stay de-asserted.
    if (!f_past_valid) begin
        PRESETn <= 1'b1;
    end
    // f_past_valid becomes true after the first clock edge and stays true.
    f_past_valid <= 1'b1;

    // --- Environment Assumptions ---
    // e_addr_in_range: Assume address is always within the slave's memory range.
    if (transfer) begin
        assume(SADDR < 1024);
    end

    // e_no_transfer_in_reset: Assume a new transfer is not requested during reset.
    if (!PRESETn) begin
        assume(!transfer);
    end

    // --- Reset checks ---
    // a_slave_reset_outputs: Slave outputs are cleared on the cycle after reset.
    if (f_past_valid && $past(!PRESETn)) begin
        assert(PRDATA == 0); // a_slave_reset_prdata
        assert(PSLVERR == 0); // a_slave_reset_pslverr
    end

    // a_master_reset_outputs: During reset, master outputs must be low.
    if (!PRESETn) begin
        assert(PSEL == 0);    // a_master_reset_psel
        assert(PENABLE == 0); // a_master_reset_penable
        assert(PWRITE == 0);  // a_master_reset_pwrite
        assert(PADDR == 0);   // a_master_reset_paddr
        assert(PWDATA == 0);  // a_master_reset_pwdata
        assert(PSTRB == 0);   // a_master_reset_pstrb
        assert(PPROT == 0);   // a_master_reset_pprot
    end

    // --- Protocol and Behavior Checks (post-reset) ---
    // All checks using $past must be guarded by f_past_valid.
    if (f_past_valid) begin
        // a_penable_requires_psel: PENABLE is high only if PSEL is high.
        assert(!PENABLE || PSEL);

        // a_write_only_in_access: PWRITE is meaningful only in ACCESS (PSEL=1, PENABLE=1).
        if (PSEL && PWRITE) begin
            assert(PENABLE);
        end

        // a_addr_control_stable: Address/control signals are stable from SETUP to ACCESS.
        if ($past(PSEL && !PENABLE)) begin
            assert($stable(PADDR));
            assert($stable(PWRITE));
            assert($stable(PPROT));
            assert($stable(PSTRB));
            if ($past(PWRITE)) begin
                assert($stable(PWDATA));
            end
        end

        // a_pslverr_on_read_with_strb: Slave must error if PSTRB is non-zero for a read.
        if ($past(PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0)) begin
            assert(PSLVERR);
        end

        // a_no_pslverr_on_valid_setup: Slave must not error for writes or valid reads.
        if ($past(PSEL && !PENABLE && (PWRITE || PSTRB == 4'b0))) begin
            assert(!PSLVERR);
        end

        // --- Cover Points ---
        cover(PSEL && !PENABLE);  // c_setup_phase
        cover(PSEL && PENABLE);   // c_access_phase
        cover(PSEL && PENABLE && PWRITE && PREADY); // c_write_complete
        cover(PSEL && PENABLE && !PWRITE && PREADY); // c_read_complete
        cover($past(PSEL && PENABLE && PREADY) && (PSEL && !PENABLE)); // c_back_to_back
        cover(PSLVERR && $past(PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0)); // c_read_error
        cover(wrote_full_word && $past(PSEL && PENABLE && PWRITE && PREADY && PADDR[9:0] == watched_addr && PSTRB inside { [4'b0001:4'b1110] })); // c_partial_overwrite
        cover(PSEL && PENABLE && !PWRITE && PREADY && PADDR[9:0] == watched_addr && !PSLVERR && |byte_valid); // c_readback_known_byte

        // --- Data Integrity Checker ---
        // Model Update: Update shadow model when a write to the watched address completes.
        // A write completes when PSEL, PENABLE, and PREADY are high in the ACCESS phase.
        if (PSEL && PENABLE && PWRITE && PREADY && PADDR[9:0] == watched_addr) begin
            expected_data <= {
                (PSTRB[3] ? PWDATA[31:24] : expected_data[31:24]),
                (PSTRB[2] ? PWDATA[23:16] : expected_data[23:16]),
                (PSTRB[1] ? PWDATA[15:8]  : expected_data[15:8]),
                (PSTRB[0] ? PWDATA[7:0]   : expected_data[7:0])
            };
            byte_valid <= byte_valid | PSTRB;
            if (PSTRB == 4'b1111) begin
                wrote_full_word <= 1'b1;
            end
        end

        // Assertion: Check read data during a completed read access.
        if (PSEL && PENABLE && !PWRITE && PREADY && PADDR[9:0] == watched_addr && !PSLVERR) begin
            // a_data_integrity: Read data must match expected data for all known bytes.
            assert((!byte_valid[0] || PRDATA[7:0]   == expected_data[7:0]) &&
                   (!byte_valid[1] || PRDATA[15:8]  == expected_data[15:8]) &&
                   (!byte_valid[2] || PRDATA[23:16] == expected_data[23:16]) &&
                   (!byte_valid[3] || PRDATA[31:24] == expected_data[31:24]));
        end
    end
end

endmodule


Build/format/timeout diagnostics:
Key diagnostics:
SBY  3:33:53 [apb4_prep] base: apb4_formal.sv:131: ERROR: syntax error, unexpected TOK_ID
SBY  3:33:53 [apb4_prep] base: task failed. ERROR.
SBY  3:33:53 [apb4_prep] DONE (ERROR, rc=16)
SBY  3:33:53 The following tasks failed: ['prep']
apb4_formal.sv:131: ERROR: syntax error, unexpected TOK_ID
Log tail:
SBY  3:33:53 [apb4_prep] Removing directory '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_be9968e362c0e6b5/apb4_gemini_formal/loop_work_shorter/apb4_prep'.
SBY  3:33:53 [apb4_prep] Writing '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_be9968e362c0e6b5/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/apb4_formal.sv'.
SBY  3:33:53 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_be9968e362c0e6b5/apb4_gemini_formal/loop_work_shorter/APB_Master.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_be9968e362c0e6b5/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Master.v'.
SBY  3:33:53 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_be9968e362c0e6b5/apb4_gemini_formal/loop_work_shorter/APB_Slave.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_be9968e362c0e6b5/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Slave.v'.
SBY  3:33:53 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_be9968e362c0e6b5/apb4_gemini_formal/loop_work_shorter/APB_Wrapper.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_be9968e362c0e6b5/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Wrapper.v'.
SBY  3:33:53 [apb4_prep] base: starting process "cd apb4_prep/src; yosys -ql ../model/design.log ../model/design.ys"
SBY  3:33:53 [apb4_prep] base: apb4_formal.sv:131: ERROR: syntax error, unexpected TOK_ID
SBY  3:33:53 [apb4_prep] base: finished (returncode=1)
SBY  3:33:53 [apb4_prep] base: task failed. ERROR.
SBY  3:33:53 [apb4_prep] summary: Elapsed clock time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  3:33:53 [apb4_prep] summary: Elapsed process time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  3:33:53 [apb4_prep] summary: engine_0 (smtbmc) did not return a status
SBY  3:33:53 [apb4_prep] summary: engine_0 did not produce any traces
SBY  3:33:53 [apb4_prep] DONE (ERROR, rc=16)
SBY  3:33:53 The following tasks failed: ['prep']

=== model/design.log ===

 /----------------------------------------------------------------------------\
 |  yosys -- Yosys Open SYnthesis Suite                                       |
 |  Copyright (C) 2012 - 2026  Claire Xenia Wolf <claire@yosyshq.com>         |
 |  Distributed under an ISC-like license, type "license" to see terms        |
 \----------------------------------------------------------------------------/
 Yosys 0.68+130 (git sha1 dd83bbad2-dirty, Release, Clang /usr/bin/clang++ 21.1.8)

-- Executing script file `../model/design.ys' --

1. Executing Verilog-2005 frontend: APB_Master.v
Parsing formal SystemVerilog input from `APB_Master.v' to AST representation.
Generating RTLIL representation for module `\APB_Master'.
Successfully finished Verilog frontend.

2. Executing Verilog-2005 frontend: APB_Slave.v
Parsing formal SystemVerilog input from `APB_Slave.v' to AST representation.
Generating RTLIL representation for module `\APB_Slave'.
Successfully finished Verilog frontend.

3. Executing Verilog-2005 frontend: APB_Wrapper.v
Parsing formal SystemVerilog input from `APB_Wrapper.v' to AST representation.
Generating RTLIL representation for module `\APB_Wrapper'.
Successfully finished Verilog frontend.

4. Executing Verilog-2005 frontend: apb4_formal.sv
Parsing formal SystemVerilog input from `apb4_formal.sv' to AST representation.
apb4_formal.sv:131: ERROR: syntax error, unexpected TOK_ID


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