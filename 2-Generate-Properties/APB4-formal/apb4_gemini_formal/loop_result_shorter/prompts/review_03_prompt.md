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
Discovery focus: byte-strobe behavior and data integrity. Generate one small,
independent property per round, grounded in this brief. Choose the scenario
and checker yourself; justify assumptions and sampling timing, and cover its
trigger. Refine this property before expanding to another requirement within
this focus. Defer unrelated protocol/error discovery. Distinguish intended
requirements from suspicious RTL behavior.
Idle requires PSEL=PENABLE=0, not zero address/data/control outputs.


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
PRESETn. Keep history validity independent of reset: initialize past_valid to
zero and set it to one after the first clock, unconditionally. Never use
past_valid <= PRESETn as the history guard for reset assertions. Initialize
checker state and guard $past() with valid history; normal-operation checks
also require reset release. Check synchronous reset outputs on the clock
following asserted reset, outside normal-operation guards. Add a cover for
the exact reset-assertion trigger, including ALL enclosing guards, to verify
it can execute. Do not add assumptions to force the trigger or outputs true.
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
BMC PASS checks assertions only within depth 10, not an unbounded proof.
Keep both BMC and cover at depth 10 throughout generation and repairs.

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
depth 10
--
cover:
mode cover
depth 10
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

PROPERTY REVIEW AFTER COVER (these response rules override the shared format)
Cover passed. Audit the CURRENT assertions against the design brief before
BMC. Passing cover is only evidence that the selected scenarios are reachable.
For each assertion, briefly identify its intended requirement and check:
- Reset guards and $past history: a request during reset is not accepted.
  Verify history validity advances independently of reset. Evaluate the full
  synchronous-reset assertion trigger on the first post-reset clock, including
  all enclosing guards, and require a matching cover. Reject contradictory
  guards rather than approving an assertion that never executes.
- Clock timing: show a short edge-by-edge table for read A followed immediately
  by read B with different data. Include phase/address, sampled PRDATA, read
  trigger/delayed flag, expected data, and the assertion edge. Reject a checker
  that compares B's response with A's expected word. A registered read flag
  followed by $past(flag) adds a second delay; do not approve it by inspection
  of comments alone. Account for this slave's SETUP read update.
- Scope: verify an actual assumption on SADDR limits traffic to 0..1023,
  full-address matches gate the scoreboard/read checks, and covers stay in
  scope. A range assumption on watched_addr alone is insufficient.
- Requirements: identify the design-brief requirement for each assertion.
  Reject invented mandatory errors for zero-strobe writes/out-of-range
  addresses or a ban on back-to-back transfers after an error. Preserve the
  valid zero-strobe memory-preservation requirement instead.
- Data readback is checked only for legal reads with PSTRB==0; an invalid
  read may leave PRDATA unchanged while reporting PSLVERR.
- The expected-data model implements the intended byte-lane behavior. Reject
  any model that copies the DUT's case(PSTRB) expressions or other suspected
  RTL behavior, because comparing the implementation with itself hides bugs.
- Assumptions constrain legal requester behavior, not DUT outputs, and do
  not exclude the cases the assertion must detect.
- The check is not a tautology; its cover exercises the actual antecedent.
- A legal trace should satisfy it and a violation should fail it. Explain
  a concrete example for any suspicious check. Do not invent requirements.
A valid assertion may expose an RTL bug: retain it for BMC. Do not rewrite
the RTL during this review or weaken a requirement to match the current RTL.

