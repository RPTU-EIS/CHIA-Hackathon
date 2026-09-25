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

The first line must contain exactly one of:
EXPLORE_MORE  when useful independent properties are missing; explain them and
              return one complete updated apb4.sby.
EXPLORE_DONE  when the design brief and the areas suggested by known bugs are
              already covered; explain why and return no files.

Accumulated bug reports:
Solver failed assertions: apb4_formal.sv, check_assert_apb4_formal_sv_165_360, check_assert_apb4_formal_sv_173_361, check_assert_apb4_formal_sv_175_363, check_assert_apb4_formal_sv_177_365, check_assert_apb4_formal_sv_179_367

BUG_REPORT
This BMC run resulted in assertion failures that expose two distinct and significant bugs in the `APB_Slave` RTL. The formal properties that failed are valid representations of the APB protocol requirements and do not need to be modified.

### Diagnosis of Failures

#### Group 1: Data Integrity Failures

*   **Failed Assertions:**
    *   `apb4_formal.sv:173.51-173.96`: `if ($past(shadow_valid_bytes[0])) assert(PRDATA[7:0] == $past(shadow_mem[7:0]));`
    *   `apb4_formal.sv:175.51-175.98`: `if ($past(shadow_valid_bytes[1])) assert(PRDATA[15:8] == $past(shadow_mem[15:8]));`
    *   `apb4_formal.sv:177.51-177.100`: `if ($past(shadow_valid_bytes[2])) assert(PRDATA[23:16] == $past(shadow_mem[23:16]));`
    *   `apb4_formal.sv:179.51-179.100`: `if ($past(shadow_valid_bytes[3])) assert(PRDATA[31:24] == $past(shadow_mem[31:24]));`
*   **Root Cause:** RTL Bug in `APB_Slave.v`.
*   **Analysis:** These four assertions verify that a read operation returns the correctly written data on a per-byte basis. The test harness's scoreboard (`shadow_mem`) correctly models a byte-masked write, where only the bytes selected by `PSTRB` are updated, and the others are preserved.
    The BMC tool found a counterexample where a partial write (e.g., to one byte) follows a full-word write. On a subsequent read, the slave returns incorrect data for the bytes that should have been preserved, causing the assertions to fail.
*   **Faulty RTL Location:** `APB_Slave.v`, lines 28-48. The `case (PSTRB)` statement for write operations is implemented incorrectly. For any partial write, instead of performing a masked update that preserves the unselected bytes, the slave overwrites the entire 32-bit memory location with a new value constructed from parts of `PWDATA` and either zero-padding or sign-extension. For example, a write with `PSTRB=4'b0010` (byte 1) executes `Cache[PADDR] <= {{24{PWDATA[15]}}, PWDATA[15:8], 8'h00};`, which incorrectly modifies all 32 bits of the memory word.
*   **Suggested Prose Repair:** The write logic in `APB_Slave.v` should be modified to implement true byte-enables. Instead of a large `case` statement, use four parallel `if` statements, one for each byte strobe, to update only the corresponding byte lane in the `Cache` memory. For example:
    ```verilog
    if (PWRITE) begin
        if (PSTRB[0]) Cache[PADDR][7:0]   <= PWDATA[7:0];
        if (PSTRB[1]) Cache[PADDR][15:8]  <= PWDATA[15:8];
        if (PSTRB[2]) Cache[PADDR][23:16] <= PWDATA[23:16];
        if (PSTRB[3]) Cache[PADDR][31:24] <= PWDATA[31:24];
        // ...
    end
    ```

#### Group 2: Address Decoding Error Failure

*   **Failed Assertion:**
    *   `apb4_formal.sv:165.17-165.32`: `assert(PSLVERR);`
*   **Root Cause:** RTL Bug in `APB_Slave.v`.
*   **Analysis:** This assertion, `a_slverr_on_bad_addr`, correctly requires the slave to generate an error (`PSLVERR=1`) when an access targets an address outside its valid 1024-word range (`PADDR >= 1024`). The assertion failed at step 4, indicating that in the ACCESS phase following a SETUP phase to an out-of-bounds address, `PSLVERR` was 0 instead of the required 1.
*   **Faulty RTL Location:** `APB_Slave.v`, lines 24-58. The main `always` block that handles read/write logic is conditioned on `if (PSEL)`, but it contains no logic to validate the incoming `PADDR`. It blindly uses the lower 10 bits of `PADDR` to access its internal memory and unconditionally sets `PSLVERR <= 0` on all writes and on reads that have `PSTRB=0`. This violates the APB protocol, which requires an error response for accesses to unsupported addresses.
*   **Suggested Prose Repair:** Add an address check in `APB_Slave.v` at the beginning of the transaction logic. Before processing a read or write, the slave should check if `PADDR >= 1024` (or `PADDR[31:10] != 0`). If the address is out of bounds, the slave must assert `PSLVERR <= 1` and suppress any memory access for that transaction.

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
/*
 * Formal verification harness for APB4 master/slave system.
 *
 * This harness includes properties to check:
 * 1. Reset Behavior: Master and slave reset to a known state.
 * 2. Protocol Sequencing: Master follows IDLE-SETUP-ACCESS states correctly.
 * 3. Signal Stability: Address and control signals are stable during a transfer.
 * 4. Error Handling: Slave correctly asserts PSLVERR on invalid reads (bad PSTRB)
 *    and is checked for correct handling of out-of-range addresses (will fail).
 * 5. Data Integrity: A read after a write returns the correct data. This check
 *    uses a behavioral model of byte-strobed writes and will fail due to the
 *    slave's buggy write implementation and aliased address corruption.
 */
