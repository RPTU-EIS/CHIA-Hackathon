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

// --- DUT inputs (driven symbolically) ---
// These are top-level registers and are not driven by any procedural block.
// The formal tool will treat them as unconstrained (symbolic) inputs on every cycle.
reg PRESETn;
reg SWRITE;
reg [31:0] SADDR;
reg [31:0] SWDATA;
reg [3:0] SSTRB;
reg [2:0] SPROT;
reg transfer;

// --- DUT-internal wires ---
wire PSEL, PENABLE, PWRITE;
wire [31:0] PADDR, PWDATA;
wire [3:0] PSTRB;
wire [2:0] PPROT;
wire PREADY, PSLVERR;
wire [31:0] PRDATA;

// --- DUT Instantiation ---
APB_Master Master (
    .PCLK(PCLK), .PRESETn(PRESETn), .SWRITE(SWRITE), .SADDR(SADDR), .SWDATA(SWDATA),
    .SSTRB(SSTRB), .SPROT(SPROT), .transfer(transfer), .PSEL(PSEL), .PENABLE(PENABLE),
    .PWRITE(PWRITE), .PADDR(PADDR), .PWDATA(PWDATA), .PSTRB(PSTRB), .PPROT(PPROT),
    .PREADY(PREADY), .PSLVERR(PSLVERR)
);

APB_Slave Slave (
    .PCLK(PCLK), .PRESETn(PRESETn), .PSEL(PSEL), .PENABLE(PENABLE), .PWRITE(PWRITE),
    .PADDR(PADDR), .PWDATA(PWDATA), .PSTRB(PSTRB), .PPROT(PPROT),
    .PREADY(PREADY), .PSLVERR(PSLVERR), .PRDATA(PRDATA)
);

// --- Harness State and Environment ---
reg first_cycle_ff = 1;
reg past_valid_ff = 0;

always @(posedge PCLK) begin
    first_cycle_ff <= 0;
    past_valid_ff <= 1;
end

always @(*) begin
    // Assumption: PRESETn is active low for the first cycle only.
    PRESETn = !first_cycle_ff;
end

