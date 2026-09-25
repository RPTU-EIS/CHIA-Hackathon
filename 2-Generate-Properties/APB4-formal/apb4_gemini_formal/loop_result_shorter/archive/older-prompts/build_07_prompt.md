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
    input wire PCLK,
    // --- DUT inputs (driven symbolically) ---
    input wire SWRITE,
    input wire [31:0] SADDR,
    input wire [31:0] SWDATA,
    input wire [3:0] SSTRB,
    input wire [2:0] SPROT,
    input wire transfer
);

// --- Internal wires ---
// PRESETn is driven internally to create a specific reset sequence.
wire PRESETn;

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

// Assumption: PRESETn is active low for the first cycle only.
assign PRESETn = !first_cycle_ff;

// Requester contract: Do not start a new transfer while reset is active.
always @(posedge PCLK) begin
    if (!PRESETn) begin
        assume(transfer == 0);
    end
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

    // Slave PREADY must be correct (no wait states).
    a_pready_logic: assert(PREADY == (PSEL && PENABLE));

    // Check PSLVERR logic. The slave registers the error status during the SETUP phase.
    // The result (PSLVERR) is then visible during the ACCESS phase.
    if (past_valid_ff && $past(PSEL && !PENABLE) && (PSEL && PENABLE)) begin // If transitioning SETUP -> ACCESS
        // Check PSLVERR based on conditions from previous (SETUP) cycle.
        
        // An access to an invalid address should generate an error.
        if ($past(PADDR >= 1024)) begin
            a_pslverr_on_bad_addr: assert(PSLVERR == 1);
        end
        // An invalid read (PWRITE=0, PSTRB!=0) to a valid address should generate an error.
        else if ($past(!PWRITE && PSTRB != 4'b0000)) begin
             a_pslverr_on_bad_read: assert(PSLVERR == 1);
        end
        // A valid transfer to a valid address must not generate an error.
        else {
            if ($past(!PWRITE)) begin
                a_pslverr_on_good_read: assert(PSLVERR == 0);
            end
            if ($past(PWRITE)) begin
                a_pslverr_on_good_write: assert(PSLVERR == 0);
            end
        }
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
        // Memory is uninitialized, so expected_data is not initialized.
    end else begin
        // A write completed to the watched address in the ACCESS phase. Update our model.
        if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024) begin
            expected_data <= {
                PSTRB[3] ? PWDATA[31:24] : expected_data[31:24],
                PSTRB[2] ? PWDATA[23:16] : expected_data[23:16],
                PSTRB[1] ? PWDATA[15:8]  : expected_data[15:8],
                PSTRB[0] ? PWDATA[7:0]   : expected_data[7:0]
            };
            byte_valid <= byte_valid | PSTRB;
        end

        // A legal read completes in the ACCESS phase. Data is available on PRDATA.
        // The value on PRDATA was registered by the slave at the end of the SETUP phase.
        // The expected_data reflects the state of memory from the last completed write.
        // This check will fail if the slave's byte-lane write logic is incorrect.
        if (PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000 && PADDR[9:0] == watched_addr && PADDR < 1024) begin
            if (byte_valid[0]) begin a_data_byte0: assert(PRDATA[7:0]   == expected_data[7:0]);   end
            if (byte_valid[1]) begin a_data_byte1: assert(PRDATA[15:8]  == expected_data[15:8]);  end
            if (byte_valid[2]) begin a_data_byte2: assert(PRDATA[23:16] == expected_data[23:16]); end
            if (byte_valid[3]) begin a_data_byte3: assert(PRDATA[31:24] == expected_data[31:24]); end
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
    if (PSEL && !PENABLE && PADDR >= 1024) c_addr_error_trigger: cover(1);
    if (PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0000) c_read_error_trigger: cover(1);
    
    // Data integrity covers
    if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024) c_data_write: cover(1);
    if (PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000 && PADDR[9:0] == watched_addr && |byte_valid) c_data_readback: cover(1);
    
    // Cover a partial overwrite of an already written word
    if (past_valid_ff && $past(PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr) && $past(|byte_valid) && (byte_valid != 4'b1111) &&
        (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr)) begin
        c_partial_overwrite: cover(1);
    end
end

endmodule


Build/format/timeout diagnostics:
Key diagnostics:
SBY  5:25:35 [apb4_prep] base: apb4_formal.sv:99: ERROR: syntax error, unexpected TOK_IF
SBY  5:25:35 [apb4_prep] base: task failed. ERROR.
SBY  5:25:35 [apb4_prep] DONE (ERROR, rc=16)
SBY  5:25:35 The following tasks failed: ['prep']
apb4_formal.sv:99: ERROR: syntax error, unexpected TOK_IF
Already reached goals:

Numbered emitted apb4_formal.sv (solver line numbers):
1: module apb4_formal (
2:     input wire PCLK,
3:     // --- DUT inputs (driven symbolically) ---
4:     input wire SWRITE,
5:     input wire [31:0] SADDR,
6:     input wire [31:0] SWDATA,
7:     input wire [3:0] SSTRB,
8:     input wire [2:0] SPROT,
9:     input wire transfer
10: );
11: 
12: // --- Internal wires ---
13: // PRESETn is driven internally to create a specific reset sequence.
14: wire PRESETn;
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
47: // Assumption: PRESETn is active low for the first cycle only.
48: assign PRESETn = !first_cycle_ff;
49: 
50: // Requester contract: Do not start a new transfer while reset is active.
51: always @(posedge PCLK) begin
52:     if (!PRESETn) begin
53:         assume(transfer == 0);
54:     end
55: end
56: 
57: // --- Assertions and Covers ---
58: always @(posedge PCLK) begin
59:     // --- Reset Properties ---
60:     // Slave reset is synchronous, outputs should be 0 on the cycle after reset is asserted.
61:     if (past_valid_ff && $past(!PRESETn)) begin
62:         a_slave_reset_outputs: assert(PSLVERR == 0 && PRDATA == 0);
63:     end
64:     
65:     // After reset is released, master should be in IDLE state.
66:     if (past_valid_ff && $rose(PRESETn)) begin
67:         a_master_reset_state: assert(PSEL == 0 && PENABLE == 0);
68:     end
69: 
70:     // --- Protocol Properties ---
71:     // Master must hold control signals stable from SETUP through ACCESS.
72:     if (past_valid_ff && $past(PSEL && !PENABLE) && (PSEL && PENABLE)) begin // SETUP -> ACCESS
73:         a_addr_stable: assert($stable(PADDR));
74:         a_pwrite_stable: assert($stable(PWRITE));
75:         if ($past(PWRITE)) begin
76:             a_pwdata_stable: assert($stable(PWDATA));
77:             a_pstrb_stable: assert($stable(PSTRB));
78:         end
79:     end
80: 
81:     // Slave PREADY must be correct (no wait states).
82:     a_pready_logic: assert(PREADY == (PSEL && PENABLE));
83: 
84:     // Check PSLVERR logic. The slave registers the error status during the SETUP phase.
85:     // The result (PSLVERR) is then visible during the ACCESS phase.
86:     if (past_valid_ff && $past(PSEL && !PENABLE) && (PSEL && PENABLE)) begin // If transitioning SETUP -> ACCESS
87:         // Check PSLVERR based on conditions from previous (SETUP) cycle.
88:         
89:         // An access to an invalid address should generate an error.
90:         if ($past(PADDR >= 1024)) begin
91:             a_pslverr_on_bad_addr: assert(PSLVERR == 1);
92:         end
93:         // An invalid read (PWRITE=0, PSTRB!=0) to a valid address should generate an error.
94:         else if ($past(!PWRITE && PSTRB != 4'b0000)) begin
95:              a_pslverr_on_bad_read: assert(PSLVERR == 1);
96:         end
97:         // A valid transfer to a valid address must not generate an error.
98:         else {
99:             if ($past(!PWRITE)) begin
100:                 a_pslverr_on_good_read: assert(PSLVERR == 0);
101:             end
102:             if ($past(PWRITE)) begin
103:                 a_pslverr_on_good_write: assert(PSLVERR == 0);
104:             end
105:         }
106:     end
107: end
108: 
109: // --- Data Integrity Checker ---
110: (* anyconst *) reg [9:0] watched_addr;
111: reg [31:0] expected_data;
112: reg [3:0] byte_valid;
113: 
114: initial begin
115:     byte_valid = 4'b0000;
116: end
117: 
118: always @(posedge PCLK) begin
119:     if (!PRESETn) begin
120:         byte_valid <= 4'b0000;
121:         // Memory is uninitialized, so expected_data is not initialized.
122:     end else begin
123:         // A write completed to the watched address in the ACCESS phase. Update our model.
124:         if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024) begin
125:             expected_data <= {
126:                 PSTRB[3] ? PWDATA[31:24] : expected_data[31:24],
127:                 PSTRB[2] ? PWDATA[23:16] : expected_data[23:16],
128:                 PSTRB[1] ? PWDATA[15:8]  : expected_data[15:8],
129:                 PSTRB[0] ? PWDATA[7:0]   : expected_data[7:0]
130:             };
131:             byte_valid <= byte_valid | PSTRB;
132:         end
133: 
134:         // A legal read completes in the ACCESS phase. Data is available on PRDATA.
135:         // The value on PRDATA was registered by the slave at the end of the SETUP phase.
136:         // The expected_data reflects the state of memory from the last completed write.
137:         // This check will fail if the slave's byte-lane write logic is incorrect.
138:         if (PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000 && PADDR[9:0] == watched_addr && PADDR < 1024) begin
139:             if (byte_valid[0]) begin a_data_byte0: assert(PRDATA[7:0]   == expected_data[7:0]);   end
140:             if (byte_valid[1]) begin a_data_byte1: assert(PRDATA[15:8]  == expected_data[15:8]);  end
141:             if (byte_valid[2]) begin a_data_byte2: assert(PRDATA[23:16] == expected_data[23:16]); end
142:             if (byte_valid[3]) begin a_data_byte3: assert(PRDATA[31:24] == expected_data[31:24]); end
143:         end
144:     end
145: end
146: 
147: // --- Cover points ---
148: always @(posedge PCLK) begin
149:     if (!PRESETn) c_reset_active: cover(1);
150:     if (past_valid_ff && $rose(PRESETn)) c_reset_release: cover(1);
151:     
152:     // Master FSM phases
153:     if (PSEL && !PENABLE) c_setup_phase: cover(1);
154:     if (PSEL && PENABLE)  c_access_phase: cover(1);
155:     
156:     // Transfer completion
157:     if (PSEL && PENABLE && PREADY && PWRITE) c_write_completes: cover(1);
158:     if (PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000) c_read_completes: cover(1);
159:     
160:     // Back-to-back transfer (ACCESS -> SETUP)
161:     if (past_valid_ff && $past(PREADY && transfer) && (PSEL && !PENABLE)) c_back_to_back: cover(1);
162: 
163:     // Error condition trigger
164:     if (PSEL && !PENABLE && PADDR >= 1024) c_addr_error_trigger: cover(1);
165:     if (PSEL && !PENABLE && !PWRITE && PSTRB != 4'b0000) c_read_error_trigger: cover(1);
166:     
167:     // Data integrity covers
168:     if (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr && PADDR < 1024) c_data_write: cover(1);
169:     if (PSEL && PENABLE && PREADY && !PWRITE && PSTRB == 4'b0000 && PADDR[9:0] == watched_addr && |byte_valid) c_data_readback: cover(1);
170:     
171:     // Cover a partial overwrite of an already written word
172:     if (past_valid_ff && $past(PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr) && $past(|byte_valid) && (byte_valid != 4'b1111) &&
173:         (PSEL && PENABLE && PREADY && PWRITE && PADDR[9:0] == watched_addr)) begin
174:         c_partial_overwrite: cover(1);
175:     end
176: end
177: 
178: endmodule
Log tail:
SBY  5:25:35 [apb4_prep] Removing directory '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_05e4891f4e990276/apb4_gemini_formal/loop_work_shorter/apb4_prep'.
SBY  5:25:35 [apb4_prep] Writing '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_05e4891f4e990276/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/apb4_formal.sv'.
SBY  5:25:35 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_05e4891f4e990276/apb4_gemini_formal/loop_work_shorter/APB_Master.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_05e4891f4e990276/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Master.v'.
SBY  5:25:35 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_05e4891f4e990276/apb4_gemini_formal/loop_work_shorter/APB_Slave.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_05e4891f4e990276/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Slave.v'.
SBY  5:25:35 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_05e4891f4e990276/apb4_gemini_formal/loop_work_shorter/APB_Wrapper.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_05e4891f4e990276/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Wrapper.v'.
SBY  5:25:35 [apb4_prep] base: starting process "cd apb4_prep/src; yosys -ql ../model/design.log ../model/design.ys"
SBY  5:25:35 [apb4_prep] base: apb4_formal.sv:99: ERROR: syntax error, unexpected TOK_IF
SBY  5:25:35 [apb4_prep] base: finished (returncode=1)
SBY  5:25:35 [apb4_prep] base: task failed. ERROR.
SBY  5:25:35 [apb4_prep] summary: Elapsed clock time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  5:25:35 [apb4_prep] summary: Elapsed process time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  5:25:35 [apb4_prep] summary: engine_0 (smtbmc) did not return a status
SBY  5:25:35 [apb4_prep] summary: engine_0 did not produce any traces
SBY  5:25:35 [apb4_prep] DONE (ERROR, rc=16)
SBY  5:25:35 The following tasks failed: ['prep']

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
apb4_formal.sv:99: ERROR: syntax error, unexpected TOK_IF


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