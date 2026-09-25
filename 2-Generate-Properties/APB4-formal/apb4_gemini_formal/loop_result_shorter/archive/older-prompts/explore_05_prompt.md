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
For EXPLORE_MORE, after the explanation write === apb4.sby === and ONE fenced
text block containing [tasks], [options], [engines], [script], [files], and
[file apb4_formal.sv] with the full module. Copy the working configuration
from the current SBY. A standalone Verilog block or embedded module alone is
NOT a complete response. Python will ask you to repair it, not assemble it.

Accumulated bug reports:
Solver failed assertions: a_data_integrity_b0, a_data_integrity_b1, a_data_integrity_b2, a_data_integrity_b3

BUG_REPORT
The BMC run failed with multiple assertions related to data integrity: `a_data_integrity_b0`, `a_data_integrity_b1`, `a_data_integrity_b2`, and `a_data_integrity_b3`. These failures all point to a single root-cause RTL bug in the `APB_Slave` module. The formal properties correctly model the requirement that unselected bytes in memory must be preserved during a write operation, but the slave RTL fails to do this.

**Root Cause Analysis:**

*   **Requirement:** The design brief specifies that for write operations, "PSTRB selects bytes to update; unselected bytes must be preserved."
*   **Failing Assertions:** The `a_data_integrity_*` assertions check read data against a shadow memory model. This model correctly implements byte-preservation on writes. All four byte-lane assertions failed, indicating that a partial write corrupts all un-strobed bytes.
    *   `a_data_integrity_b2: assert(PRDATA[23:16] == r_expected_data[23:16]);`
    *   `a_data_integrity_b3: assert(PRDATA[31:24] == r_expected_data[31:24]);`
    *   `a_data_integrity_b0: assert(PRDATA[7:0]   == r_expected_data[7:0]);`
    *   `a_data_integrity_b1: assert(PRDATA[15:8]  == r_expected_data[15:8]);`
*   **Counterexample:** A sequence that causes the failure is:
    1.  A full-word write to an address (e.g., `PSTRB=4'b1111`) populates memory and the shadow model.
    2.  A partial-word write to the same address (e.g., `PSTRB=4'b0001`) follows. The shadow model correctly updates only the selected byte, preserving the others.
    3.  The slave RTL, however, does not preserve the other bytes. For `PSTRB=4'b0001`, it sign-extends `PWDATA[7]` to the upper 24 bits, overwriting and corrupting bytes 1, 2, and 3. Other `PSTRB` values have similar bugs, often writing zeroes or sign-extended data to unselected byte lanes.
    4.  A subsequent read from that address returns the corrupted data from the slave, which mismatches the correctly preserved data in the shadow model, causing the assertions to fail.
*   **Faulty RTL Location:** `APB_Slave.v`, the `case (PSTRB)` statement inside the `always @(posedge PCLK)` block. This block incorrectly implements byte-strobed writes.
*   **Suggested Repair (Prose):** The `case (PSTRB)` logic is fundamentally flawed. It should be replaced with logic that updates memory on a byte-by-byte basis according to the strobes, leaving unselected bytes unchanged. For example, using four separate `if (PSTRB[i])` statements to update each byte lane of `Cache[PADDR]` individually would correctly implement the required behavior.

The formal properties are valid as they correctly represent the design requirements. No changes to the properties or harness are needed.

Solver failed assertions: a_data_integrity_b0, a_data_integrity_b1, a_data_integrity_b2, a_data_integrity_b3

BUG_REPORT
The BMC run failed with multiple assertions related to data integrity: `a_data_integrity_b0`, `a_data_integrity_b1`, `a_data_integrity_b2`, and `a_data_integrity_b3`. These failures all point to a single root-cause RTL bug in the `APB_Slave` module. The formal properties correctly model the requirement that unselected bytes in memory must be preserved during a write operation, but the slave RTL fails to do this.

**Root Cause Analysis:**

*   **Requirement:** The design brief specifies that for write operations, "PSTRB selects bytes to update; unselected bytes must be preserved."
*   **Failing Assertions:** The `a_data_integrity_*` assertions check read data against a shadow memory model. This model correctly implements byte-preservation on writes. All four byte-lane assertions failed, indicating that a partial write corrupts all un-strobed bytes.
    *   `a_data_integrity_b3: assert(PRDATA[31:24] == r_expected_data[31:24]);`
    *   `a_data_integrity_b2: assert(PRDATA[23:16] == r_expected_data[23:16]);`
    *   `a_data_integrity_b1: assert(PRDATA[15:8]  == r_expected_data[15:8]);`
    *   `a_data_integrity_b0: assert(PRDATA[7:0]   == r_expected_data[7:0]);`