// --- Assertions and Covers ---
always @(posedge PCLK) begin
    // --- Reset Properties ---
    // Slave reset is synchronous, outputs should be 0 on the cycle after reset is asserted.
    if (past_valid_ff && $past(!PRESETn)) begin
        a_slave_reset_outputs: assert(PSLVERR == 0 && PRDATA == 0);
    end
    
    // After reset is released, master should be in IDLE state.
    if (past_valid_ff && $rose(PRESETn)) begin
        a_master_reset_state: assert(PSEL == 0 && PENABLE == 0);
    end

    // --- Protocol Properties ---
    // Master must hold control signals stable from SETUP through ACCESS.
    if (past_valid_ff && $past(PSEL && !PENABLE) && (PSEL && PENABLE)) begin // SETUP -> ACCESS
        a_addr_stable: assert($stable(PADDR));
        a_pwrite_stable: assert($stable(PWRITE));
        if ($past(PWRITE)) begin
            a_pwdata_stable: assert($stable(PWDATA));
            a_pstrb_stable: assert($stable(PSTRB));
        end
    end

    // Slave PREADY must be correct.
    a_pready_logic: assert(PREADY == (PSEL && PENABLE));

    // Slave asserts PSLVERR on read with non-zero PSTRB.
    // The slave RTL checks this during SETUP. The error is registered and appears during ACCESS.
    if (past_valid_ff && $past(PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0000)) begin
        a_pslverr_on_bad_read: assert(PSLVERR == 1);
    end
    
    // Slave must not assert PSLVERR on valid transfers.
    if (past_valid_ff && $past(PSEL && PENABLE && PREADY)) begin
        if ($past(!PWRITE && PSTRB == 4'b0000 && PADDR < 1024)) begin
            a_pslverr_on_good_read: assert(PSLVERR == 0);
        end
        if ($past(PWRITE && PADDR < 1024)) begin
            a_pslverr_on_good_write: assert(PSLVERR == 0);
        end
    end
end

// --- Data Integrity Checker ---
(* anyconst *) reg [9:0] watched_addr;
reg [31:0] expected_data;
reg [3:0] byte_valid;

always @(posedge PCLK) begin
    if (!PRESETn) begin
        byte_valid <= 4'b0000;
        // Memory is uninitialized, so expected_data is not initialized.
    end else begin
        // A write completed to the watched address in the ACCESS phase.
        // Update our model of the memory.
        if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024) begin
            expected_data <= {
                PSTRB[3] ? PWDATA[31:24] : expected_data[31:24],
                PSTRB[2] ? PWDATA[23:16] : expected_data[23:16],
                PSTRB[1] ? PWDATA[15:8]  : expected_data[15:8],
                PSTRB[0] ? PWDATA[7:0]   : expected_data[7:0]
            };
            byte_valid <= byte_valid | PSTRB;
        end

        // A read completed from the watched address. Data is available on PRDATA.
        // Compare against expected data from the *previous* cycle.
        // This is expected to FAIL for partial writes due to the bug in the slave.
        if (past_valid_ff && $past(PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000 && PADDR[9:0] == watched_addr && PADDR < 1024)) begin
            if ($past(byte_valid[0])) begin a_data_byte0: assert(PRDATA[7:0]   == $past(expected_data[7:0]));   end
            if ($past(byte_valid[1])) begin a_data_byte1: assert(PRDATA[15:8]  == $past(expected_data[15:8]));  end
            if ($past(byte_valid[2])) begin a_data_byte2: assert(PRDATA[23:16] == $past(expected_data[23:16])); end
            if ($past(byte_valid[3])) begin a_data_byte3: assert(PRDATA[31:24] == $past(expected_data[31:24])); end
        end
    end
end

// --- Cover points ---
always @(posedge PCLK) begin
    if (!PRESETn) c_reset_active: cover(1);
    if (past_valid_ff && $rose(PRESETn)) c_reset_release: cover(1);
    
    // Master FSM phases
    if (PSEL && !PENABLE) c_setup_phase: cover(1);
    if (PSEL && PENABLE)  c_access_phase: cover(1);
    
    // Transfer completion
    if (PSEL && PENABLE && PREADY && PWRITE) c_write_completes: cover(1);
    if (PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000) c_read_completes: cover(1);
    
    // Back-to-back transfer (ACCESS -> SETUP)
    if (past_valid_ff && $past(PREADY && transfer) && (PSEL && !PENABLE)) c_back_to_back: cover(1);

    // Error condition trigger
    if (PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0000) c_read_error_trigger: cover(1);
    
    // Data integrity covers
    if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024) c_data_write: cover(1);
    if (past_valid_ff && $past(PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000 && PADDR[9:0] == watched_addr && PADDR < 1024 && |byte_valid)) c_data_readback: cover(1);
    
    // Cover a partial overwrite of an already written word
    if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024 && |byte_valid && byte_valid != 4'b1111 && PSTRB != byte_valid) begin
        c_partial_overwrite: cover(1);
    end
end

endmodule


Build/format/timeout diagnostics:
Key diagnostics:
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWRITE is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [31] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [30] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [29] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [28] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [27] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [26] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [25] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [24] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [23] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [22] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [21] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [20] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [19] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [18] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [17] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [16] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [15] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [14] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [13] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [12] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [11] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [10] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [9] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [8] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [7] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [6] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [5] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [4] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [3] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [2] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [1] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SADDR [0] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [31] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [30] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [29] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [28] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [27] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [26] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [25] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [24] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [23] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [22] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [21] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [20] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [19] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [18] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [17] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [16] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [15] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [14] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [13] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [12] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [11] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [10] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [9] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [8] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [7] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [6] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [5] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [4] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: Wire apb4_formal.\SWDATA [3] is used but has no driver.
SBY  5:21:28 [apb4_prep] base: Warning: 
Already reached goals:

Numbered emitted apb4_formal.sv (solver line numbers):
1: module apb4_formal (
2:     input wire PCLK
3: );
4: 
5: // --- DUT inputs (driven symbolically) ---
6: // These are top-level registers and are not driven by any procedural block.
7: // The formal tool will treat them as unconstrained (symbolic) inputs on every cycle.
8: reg PRESETn;
9: reg SWRITE;
10: reg [31:0] SADDR;
11: reg [31:0] SWDATA;
12: reg [3:0] SSTRB;
13: reg [2:0] SPROT;
14: reg transfer;
15: 
16: // --- DUT-internal wires ---
17: wire PSEL, PENABLE, PWRITE;
18: wire [31:0] PADDR, PWDATA;
19: wire [3:0] PSTRB;
20: wire [2:0] PPROT;
21: wire PREADY, PSLVERR;
22: wire [31:0] PRDATA;
23: 
24: // --- DUT Instantiation ---
25: APB_Master Master (
26:     .PCLK(PCLK), .PRESETn(PRESETn), .SWRITE(SWRITE), .SADDR(SADDR), .SWDATA(SWDATA),
27:     .SSTRB(SSTRB), .SPROT(SPROT), .transfer(transfer), .PSEL(PSEL), .PENABLE(PENABLE),
28:     .PWRITE(PWRITE), .PADDR(PADDR), .PWDATA(PWDATA), .PSTRB(PSTRB), .PPROT(PPROT),
29:     .PREADY(PREADY), .PSLVERR(PSLVERR)
30: );
31: 
32: APB_Slave Slave (
33:     .PCLK(PCLK), .PRESETn(PRESETn), .PSEL(PSEL), .PENABLE(PENABLE), .PWRITE(PWRITE),
34:     .PADDR(PADDR), .PWDATA(PWDATA), .PSTRB(PSTRB), .PPROT(PPROT),
35:     .PREADY(PREADY), .PSLVERR(PSLVERR), .PRDATA(PRDATA)
36: );
37: 
38: // --- Harness State and Environment ---
39: reg first_cycle_ff = 1;
40: reg past_valid_ff = 0;
41: 
42: always @(posedge PCLK) begin
43:     first_cycle_ff <= 0;
44:     past_valid_ff <= 1;
45: end
46: 
47: always @(*) begin
48:     // Assumption: PRESETn is active low for the first cycle only.
49:     PRESETn = !first_cycle_ff;
50: end
51: 
52: // --- Assertions and Covers ---
53: always @(posedge PCLK) begin
54:     // --- Reset Properties ---
55:     // Slave reset is synchronous, outputs should be 0 on the cycle after reset is asserted.
56:     if (past_valid_ff && $past(!PRESETn)) begin
57:         a_slave_reset_outputs: assert(PSLVERR == 0 && PRDATA == 0);
58:     end
59:     
60:     // After reset is released, master should be in IDLE state.
61:     if (past_valid_ff && $rose(PRESETn)) begin
62:         a_master_reset_state: assert(PSEL == 0 && PENABLE == 0);
63:     end
64: 
65:     // --- Protocol Properties ---
66:     // Master must hold control signals stable from SETUP through ACCESS.
67:     if (past_valid_ff && $past(PSEL && !PENABLE) && (PSEL && PENABLE)) begin // SETUP -> ACCESS
68:         a_addr_stable: assert($stable(PADDR));
69:         a_pwrite_stable: assert($stable(PWRITE));
70:         if ($past(PWRITE)) begin
71:             a_pwdata_stable: assert($stable(PWDATA));
72:             a_pstrb_stable: assert($stable(PSTRB));
73:         end
74:     end
75: 
76:     // Slave PREADY must be correct.
77:     a_pready_logic: assert(PREADY == (PSEL && PENABLE));
78: 
79:     // Slave asserts PSLVERR on read with non-zero PSTRB.
80:     // The slave RTL checks this during SETUP. The error is registered and appears during ACCESS.
81:     if (past_valid_ff && $past(PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0000)) begin
82:         a_pslverr_on_bad_read: assert(PSLVERR == 1);
83:     end
84:     
85:     // Slave must not assert PSLVERR on valid transfers.
86:     if (past_valid_ff && $past(PSEL && PENABLE && PREADY)) begin
87:         if ($past(!PWRITE && PSTRB == 4'b0000 && PADDR < 1024)) begin
88:             a_pslverr_on_good_read: assert(PSLVERR == 0);
89:         end
90:         if ($past(PWRITE && PADDR < 1024)) begin
91:             a_pslverr_on_good_write: assert(PSLVERR == 0);
92:         end
93:     end
94: end
95: 
96: // --- Data Integrity Checker ---
97: (* anyconst *) reg [9:0] watched_addr;
98: reg [31:0] expected_data;
99: reg [3:0] byte_valid;
100: 
101: always @(posedge PCLK) begin
102:     if (!PRESETn) begin
103:         byte_valid <= 4'b0000;
104:         // Memory is uninitialized, so expected_data is not initialized.
105:     end else begin
106:         // A write completed to the watched address in the ACCESS phase.
107:         // Update our model of the memory.
108:         if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024) begin
109:             expected_data <= {
110:                 PSTRB[3] ? PWDATA[31:24] : expected_data[31:24],
111:                 PSTRB[2] ? PWDATA[23:16] : expected_data[23:16],
112:                 PSTRB[1] ? PWDATA[15:8]  : expected_data[15:8],
113:                 PSTRB[0] ? PWDATA[7:0]   : expected_data[7:0]
114:             };
115:             byte_valid <= byte_valid | PSTRB;
116:         end
117: 
118:         // A read completed from the watched address. Data is available on PRDATA.
119:         // Compare against expected data from the *previous* cycle.
120:         // This is expected to FAIL for partial writes due to the bug in the slave.
121:         if (past_valid_ff && $past(PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000 && PADDR[9:0] == watched_addr && PADDR < 1024)) begin
122:             if ($past(byte_valid[0])) begin a_data_byte0: assert(PRDATA[7:0]   == $past(expected_data[7:0]));   end
123:             if ($past(byte_valid[1])) begin a_data_byte1: assert(PRDATA[15:8]  == $past(expected_data[15:8]));  end
124:             if ($past(byte_valid[2])) begin a_data_byte2: assert(PRDATA[23:16] == $past(expected_data[23:16])); end
125:             if ($past(byte_valid[3])) begin a_data_byte3: assert(PRDATA[31:24] == $past(expected_data[31:24])); end
126:         end
127:     end
128: end
129: 
130: // --- Cover points ---
131: always @(posedge PCLK) begin
132:     if (!PRESETn) c_reset_active: cover(1);
133:     if (past_valid_ff && $rose(PRESETn)) c_reset_release: cover(1);
134:     
135:     // Master FSM phases
136:     if (PSEL && !PENABLE) c_setup_phase: cover(1);
137:     if (PSEL && PENABLE)  c_access_phase: cover(1);
138:     
139:     // Transfer completion
140:     if (PSEL && PENABLE && PREADY && PWRITE) c_write_completes: cover(1);
141:     if (PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000) c_read_completes: cover(1);
142:     
143:     // Back-to-back transfer (ACCESS -> SETUP)
144:     if (past_valid_ff && $past(PREADY && transfer) && (PSEL && !PENABLE)) c_back_to_back: cover(1);
145: 
146:     // Error condition trigger
147:     if (PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0000) c_read_error_trigger: cover(1);
148:     
149:     // Data integrity covers
150:     if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024) c_data_write: cover(1);
151:     if (past_valid_ff && $past(PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000 && PADDR[9:0] == watched_addr && PADDR < 1024 && |byte_valid)) c_data_readback: cover(1);
152:     
153:     // Cover a partial overwrite of an already written word
154:     if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024 && |byte_valid && byte_valid != 4'b1111 && PSTRB != byte_valid) begin
155:         c_partial_overwrite: cover(1);
156:     end
157: end
158: 
159: endmodule
Log tail:
DDR [14] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [13] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [12] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [11] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [10] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [9] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [8] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [7] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [6] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [5] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [4] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [3] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [2] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [1] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [0] is used but has no driver.
Warning: Wire apb4_formal.\transfer is used but has no driver.
Found and reported 73 problems.