module apb4_formal(input wire PCLK);

    // DUT inputs from the environment/requester
    (* anyseq *) reg SWRITE;
    (* anyseq *) reg [31:0] SADDR, SWDATA;
    (* anyseq *) reg [3:0] SSTRB;
    (* anyseq *) reg [2:0] SPROT;
    (* anyseq *) reg transfer;

    // Reset generation
    reg first_cycle = 1'b1;
    wire PRESETn = !first_cycle;
    reg past_valid = 1'b0;

    always @(posedge PCLK) begin
        first_cycle <= 1'b0;
        past_valid <= 1'b1;
    end

    // DUT outputs and internal APB signals
    wire PSEL, PENABLE, PWRITE;
    wire [31:0] PADDR, PWDATA;
    wire [3:0] PSTRB;
    wire [2:0] PPROT;
    wire PREADY, PSLVERR;
    wire [31:0] PRDATA;

    // DUT instantiation (Master and Slave are instantiated directly)
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

    // Data integrity checker state
    // A single address is watched to check write/read consistency.
    (* anyconst *) reg [9:0] watched_addr;
    reg [31:0] shadow_mem;
    reg [3:0] shadow_valid_bytes;
    reg read_access_to_watched_addr = 1'b0;
    reg has_been_written_in_bounds = 1'b0;

    initial begin
        // Ensure watched_addr is within the valid slave range
        assume(watched_addr < 1024);
    end

    // Scoreboard logic to track writes to the watched address
    always @(posedge PCLK) begin
        if (!PRESETn) begin
            shadow_valid_bytes <= 4'b0;
            has_been_written_in_bounds <= 1'b0;
        end else begin
            // Update shadow memory on a valid, in-bounds write during the SETUP phase.
            // This models the correct, expected behavior, ignoring out-of-bounds writes.
            if (PSEL && !PENABLE && PWRITE && (PADDR[9:0] == watched_addr) && (PADDR < 1024)) begin
                has_been_written_in_bounds <= 1'b1;
                if (PSTRB[0]) begin
                    shadow_mem[7:0] <= PWDATA[7:0];
                    shadow_valid_bytes[0] <= 1'b1;
                end
                if (PSTRB[1]) begin
                    shadow_mem[15:8] <= PWDATA[15:8];
                    shadow_valid_bytes[1] <= 1'b1;
                end
                if (PSTRB[2]) begin
                    shadow_mem[23:16] <= PWDATA[23:16];
                    shadow_valid_bytes[2] <= 1'b1;
                end
                if (PSTRB[3]) begin
                    shadow_mem[31:24] <= PWDATA[31:24];
                    shadow_valid_bytes[3] <= 1'b1;
                end
            end
        end
    end

    // Properties for reset state
    always @(posedge PCLK) begin
        // Master has combinatorial reset outputs. Check them during the reset cycle.
        if (first_cycle) begin
            // a_reset_master_outputs: Master outputs must be zero during reset.
            assert(PSEL == 1'b0);
            assert(PENABLE == 1'b0);
            assert(PWRITE == 1'b0);
            assert(PADDR == 32'h0);
            assert(PWDATA == 32'h0);
            assert(PSTRB == 4'h0);
            assert(PPROT == 3'h0);
        end

        // Slave has synchronous reset outputs. Check them on the cycle after reset is asserted.
        if (past_valid && $past(!PRESETn)) begin
            // a_reset_slave_outputs: Slave registered outputs must be zero after reset.
            assert(PRDATA == 32'h0);
            assert(PSLVERR == 1'b0);
        end
    end

    // Properties (assumptions, assertions)
    always @(posedge PCLK) begin
        // Update state for delayed data integrity check.
        if (!PRESETn) begin
            read_access_to_watched_addr <= 1'b0;
        end else begin
            // Trigger is a valid read access to the watched address.
            read_access_to_watched_addr <= (PSEL && PENABLE && !PWRITE && (PADDR[9:0] == watched_addr) && PSTRB == 4'b0000);
        end

        // Checks below are only valid after reset has been released
        if (past_valid && $past(PRESETn)) begin
            // --- Protocol Sequencing and Stability ---
            // a_penable_after_psel: PENABLE should only be asserted in the cycle after PSEL is asserted.
            if ($rose(PENABLE)) begin
                assert($past(PSEL));
            end

            // a_penable_with_psel: PENABLE must not be high if PSEL is low.
            assert(!(PENABLE && !PSEL));

            // a_stable_addr_ctrl: Address and control must be stable from SETUP to ACCESS.
            if ($past(PSEL && !PENABLE)) begin
                assert($stable(PADDR));
                assert($stable(PWRITE));
                assert($stable(PPROT));
                assert($stable(PSTRB));
            end

            // a_stable_wdata: Write data must be stable during a write transfer from SETUP to ACCESS.
            if ($past(PSEL && !PENABLE && PWRITE)) begin
                assert($stable(PWDATA));
            end

            // a_pready_no_wait: Slave must assert PREADY in ACCESS phase (no wait states).
            assert(PREADY == (PSEL && PENABLE));

            // --- Error Response ---
            // a_slverr_on_bad_read_strobe: A read with PSTRB!=0 must cause a slave error.
            if ($past(PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0)) begin
                assert(PSLVERR);
            end

            // a_no_slverr_on_good_write: A valid write should not cause a slave error.
            if ($past(PSEL && !PENABLE && PWRITE && PADDR < 1024)) begin
                assert(!PSLVERR);
            end
            
            // a_slverr_on_bad_addr: An access to an out-of-bounds address should cause a slave error.
            // This will FAIL, revealing a bug in the slave.
            if ($past(PSEL && !PENABLE && PADDR >= 1024)) begin
                assert(PSLVERR);
            end

            // --- Data Integrity ---
            // On a valid read from watched_addr, check PRDATA on the next cycle, as it's a registered output.
            // This will FAIL due to the slave's buggy write logic and/or aliased address corruption.
            if ($past(read_access_to_watched_addr)) begin
                // a_data_byte0: Check byte 0 if it has been written
                if ($past(shadow_valid_bytes[0])) assert(PRDATA[7:0] == $past(shadow_mem[7:0]));
                // a_data_byte1: Check byte 1 if it has been written
                if ($past(shadow_valid_bytes[1])) assert(PRDATA[15:8] == $past(shadow_mem[15:8]));
                // a_data_byte2: Check byte 2 if it has been written
                if ($past(shadow_valid_bytes[2])) assert(PRDATA[23:16] == $past(shadow_mem[23:16]));
                // a_data_byte3: Check byte 3 if it has been written
                if ($past(shadow_valid_bytes[3])) assert(PRDATA[31:24] == $past(shadow_mem[31:24]));
            end
        end
    end

    // Cover Statements
    always @(posedge PCLK) begin
        // c_reset_asserted: Cover reset being asserted on the first cycle.
        if (first_cycle) begin
             cover(!PRESETn);
        end
        // c_reset_released: Cover reset being released after the first cycle.
        if (past_valid && $past(!PRESETn)) begin
            cover(PRESETn);
        end
        
        // Cover properties after reset is released
        if (past_valid && PRESETn) begin
            // c_setup_phase: Cover a SETUP phase.
            cover(PSEL && !PENABLE);
            // c_access_phase: Cover an ACCESS phase.
            cover(PSEL && PENABLE);
            // c_write_completes: Cover a completed write.
            cover(PSEL && PENABLE && PREADY && PWRITE);
            // c_read_completes: Cover a completed read.
            cover(PSEL && PENABLE && PREADY && !PWRITE);
            // c_back_to_back: Cover a back-to-back transfer.
            cover($past(PSEL && PENABLE && PREADY) && (PSEL && !PENABLE));

            // c_bad_read_strobe_trigger: Cover trigger for bad read strobe error check.
            cover(PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0);
            
            // c_bad_addr_trigger: Cover trigger for bad address error check.
            cover(PSEL && !PENABLE && PADDR >= 1024);

            // c_aliased_write_trigger: Cover an out-of-bounds write to a previously written valid address.
            cover((PSEL && !PENABLE && PWRITE && PADDR >= 1024 && PADDR[9:0] == watched_addr) && has_been_written_in_bounds);

            // c_read_after_full_write: Cover reading a fully written word.
            cover((PSEL && PENABLE && !PWRITE && (PADDR[9:0] == watched_addr)) && (&shadow_valid_bytes));

            // c_read_after_partial_write: Cover reading after a partial write.
            cover((PSEL && PENABLE && !PWRITE && (PADDR[9:0] == watched_addr)) && (|shadow_valid_bytes) && !(&shadow_valid_bytes));

            // c_overwrite_byte: Cover a byte write after a full write.
            cover((&shadow_valid_bytes) &&
                  (PSEL && !PENABLE && PWRITE && (PADDR[9:0] == watched_addr) &&
                   (PSTRB == 4'b0001 || PSTRB == 4'b0010 || PSTRB == 4'b0100 || PSTRB == 4'b1000)));
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