*   **Counterexample:** The BMC tool found a sequence that causes the failure:
    1.  A full-word write to an address (e.g., `PSTRB=4'b1111`) populates the slave's memory and the formal checker's shadow model.
    2.  A subsequent partial-word write to the same address (e.g., `PSTRB=4'b0001`) follows. The shadow model correctly updates only the selected byte (`PWDATA[7:0]`), preserving the other three bytes from the first write.
    3.  The slave RTL, however, does not preserve the other bytes. For `PSTRB=4'b0001`, the faulty `case` statement executes `Cache[PADDR] <= {{24{PWDATA[7]}}, PWDATA[7:0]};`. This sign-extends `PWDATA[7]` to the upper 24 bits, overwriting and corrupting bytes 1, 2, and 3 of the stored word. Other `PSTRB` values have similar bugs, often writing zeroes or other incorrect data to unselected byte lanes.
    4.  A final read from that address returns the corrupted data from the slave. This data mismatches the correctly preserved data in the shadow model, causing the `a_data_integrity_*` assertions for the un-strobed bytes to fail.
*   **Faulty RTL Location:** `APB_Slave.v`, the `case (PSTRB)` statement inside the `always @(posedge PCLK)` block. This block incorrectly implements byte-strobed writes.
*   **Suggested Repair (Prose):** The `case (PSTRB)` logic is fundamentally flawed and does not meet the byte-preservation requirement. It should be replaced with logic that updates memory on a byte-by-byte basis according to the strobes, leaving unselected bytes unchanged. For example, using four separate `if (PSTRB[i])` statements to update each byte lane of `Cache[PADDR]` individually would correctly implement the required behavior.