6. Executing CHECK pass (checking for obvious problems).
Checking module APB_Master...
Checking module APB_Slave...
Checking module apb4_formal...
Warning: Wire apb4_formal.\SWRITE is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [31] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [30] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [29] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [28] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [27] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [26] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [25] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [24] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [23] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [22] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [21] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [20] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [19] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [18] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [17] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [16] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [15] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [14] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [13] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [12] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [11] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [10] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [9] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [8] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [7] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [6] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [5] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [4] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [3] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [2] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [1] is used but has no driver.
Warning: Wire apb4_formal.\SWDATA [0] is used but has no driver.
Warning: Wire apb4_formal.\SSTRB [3] is used but has no driver.
Warning: Wire apb4_formal.\SSTRB [2] is used but has no driver.
Warning: Wire apb4_formal.\SSTRB [1] is used but has no driver.
Warning: Wire apb4_formal.\SSTRB [0] is used but has no driver.
Warning: Wire apb4_formal.\SPROT [2] is used but has no driver.
Warning: Wire apb4_formal.\SPROT [1] is used but has no driver.
Warning: Wire apb4_formal.\SPROT [0] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [31] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [30] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [29] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [28] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [27] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [26] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [25] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [24] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [23] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [22] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [21] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [20] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [19] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [18] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [17] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [16] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [15] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [14] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [13] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [12] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [11] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [10] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [9] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [8] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [7] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [6] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [5] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [4] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [3] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [2] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [1] is used but has no driver.
Warning: Wire apb4_formal.\SADDR [0] is used but has no driver.
Warning: Wire apb4_formal.\transfer is used but has no driver.
Found and reported 73 problems.
ERROR: Found 73 problems in 'check -assert'.


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