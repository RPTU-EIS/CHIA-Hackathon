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
  Enforce this on the requester: if (PRESETn) assume(SADDR < 32'd1024).
  Constraining only the 10-bit watched_addr does not constrain bus traffic.
  Compare the FULL PADDR with watched_addr, not just PADDR[9:0]. Out-of-range
  accesses and aliasing are outside this experiment, including its covers.
- A legal read has PWRITE=0, PSTRB=0000, an address in 0..1023, one SETUP
  cycle followed by ACCESS, and completes when PREADY=1. Check read data only
  after known writes to that address. A read with nonzero PSTRB is invalid:
  check its error response separately and do not require valid PRDATA from it.
- Treat writes during SETUP as RTL behavior to evaluate, not an assumed
  protocol fact. Do not assume away behavior you cover.
- PSTRB selects bytes to update; unselected bytes must be preserved. A write
  with PSTRB=0000 selects no bytes: check preservation, not a mandatory error.
  Do not invent mandatory address-error responses or a ban on back-to-back
  transfers after PSLVERR. Neither is a requirement in this design brief.
- Instantiate master and slave directly with explicit APB wires because the
  wrapper exposes only PRDATA.

Explain any assumed requester contract on transfer and S* inputs. Assumptions
may constrain the environment, never force DUT outputs to satisfy assertions.
Check protocol sequencing, reset, stability, completion, errors, and data
integrity. Distinguish intended requirements from suspicious RTL behavior.
Idle requires PSEL=PENABLE=0, not zero address/data/control outputs.


PROPERTY DISCOVERY AFTER A BMC ROUND
(these response rules override the shared output format)
Use the current properties and accumulated bug reports to look for meaningful
requirements that are still missing. A discovered bug is a lead: add adjacent,
independent assertions for related boundary cases, timing, state transitions,
data isolation, and interactions that the existing assertion does not cover.
Do not duplicate an existing assertion or specialize it to the same trace.
Do not modify RTL, remove known failing assertions, weaken assumptions, or
encode current RTL behavior as the expected result. Add only 1-3 high-value
assertions per round, each with a reachable cover for its antecedent. Preserve
all valid existing properties and the complete SBY structure.
Stay within the same address scope and stated requirements. Do not invent
error policies or out-of-range tests to create new failing assertions.

The first line must contain exactly one of:
EXPLORE_MORE  when useful independent properties are missing; explain them and
              return one complete updated apb4.sby.
EXPLORE_DONE  when the design brief and the areas suggested by known bugs are
              already covered; explain why and return no files.

Accumulated bug reports:
Solver failed assertions: apb4_formal.sv:119.36-119.101, apb4_formal.sv:120.36-120.102, apb4_formal.sv:121.36-121.103, apb4_formal.sv:122.36-122.103, assert_c6_read_byte0, assert_c6_read_byte1, assert_c6_read_byte2, assert_c6_read_byte3

BUG_REPORT
The BMC run resulted in multiple assertion failures, all related to the read-data integrity check `C6`. These are not property errors; they have correctly identified a critical bug in the `APB_Slave` RTL.

**1. Diagnosis of Failures**

*   **Failure Classification:** Assertion `FAIL`. The log clearly reports multiple `Assert failed in apb4_formal` messages for `assert_c6_read_byte0` through `assert_c6_read_byte3`.

*   **Root Cause Analysis:**
    All failing assertions (`assert_c6_read_byte0`, `assert_c6_read_byte1`, `assert_c6_read_byte2`, `assert_c6_read_byte3`) share the same root cause. The properties themselves correctly model the intended behavior of byte-strobed writes as specified in the design brief: "PSTRB selects bytes to update; unselected bytes must be preserved." The checker's `expected_data` register is updated byte-wise, preserving the state of unselected bytes.

    The bug lies in the `APB_Slave.v` module. The write logic, implemented as a large `case (PSTRB)` statement (lines 29-45), does not preserve unselected bytes. Instead, for any partial write (where `PSTRB` is not `4'b1111`), each `case` item constructs a new 32-bit word from scratch, overwriting the unselected bytes with zeros or sign-extended values from the bytes that *are* being written.

*   **Evidence from RTL:**
    - **Failing Assertion (example):** `apb4_formal.assert_c6_read_byte3 at apb4_formal.sv:122.36-122.103 step 6`
    - **Broken Requirement:** Unselected bytes must be preserved during a write.
    - **Counterexample Scenario:**
        1.  A full-word write (`PSTRB=4'b1111`) occurs to `watched_addr`, initializing the slave's memory and the checker's `expected_data`.
        2.  A second, partial-word write occurs to the same `watched_addr`. For instance, with `PSTRB = 4'b0010` (write to byte 1).
        3.  The slave RTL executes `Cache[PADDR] <= {{24{PWDATA[15]}}, PWDATA[15:8], 8'h00};`. This incorrectly modifies bytes 0, 2, and 3, rather than preserving their previous values.
        4.  The checker correctly updates only byte 1 of its `expected_data` model.
        5.  A subsequent read from `watched_addr` returns the corrupted word from the slave.
        6.  The `assert_c6_*` checks fire, comparing the incorrect `PRDATA` from the slave against the correct `expected_data` from the checker model, leading to the failure.

*   **Faulty RTL Location:** `APB_Slave.v`, lines 29-45. The entire `case (PSTRB)` block is implemented incorrectly for byte-strobed writes.

**2. Bug Report**

*   **Bug:** The APB slave's write logic corrupts memory data during partial-word writes. It fails to preserve the value of bytes that are not selected by `PSTRB`.
*   **Smallest Suggested Repair (in prose):** The `case (PSTRB)` statement in `APB_Slave.v` should be removed. It should be replaced with logic that correctly performs byte-masked updates. A standard implementation would use `PSTRB` to enable writing to each byte lane of the target memory word independently, for example:
    ```verilog
    // This logic should replace the `case (PSTRB)` block
    if (PWRITE && PSEL) begin
        if (PSTRB[0]) Cache[PADDR[9:0]][7:0]   <= PWDATA[7:0];
        if (PSTRB[1]) Cache[PADDR[9:0]][15:8]  <= PWDATA[15:8];
        if (PSTRB[2]) Cache[PADDR[9:0]][23:16] <= PWDATA[23:16];
        if (PSTRB[3]) Cache[PADDR[9:0]][31:24] <= PWDATA[31:24];
        PSLVERR <= 0;
    end
    ```
    Note that this simple implementation relies on the synthesis tool correctly inferring a memory with byte-enable capabilities from the `reg [31:0] Cache [1023:0]` declaration. The existing RTL implementation is definitively incorrect.

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
module apb4_formal(input wire PCLK);
    // APB Interface Wires
    wire PSEL, PENABLE, PWRITE;
    wire [31:0] PADDR, PWDATA;
    wire [3:0] PSTRB;
    wire [2:0] PPROT;
    wire PREADY, PSLVERR;
    wire [31:0] PRDATA;

    // Requester (Environment) Inputs
    (* anyseq *) reg transfer, SWRITE;
    (* anyseq *) reg [31:0] SADDR, SWDATA;
    (* anyseq *) reg [3:0] SSTRB;
    (* anyseq *) reg [2:0] SPROT;

    // Reset and History Generation
    reg first_cycle = 1'b1;
    wire PRESETn = !first_cycle;
    reg past_valid = 1'b0;

    always @(posedge PCLK) begin
        first_cycle <= 1'b0;
        past_valid <= 1'b1;
    end

    // DUT Instantiation
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

    // --- Data Integrity Checker Model ---
    (* anyconst *) reg [31:0] watched_addr;
    initial assume(watched_addr < 1024);

    reg [31:0] expected_data;
    reg [3:0] byte_valid;

    initial byte_valid = 4'b0;

    // --- Data Integrity Logic ---
    wire is_access  = PSEL && PENABLE;
    wire is_complete = is_access && PREADY;
    wire is_write_complete = is_complete && PWRITE;
    wire is_read_complete  = is_complete && !PWRITE && PSTRB == 4'b0;

    // --- Formal Properties and Covers ---
    always @(posedge PCLK) begin
        // --- Reset Logic for Checker ---
        if (!PRESETn) begin
            byte_valid <= 4'b0;
        end

        // --- Environment Assumptions ---
        // A1: Assume valid address from requester when not in reset
        if (PRESETn) begin
            assume_a1_addr_range: assume(SADDR < 1024);
        end

        // --- Protocol Assertions and Covers ---
        // C1: PSEL/PENABLE must be low during reset
        if (!PRESETn) begin
            assert_c1_reset_idle: assert(PSEL == 0 && PENABLE == 0);
        end
        
        // --- Checks requiring past state (guarded by past_valid and PRESETn) ---
        if (past_valid && $past(PRESETn)) begin
            // C2: SETUP phase must be followed by ACCESS phase
            if ($past(PSEL && !PENABLE)) begin
                assert_c2_setup_to_access: assert(PSEL == 1 && PENABLE == 1);
            end

            // C3: Address and control signals must be stable from SETUP to ACCESS
            if ((PSEL && PENABLE) && $past(PSEL && !PENABLE)) begin
                assert_c3_stable_addr:   assert(PADDR   == $past(PADDR));
                assert_c3_stable_pwrite: assert(PWRITE  == $past(PWRITE));
                assert_c3_stable_pwdata: assert(PWDATA  == $past(PWDATA));
                assert_c3_stable_pstrb:  assert(PSTRB   == $past(PSTRB));
                assert_c3_stable_pprot:  assert(PPROT   == $past(PPROT));
            end
            
            // C4/C5: PSLVERR response is registered, check one cycle after condition
            if ($past(PSEL && !PWRITE)) begin
                if ($past(PSTRB != 4'b0)) begin
                    // C4: Invalid read must cause an error
                    assert_c4_invalid_read_slverr: assert(PSLVERR == 1);
                end else begin
                    // C5: Valid read must not cause an error
                    assert_c5_valid_read_no_slverr: assert(PSLVERR == 0);
                end
            end
        end

        if (PRESETn) begin
            // Update checker model on completed write to watched_addr
            if (is_write_complete && PADDR == watched_addr) begin
                if (PSTRB[0]) expected_data[7:0]   <= PWDATA[7:0];
                if (PSTRB[1]) expected_data[15:8]  <= PWDATA[15:8];
                if (PSTRB[2]) expected_data[23:16] <= PWDATA[23:16];
                if (PSTRB[3]) expected_data[31:24] <= PWDATA[31:24];
                
                if (PSTRB[0]) byte_valid[0] <= 1'b1;
                if (PSTRB[1]) byte_valid[1] <= 1'b1;
                if (PSTRB[2]) byte_valid[2] <= 1'b1;
                if (PSTRB[3]) byte_valid[3] <= 1'b1;
            end
            
            // C6: Read data integrity check for known bytes
            if (is_read_complete && PADDR == watched_addr) begin
                if (byte_valid[0]) assert_c6_read_byte0: assert(PRDATA[7:0]   == expected_data[7:0]);
                if (byte_valid[1]) assert_c6_read_byte1: assert(PRDATA[15:8]  == expected_data[15:8]);
                if (byte_valid[2]) assert_c6_read_byte2: assert(PRDATA[23:16] == expected_data[23:16]);
                if (byte_valid[3]) assert_c6_read_byte3: assert(PRDATA[31:24] == expected_data[31:24]);
            end
        end
        
        // --- Cover Points ---
        cover_cov1_reset_assert: cover(!PRESETn);
        if (past_valid) begin
            if ($past(!PRESETn) && PRESETn) cover_cov1_reset_release: cover(1);
        end

        cover_cov2_setup: cover(PSEL && !PENABLE);
        cover_cov2_access: cover(PSEL && PENABLE);

        if (is_write_complete && PSTRB == 4'b1111) cover_cov3_full_write: cover(1);
        if (is_read_complete) cover_cov3_legal_read: cover(1);
        if (past_valid && $past(is_complete) && (PSEL && !PENABLE)) cover_cov4_b2b: cover(1);
        if (is_complete && !PWRITE && PSTRB != 4'b0) cover_cov5_invalid_read: cover(1);
        if (is_read_complete && PADDR == watched_addr && |byte_valid) cover_cov6_read_known: cover(1);
        if (is_write_complete && PADDR == watched_addr && PSTRB != 4'b1111 && PSTRB != 4'b0 && $past(byte_valid == 4'b1111))
            cover_cov7_partial_overwrite: cover(1);
        if (is_write_complete && PSTRB == 4'b0) cover_cov8_pstrb0_write: cover(1);
    end
endmodule


Current RTL (read-only):
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

HARNESS SYNTAX EXAMPLE (mechanics only; generate your own properties)
Use these declaration/reset patterns at module scope:
  (* anyseq *) reg transfer, SWRITE;
  (* anyseq *) reg [31:0] SADDR, SWDATA;
  (* anyseq *) reg [3:0] SSTRB;
  (* anyseq *) reg [2:0] SPROT;
  reg first_cycle = 1'b1;
  reg past_valid = 1'b0;
  wire PRESETn = !first_cycle;
  always @(posedge PCLK) begin
      first_cycle <= 1'b0;
      past_valid <= 1'b1;
  end
An anyseq attribute belongs on the complete reg declaration. Never write
`(* anyseq *) SWRITE;` inside a process. Changing an undriven internal reg
to wire does NOT supply a driver; use anyseq or a real top-level input port.
Give each checker register one procedural driver (plus initialization).
Use ONLY @(posedge PCLK) for checker processes, with reset tested inside
the block. Never add negedge PRESETn to a checker containing $past/asserts.
Do not redeclare signals or put declarations/initial/always blocks inside
another always block. Close every begin/end before starting the next block.

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
  inside  foreach
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
Keep reset covers outside post-reset guards: cover(!PRESETn) cannot fire
inside a block requiring $past(PRESETn) when reset is asserted only initially.
Cover reset release before requiring $past(PRESETn) to be high.

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
Align each read check to ONE response delay. If a checker register captures
the read event with <=, that register already represents the previous cycle:
do not apply $past() to it again. Alternatively use $past(raw_read_event)
directly, with history/reset guards. Align expected data and valid-byte bits
to that same transaction. Check before the next transfer can overwrite
PRDATA; this slave updates PRDATA at SETUP as well as ACCESS.

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
After prep passes, preserve declarations, DUT connections, reset generation,
and the SBY layout across property/cover repairs unless the logs specifically
implicate them. Do not rewrite working harness mechanics during review.
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