The formal properties are valid as they correctly represent the design requirements. No changes to the properties or harness are needed.

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
    // DUT Connections
    wire PSEL, PENABLE, PWRITE;
    wire [31:0] PADDR, PWDATA, PRDATA;
    wire [3:0] PSTRB;
    wire [2:0] PPROT;
    wire PREADY, PSLVERR;

    // Requester (environment) signals
    (* anyseq *) reg SWRITE;
    (* anyseq *) reg [31:0] SADDR, SWDATA;
    (* anyseq *) reg [3:0] SSTRB;
    (* anyseq *) reg [2:0] SPROT;
    (* anyseq *) reg transfer;

    // Reset and history logic
    reg first_cycle = 1'b1;
    reg past_valid = 1'b0;
    wire PRESETn = !first_cycle;

    always @(posedge PCLK) begin
        first_cycle <= 1'b0;
        past_valid <= PRESETn;
    end

    // Instantiate Master and Slave directly as requested
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

    // --- Formal Properties ---

    // Shadow memory model for data integrity check
    (* anyconst *) reg [9:0] watched_addr;
    reg [31:0] r_expected_data;
    reg [3:0]  r_byte_valid;

    // Helper signals for state
    wire is_setup = PSEL && !PENABLE;
    wire is_access = PSEL && PENABLE;
    wire transfer_done = is_access && PREADY;
    wire is_write = PWRITE;
    wire is_read = !PWRITE;
    wire is_watched_addr = (PADDR[31:10] == 0) && (PADDR[9:0] == watched_addr);

    always @(posedge PCLK) begin
        // --- Shadow Model Update Logic ---
        // On reset, memory content is unknown
        if (!PRESETn) begin
            r_byte_valid <= 4'b0000;
        end
        // On a completed write to the watched address, update our model
        // This models the CORRECT behavior (byte preservation)
        else if (transfer_done && is_write && is_watched_addr) begin
            if (PSTRB[0]) r_expected_data[7:0]   <= PWDATA[7:0];
            if (PSTRB[1]) r_expected_data[15:8]  <= PWDATA[15:8];
            if (PSTRB[2]) r_expected_data[23:16] <= PWDATA[23:16];
            if (PSTRB[3]) r_expected_data[31:24] <= PWDATA[31:24];
            r_byte_valid <= r_byte_valid | PSTRB;
        end
    end

    always @(posedge PCLK) begin
        // --- Assumptions (Environment Constraints) ---
        // Assume reset is held for the first cycle only
        if (first_cycle) begin
            assume(!PRESETn);
        end else begin
            assume(PRESETn);
        end

        // Assume address is in range for any new transfer
        if (PRESETn && transfer) begin
            assume_saddr_in_range: assume(SADDR < 1024);
        end

        // --- Assertions (DUT Requirements) ---
        // During reset, check combinatorial master outputs
        if (!PRESETn) begin
            a_master_reset_state: assert(PSEL == 0 && PENABLE == 0);
        end

        // Post-reset checks
        if (past_valid) begin
            // On the first cycle after reset release, check slave's synchronous reset values
            if ($past(!PRESETn)) begin
                a_slave_reset_pslverr: assert(PSLVERR == 0);
                a_slave_reset_prdata: assert(PRDATA == 0);
            end

            // The slave should assert PREADY in the access phase (zero wait states)
            a_slave_ready: assert(PREADY == is_access);

            // --- Bus Stability Checks ---
            // APB protocol requires address and control signals to be stable during
            // the SETUP and ACCESS phases of a transfer. Check this on the transition
            // from SETUP to ACCESS.
            if (is_access && $past(is_setup)) begin
                a_stability_addr_ctrl: assert($stable(PADDR) && $stable(PWRITE) && $stable(PPROT));

                // For writes, data and strobes must also be stable.
                if ($past(is_write)) begin
                    a_stability_wdata: assert($stable(PWDATA) && $stable(PSTRB));
                end
            end

            // --- PSLVERR checks ---
            // PSLVERR is a registered output from the slave, so its value reflects the
            // conditions of the previous cycle. These checks must be delayed by one cycle.

            // A read with non-zero PSTRB in the previous cycle must raise an error now.
            if ($past(is_access && is_read && PSTRB != 4'b0000)) begin
                a_read_with_strb_is_error: assert(PSLVERR);
            end

            // A valid read in the previous cycle should not raise an error now.
            if ($past(is_access && is_read && PSTRB == 4'b0000)) begin
                a_valid_read_no_err: assert(!PSLVERR);
            end

            // A valid write in the previous cycle should not raise an error now.
            if ($past(is_access && is_write)) begin
                a_valid_write_no_err: assert(!PSLVERR);
            end

            // --- Data Integrity Check ---
            // This check will FAIL due to the slave's faulty write logic.
            // If a valid read completes on the watched address, check PRDATA against our model.
            if (transfer_done && is_read && PSTRB == 4'b0000 && is_watched_addr) begin
                if (r_byte_valid[0]) a_data_integrity_b0: assert(PRDATA[7:0]   == r_expected_data[7:0]);
                if (r_byte_valid[1]) a_data_integrity_b1: assert(PRDATA[15:8]  == r_expected_data[15:8]);
                if (r_byte_valid[2]) a_data_integrity_b2: assert(PRDATA[23:16] == r_expected_data[23:16]);
                if (r_byte_valid[3]) a_data_integrity_b3: assert(PRDATA[31:24] == r_expected_data[31:24]);
            end
        end

        // --- Cover Properties (Reachability) ---
        c_reset_held: cover(!PRESETn);
        c_reset_released: cover($rose(PRESETn));

        if (PRESETn) begin
            c_setup: cover(is_setup);
            c_access: cover(is_access);
            c_write_complete: cover(transfer_done && is_write);
            c_read_complete: cover(transfer_done && is_read);
            c_back_to_back: cover($past(is_access && PREADY) && is_setup);
            c_zero_strobe_write: cover(transfer_done && is_write && PSTRB == 4'b0000);
            
            // Cover bus stability check antecedents
            c_setup_to_access: cover(is_access && $past(is_setup));
            c_setup_to_access_write: cover(is_access && $past(is_setup) && $past(is_write));

            // Cover the trigger for the invalid read error check
            c_read_err_trigger: cover(is_access && is_read && PSTRB != 4'b0000);

            // Cover a read-after-write sequence to exercise the data integrity check
            if (transfer_done && is_read && is_watched_addr && |r_byte_valid) begin
                c_read_after_write: cover(1);
            end

            // Cover a partial overwrite sequence
            if (transfer_done && is_read && is_watched_addr && r_byte_valid != 4'b0 && r_byte_valid != 4'b1111) begin
                c_partial_overwrite: cover(1);
            end
        end
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