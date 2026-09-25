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

    // --- Formal verification state and reset generation ---
    reg fv_first_cycle;
    reg fv_past_valid; // Guards against checks at time 0

    initial begin
        fv_first_cycle = 1;
        fv_past_valid = 0;
    end

    always @(posedge PCLK) begin
        if (fv_first_cycle) begin
            PRESETn <= 0;
            fv_first_cycle <= 0;
        end else begin
            PRESETn <= 1;
        end
        fv_past_valid <= 1;
    end

    // --- Assumptions (Environment Constraints) ---
    // Assumed requester contract: The system driving the master provides inputs
    // that are stable when a transfer is initiated. When `transfer` is asserted,
    // the associated address in `SADDR` is assumed to be within the valid 4KB
    // range of the 1024-word slave memory. Other S* inputs are unconstrained.
    always @(posedge PCLK) begin
        if (fv_past_valid && transfer) begin
            // a_valid_addr: Constrain SADDR for transfers to be in the slave's 4KB range.
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
        end else if (fv_past_valid) begin
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
    initial saw_full_write_to_watched_addr = 0;

    // --- Assertions and Covers ---
    always @(posedge PCLK) begin
        if (!PRESETn) begin
            saw_full_write_to_watched_addr <= 0;
            // cover_reset_active: Cover the cycle where reset is active.
            cover(1);
        } else if (fv_past_valid) begin
            // --- Reset Checks ---
            // On the cycle after PRESETn was asserted low. Master is in IDLE.
            if ($past(PRESETn == 0)) begin
                // assert_master_reset_outputs: Master control signals are low after reset.
                assert(PSEL == 0);
                assert(PENABLE == 0);
                // assert_slave_reset_outputs: Slave registered outputs are zero after reset.
                assert(PRDATA == 0);
                assert(PSLVERR == 0);
            end

            // cover_reset_release: Cover the first cycle after reset de-assertion.
            if (PRESETn && !$past(PRESETn)) begin
                cover(1);
            end

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
            if (SETUP_PHASE) cover(1); // cover_setup
            if (ACCESS_PHASE) cover(1); // cover_access
            if ($past(XFER_DONE && PWRITE)) cover(1); // cover_write_complete
            if ($past(XFER_DONE && !PWRITE)) cover(1); // cover_read_complete
            if ($past(XFER_DONE) && SETUP_PHASE) cover(1); // cover_back_to_back
            if (ACCESS_PHASE && !PWRITE && PSTRB != 4'b0) cover(1); // cover_read_error
            if ($past(XFER_DONE && PWRITE && PADDR[11:2] == watched_addr)) cover(1); // cover_data_write
            if ($past(XFER_DONE && !PWRITE && PADDR[11:2] == watched_addr) && |$past(byte_valid)) cover(1); // cover_data_read

            // cover_partial_overwrite: Cover a partial write after a full write to the same address.
            if ($past(XFER_DONE && PWRITE && PSTRB != 4'b1111 && PSTRB != 4'b0000 && PADDR[11:2] == watched_addr) && saw_full_write_to_watched_addr) begin
                cover(1);
                saw_full_write_to_watched_addr <= 0;
            end else if ($past(XFER_DONE && PWRITE && PSTRB == 4'b1111 && PADDR[11:2] == watched_addr)) begin
                saw_full_write_to_watched_addr <= 1;
            end
        end
    end
endmodule


Build/format/timeout diagnostics:
Key diagnostics:
SBY  3:19:37 [apb4_prep] base: apb4_formal.sv:166: ERROR: syntax error, unexpected '}'
SBY  3:19:37 [apb4_prep] base: task failed. ERROR.
SBY  3:19:37 [apb4_prep] DONE (ERROR, rc=16)
SBY  3:19:37 The following tasks failed: ['prep']
apb4_formal.sv:166: ERROR: syntax error, unexpected '}'
Log tail:
SBY  3:19:37 [apb4_prep] Removing directory '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_9705f1e1102fb9b1/apb4_gemini_formal/loop_work_shorter/apb4_prep'.
SBY  3:19:37 [apb4_prep] Writing '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_9705f1e1102fb9b1/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/apb4_formal.sv'.
SBY  3:19:37 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_9705f1e1102fb9b1/apb4_gemini_formal/loop_work_shorter/APB_Master.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_9705f1e1102fb9b1/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Master.v'.
SBY  3:19:37 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_9705f1e1102fb9b1/apb4_gemini_formal/loop_work_shorter/APB_Slave.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_9705f1e1102fb9b1/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Slave.v'.
SBY  3:19:37 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_9705f1e1102fb9b1/apb4_gemini_formal/loop_work_shorter/APB_Wrapper.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_9705f1e1102fb9b1/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Wrapper.v'.
SBY  3:19:37 [apb4_prep] base: starting process "cd apb4_prep/src; yosys -ql ../model/design.log ../model/design.ys"
SBY  3:19:37 [apb4_prep] base: apb4_formal.sv:166: ERROR: syntax error, unexpected '}'
SBY  3:19:37 [apb4_prep] base: finished (returncode=1)
SBY  3:19:37 [apb4_prep] base: task failed. ERROR.
SBY  3:19:37 [apb4_prep] summary: Elapsed clock time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  3:19:37 [apb4_prep] summary: Elapsed process time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  3:19:37 [apb4_prep] summary: engine_0 (smtbmc) did not return a status
SBY  3:19:37 [apb4_prep] summary: engine_0 did not produce any traces
SBY  3:19:37 [apb4_prep] DONE (ERROR, rc=16)
SBY  3:19:37 The following tasks failed: ['prep']

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
apb4_formal.sv:166: ERROR: syntax error, unexpected '}'


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