The first line must contain only REVIEW_OK, REVIEW_FIX, or REVIEW_BLOCKED,
with no Markdown or other text on that line.
REVIEW_OK means all the checks above are justified; give the audit and timing
table only. Missing timing/scope evidence requires REVIEW_FIX or REVIEW_BLOCKED.
REVIEW_FIX means repairs are needed; explain and return the complete corrected
apb4.sby in the shared format. No RTL files.
REVIEW_BLOCKED means a requirement is ambiguous; explain what is missing.
For REVIEW_OK, return no files. For REVIEW_FIX, Python reruns the build check,
cover, and this review before BMC. This is an LLM assessment, not a proof.

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
depth 10
--
cover:
mode cover
depth 10
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
  // --- External System / Requester Driver ---
  (* anyseq *) reg transfer, SWRITE;
  (* anyseq *) reg [31:0] SADDR, SWDATA;
  (* anyseq *) reg [3:0] SSTRB;
  (* anyseq *) reg [2:0] SPROT;

  // --- Reset and History Generation ---
  reg first_cycle = 1'b1;
  reg past_valid = 1'b0;
  reg past_PRESETn = 1'b0;
  wire PRESETn = !first_cycle;

  always @(posedge PCLK) begin
    first_cycle <= 1'b0;
    past_valid <= 1'b1;
    past_PRESETn <= PRESETn;
  end

  // --- DUT Connections ---
  wire PSEL, PENABLE, PWRITE;
  wire [31:0] PADDR, PWDATA, PRDATA;
  wire [3:0] PSTRB;
  wire [2:0] PPROT;
  wire PREADY, PSLVERR;

  APB_Master Master (
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

  // --- Requester Contract (Assumptions) ---
  always @(posedge PCLK) begin
    // Assume valid addresses are used for transfers after reset.
    if (PRESETn && transfer) begin
      assume(SADDR < 32'd1024);
    end
  end

  // --- Properties and Scoreboard ---
  (* anyconst *) reg [31:0] watched_addr;
  initial assume(watched_addr[31:10] == 22'b0); // Constrain to 0..1023

  reg [31:0] expected_data;
  reg [3:0]  known_bytes;

  wire is_setup   = PSEL && !PENABLE;
  wire is_access  = PSEL && PENABLE;
  wire write_complete = is_access && PWRITE && PREADY;
  wire read_complete  = is_access && !PWRITE && PREADY;
  wire at_watched_addr = (PADDR == watched_addr);
  
  reg saw_legal_read_setup_at_watched;

  always @(posedge PCLK) begin
    if (!PRESETn) begin
      known_bytes <= 4'b0;
      saw_legal_read_setup_at_watched <= 1'b0;
      // expected_data does not need reset, its validity is tracked by known_bytes
    end else begin
      // --- Helper flag for legal read setup ---
      saw_legal_read_setup_at_watched <= is_setup && !PWRITE && at_watched_addr && (PSTRB == 4'b0);

      // --- Scoreboard Update ---
      // Update on completed write to watched_addr. The model correctly preserves
      // un-strobed bytes, which the RTL is suspected of not doing.
      if (write_complete && at_watched_addr) begin
        if (PSTRB[0]) expected_data[7:0]   <= PWDATA[7:0];
        if (PSTRB[1]) expected_data[15:8]  <= PWDATA[15:8];
        if (PSTRB[2]) expected_data[23:16] <= PWDATA[23:16];
        if (PSTRB[3]) expected_data[31:24] <= PWDATA[31:24];
        known_bytes <= known_bytes | PSTRB;
      end

      // --- Data Integrity Assertion (Legal Read) ---
      // Check on the ACCESS cycle of a legal read to a watched address.
      if (read_complete && at_watched_addr && saw_legal_read_setup_at_watched) begin
        // Requirement: For a legal read, data from known-written bytes must match
        // the expected value from the scoreboard.
        if (known_bytes[0]) a_data_byte0: assert(PRDATA[7:0]   == expected_data[7:0]);
        if (known_bytes[1]) a_data_byte1: assert(PRDATA[15:8]  == expected_data[15:8]);
        if (known_bytes[2]) a_data_byte2: assert(PRDATA[23:16] == expected_data[23:16]);
        if (known_bytes[3]) a_data_byte3: assert(PRDATA[31:24] == expected_data[31:24]);
      end

      // --- Illegal Read Error Assertion ---
      // An invalid read is one where PWRITE=0 but PSTRB is not zero. This should
      // be flagged by the slave with PSLVERR on the following cycle.
      if (past_valid && $past(is_setup && !PWRITE && PSTRB != 4'b0)) begin
        // Requirement: An invalid read (PSTRB != 0) should be flagged with PSLVERR.
        a_illegal_read_slverr: assert(PSLVERR == 1'b1);
      end
    end
  end

  // --- Covers ---
  always @(posedge PCLK) begin
    if (past_valid) begin
        if (!past_PRESETn && PRESETn) begin
            c_reset_release: cover(1);
        end

        if (PRESETn) begin
            // Cover a full write, a partial write, and a zero-strobe write to the watched address
            if (write_complete && at_watched_addr) begin
                c_full_write:   cover(PSTRB == 4'b1111);
                c_partial_write:cover(PSTRB != 4'b1111 && PSTRB != 4'b0000);
                c_zero_strobe_write: cover(PSTRB == 4'b0000);
            end

            // Cover the data integrity assertion trigger for at least one known byte.
            if (read_complete && at_watched_addr && saw_legal_read_setup_at_watched && |known_bytes) begin
                c_read_after_write: cover(1);
            end

            // Cover the trigger for the illegal read check
            if (is_setup && !PWRITE && PSTRB != 4'b0) begin
                c_illegal_read_setup: cover(1);
            end
        end
    end
  end

endmodule


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