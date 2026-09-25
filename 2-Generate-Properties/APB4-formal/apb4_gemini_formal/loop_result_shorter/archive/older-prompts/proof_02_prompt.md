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
   Recheck the read-A/read-B timing table and full-address scope from review.
   A delayed checker comparing another transaction's PRDATA is PROPERTY_FIX,
   not an RTL bug. Unspecified error policies are not bug evidence. When a
   data-corruption claim depends on unavailable waveform values, use
   PROOF_BLOCKED unless the supplied RTL and property establish the violation
   independently; clearly distinguish static reasoning from observed values.
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
        if (!PRESETn) begin
            // Check master reset state
            a_master_reset_state: assert(PSEL == 0 && PENABLE == 0);
            // Check slave reset state
            a_slave_reset_pslverr: assert(PSLVERR == 0);
            a_slave_reset_prdata: assert(PRDATA == 0);
        end

        // Post-reset checks
        if (past_valid) begin
            // The slave should assert PREADY in the access phase (zero wait states)
            a_slave_ready: assert(PREADY == is_access);

            // A read with non-zero PSTRB is a protocol violation and must raise an error
            if (is_access && is_read && PSTRB != 4'b0000) begin
                a_read_with_strb_is_error: assert(PSLVERR);
            end

            // A valid read should not raise an error
            if (is_access && is_read && PSTRB == 4'b0000) begin
                a_valid_read_no_err: assert(!PSLVERR);
            end

            // A valid write should not raise an error
            if (is_access && is_write) begin
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


BMC log:
Key diagnostics:
SBY  7:11:00 [apb4_bmc] engine_0: ##   0:00:00  BMC failed!
SBY  7:11:00 [apb4_bmc] engine_0: ##   0:00:00  Assert failed in apb4_formal: a_slave_reset_prdata
SBY  7:11:01 [apb4_bmc] engine_0: ##   0:00:00  Assert failed in apb4_formal: a_slave_reset_pslverr
SBY  7:11:10 [apb4_bmc] engine_0: ##   0:00:09  Assert failed in apb4_formal: a_data_integrity_b3
SBY  7:11:14 [apb4_bmc] engine_0: ##   0:00:13  Assert failed in apb4_formal: a_data_integrity_b2
SBY  7:12:48 [apb4_bmc] engine_0: ##   0:01:48  Assert failed in apb4_formal: a_data_integrity_b0
SBY  7:12:48 [apb4_bmc] engine_0: ##   0:01:48  Assert failed in apb4_formal: a_data_integrity_b1
SBY  7:12:48 [apb4_bmc] engine_0: ##   0:01:48  Assert failed in apb4_formal: a_data_integrity_b2 [failed before]
SBY  7:12:48 [apb4_bmc] engine_0: ##   0:01:48  Assert failed in apb4_formal: a_data_integrity_b3 [failed before]
SBY  7:12:51 [apb4_bmc] engine_0: ##   0:01:50  Status: failed
SBY  7:12:51 [apb4_bmc] engine_0: Status returned by engine: FAIL
SBY  7:12:51 [apb4_bmc] summary: engine_0 (smtbmc --keep-going) returned FAIL
SBY  7:12:51 [apb4_bmc] summary:   failed assertion apb4_formal.a_slave_reset_prdata at apb4_formal.sv:114.13-114.54 step 1
SBY  7:12:51 [apb4_bmc] summary:   failed assertion apb4_formal.a_slave_reset_pslverr at apb4_formal.sv:113.13-113.56 step 1
SBY  7:12:51 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_integrity_b3 at apb4_formal.sv:144.38-144.106 step 6
SBY  7:12:51 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_integrity_b2 at apb4_formal.sv:143.38-143.106 step 6
SBY  7:12:51 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_integrity_b0 at apb4_formal.sv:141.38-141.104 step 8
SBY  7:12:51 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_integrity_b1 at apb4_formal.sv:142.38-142.105 step 8
SBY  7:12:51 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_integrity_b2 at apb4_formal.sv:143.38-143.106 step 8
SBY  7:12:51 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_integrity_b3 at apb4_formal.sv:144.38-144.106 step 8
SBY  7:12:51 [apb4_bmc] DONE (FAIL, rc=2)
SBY  7:12:51 The following tasks failed: ['bmc']
    16/23: \a_read_with_strb_is_error_EN
Already reached goals:

Numbered emitted apb4_formal.sv (solver line numbers):
1: module apb4_formal(input wire PCLK);
2:     // DUT Connections
3:     wire PSEL, PENABLE, PWRITE;
4:     wire [31:0] PADDR, PWDATA, PRDATA;
5:     wire [3:0] PSTRB;
6:     wire [2:0] PPROT;
7:     wire PREADY, PSLVERR;
8: 
9:     // Requester (environment) signals
10:     (* anyseq *) reg SWRITE;
11:     (* anyseq *) reg [31:0] SADDR, SWDATA;
12:     (* anyseq *) reg [3:0] SSTRB;
13:     (* anyseq *) reg [2:0] SPROT;
14:     (* anyseq *) reg transfer;
15: 
16:     // Reset and history logic
17:     reg first_cycle = 1'b1;
18:     reg past_valid = 1'b0;
19:     wire PRESETn = !first_cycle;
20: 
21:     always @(posedge PCLK) begin
22:         first_cycle <= 1'b0;
23:         past_valid <= PRESETn;
24:     end
25: 
26:     // Instantiate Master and Slave directly as requested
27:     APB_Master Master (
28:         .PCLK(PCLK),
29:         .PRESETn(PRESETn),
30:         .SWRITE(SWRITE),
31:         .SADDR(SADDR),
32:         .SWDATA(SWDATA),
33:         .SSTRB(SSTRB),
34:         .SPROT(SPROT),
35:         .transfer(transfer),
36:         .PSEL(PSEL),
37:         .PENABLE(PENABLE),
38:         .PWRITE(PWRITE),
39:         .PADDR(PADDR),
40:         .PWDATA(PWDATA),
41:         .PSTRB(PSTRB),
42:         .PPROT(PPROT),
43:         .PREADY(PREADY),
44:         .PSLVERR(PSLVERR)
45:     );
46: 
47:     APB_Slave Slave (
48:         .PCLK(PCLK),
49:         .PRESETn(PRESETn),
50:         .PSEL(PSEL),
51:         .PENABLE(PENABLE),
52:         .PWRITE(PWRITE),
53:         .PADDR(PADDR),
54:         .PWDATA(PWDATA),
55:         .PSTRB(PSTRB),
56:         .PPROT(PPROT),
57:         .PREADY(PREADY),
58:         .PSLVERR(PSLVERR),
59:         .PRDATA(PRDATA)
60:     );
61: 
62:     // --- Formal Properties ---
63: 
64:     // Shadow memory model for data integrity check
65:     (* anyconst *) reg [9:0] watched_addr;
66:     reg [31:0] r_expected_data;
67:     reg [3:0]  r_byte_valid;
68: 
69:     // Helper signals for state
70:     wire is_setup = PSEL && !PENABLE;
71:     wire is_access = PSEL && PENABLE;
72:     wire transfer_done = is_access && PREADY;
73:     wire is_write = PWRITE;
74:     wire is_read = !PWRITE;
75:     wire is_watched_addr = (PADDR[31:10] == 0) && (PADDR[9:0] == watched_addr);
76: 
77:     always @(posedge PCLK) begin
78:         // --- Shadow Model Update Logic ---
79:         // On reset, memory content is unknown
80:         if (!PRESETn) begin
81:             r_byte_valid <= 4'b0000;
82:         end
83:         // On a completed write to the watched address, update our model
84:         // This models the CORRECT behavior (byte preservation)
85:         else if (transfer_done && is_write && is_watched_addr) begin
86:             if (PSTRB[0]) r_expected_data[7:0]   <= PWDATA[7:0];
87:             if (PSTRB[1]) r_expected_data[15:8]  <= PWDATA[15:8];
88:             if (PSTRB[2]) r_expected_data[23:16] <= PWDATA[23:16];
89:             if (PSTRB[3]) r_expected_data[31:24] <= PWDATA[31:24];
90:             r_byte_valid <= r_byte_valid | PSTRB;
91:         end
92:     end
93: 
94:     always @(posedge PCLK) begin
95:         // --- Assumptions (Environment Constraints) ---
96:         // Assume reset is held for the first cycle only
97:         if (first_cycle) begin
98:             assume(!PRESETn);
99:         end else begin
100:             assume(PRESETn);
101:         end
102: 
103:         // Assume address is in range for any new transfer
104:         if (PRESETn && transfer) begin
105:             assume_saddr_in_range: assume(SADDR < 1024);
106:         end
107: 
108:         // --- Assertions (DUT Requirements) ---
109:         if (!PRESETn) begin
110:             // Check master reset state
111:             a_master_reset_state: assert(PSEL == 0 && PENABLE == 0);
112:             // Check slave reset state
113:             a_slave_reset_pslverr: assert(PSLVERR == 0);
114:             a_slave_reset_prdata: assert(PRDATA == 0);
115:         end
116: 
117:         // Post-reset checks
118:         if (past_valid) begin
119:             // The slave should assert PREADY in the access phase (zero wait states)
120:             a_slave_ready: assert(PREADY == is_access);
121: 
122:             // A read with non-zero PSTRB is a protocol violation and must raise an error
123:             if (is_access && is_read && PSTRB != 4'b0000) begin
124:                 a_read_with_strb_is_error: assert(PSLVERR);
125:             end
126: 
127:             // A valid read should not raise an error
128:             if (is_access && is_read && PSTRB == 4'b0000) begin
129:                 a_valid_read_no_err: assert(!PSLVERR);
130:             end
131: 
132:             // A valid write should not raise an error
133:             if (is_access && is_write) begin
134:                 a_valid_write_no_err: assert(!PSLVERR);
135:             end
136: 
137:             // --- Data Integrity Check ---
138:             // This check will FAIL due to the slave's faulty write logic.
139:             // If a valid read completes on the watched address, check PRDATA against our model.
140:             if (transfer_done && is_read && PSTRB == 4'b0000 && is_watched_addr) begin
141:                 if (r_byte_valid[0]) a_data_integrity_b0: assert(PRDATA[7:0]   == r_expected_data[7:0]);
142:                 if (r_byte_valid[1]) a_data_integrity_b1: assert(PRDATA[15:8]  == r_expected_data[15:8]);
143:                 if (r_byte_valid[2]) a_data_integrity_b2: assert(PRDATA[23:16] == r_expected_data[23:16]);
144:                 if (r_byte_valid[3]) a_data_integrity_b3: assert(PRDATA[31:24] == r_expected_data[31:24]);
145:             end
146:         end
147: 
148:         // --- Cover Properties (Reachability) ---
149:         c_reset_held: cover(!PRESETn);
150:         c_reset_released: cover($rose(PRESETn));
151: 
152:         if (PRESETn) begin
153:             c_setup: cover(is_setup);
154:             c_access: cover(is_access);
155:             c_write_complete: cover(transfer_done && is_write);
156:             c_read_complete: cover(transfer_done && is_read);
157:             c_back_to_back: cover($past(is_access && PREADY) && is_setup);
158:             c_zero_strobe_write: cover(transfer_done && is_write && PSTRB == 4'b0000);
159: 
160:             // Cover the trigger for the invalid read error check
161:             c_read_err_trigger: cover(is_access && is_read && PSTRB != 4'b0000);
162: 
163:             // Cover a read-after-write sequence to exercise the data integrity check
164:             if (transfer_done && is_read && is_watched_addr && |r_byte_valid) begin
165:                 c_read_after_write: cover(1);
166:             end
167: 
168:             // Cover a partial overwrite sequence
169:             if (transfer_done && is_read && is_watched_addr && r_byte_valid != 4'b0 && r_byte_valid != 4'b1111) begin
170:                 c_partial_overwrite: cover(1);
171:             end
172:         end
173:     end
174: endmodule
Log tail:
g module APB_Slave...
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
Computing hashes of 117 cells of `\apb4_formal'.
Finding duplicate cells in `\apb4_formal'.
Computing hashes of 114 cells of `\apb4_formal'.
Finding duplicate cells in `\apb4_formal'.
<suppressed ~9 debug messages>
Removed a total of 3 cells.

11.3. Executing OPT_DFF pass (perform DFF optimizations).

11.4. Executing OPT_CLEAN pass (remove unused cells and wires).
Finding unused cells or wires in module \APB_Master..
Finding unused cells or wires in module \APB_Slave..
Finding unused cells or wires in module \apb4_formal..
Removed 0 unused cells and 3 unused wires.
<suppressed ~1 debug messages>

11.5. Finished fast OPT passes.

12. Executing OPT_CLEAN pass (remove unused cells and wires).
Finding unused cells or wires in module \APB_Master..
Finding unused cells or wires in module \APB_Slave..
Finding unused cells or wires in module \apb4_formal..
Removed 0 unused cells and 132 unused wires.
<suppressed ~2 debug messages>

13. Executing RTLIL backend.
Output filename: ../model/design_prep.il

End of script. Logfile hash: b0962d1bef, time: 0.05s, user: 0.05s, system: 0.01s, MEM: 21.17 MB peak
Yosys 0.68+130 (git sha1 dd83bbad2-dirty, Release, Clang /usr/bin/clang++ 21.1.8)
Time spent: 44% 4x opt_clean (0 sec), 12% 1x check (0 sec), ...

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
      118 wires
      422 wire bits
       49 public wires
      257 public wire bits
        1 ports
        1 port bits
      114 cells
        1   $anyconst
        2   $anyinit
        6   $anyseq
       11   $assert
        4   $assume
        6   $eq
       21   $ff
       14   $logic_and
        6   $logic_not
        1   $lt
       36   $mux
        4   $not
        1   $or
        1   $reduce_bool
        2 submodules
        1   APB_Master
        1   APB_Slave

=== design hierarchy ===

        +----------Count including submodules.
        | 
      503 apb4_formal
       45 APB_Master
      344 APB_Slave

        +----------Count including submodules.
        | 
      524 wires
     8211 wire bits
      212 public wires
     3158 public wire bits
       30 ports
      262 port bits
        - memories
        - memory bits
        - processes
      503 cells
        3   $and
        1   $anyconst
       12   $anyinit
      130   $anyseq
       11   $assert
        6   $assume
       23   $eq
       21   $ff
       17   $logic_and
        8   $logic_not
        1   $lt
        1   $mem_v2
      254   $mux
        9   $not
        1   $or
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

End of script. Logfile hash: b3fc8baa18, time: 0.02s, user: 0.02s, system: 0.01s, MEM: 18.00 MB peak
Yosys 0.68+130 (git sha1 dd83bbad2-dirty, Release, Clang /usr/bin/clang++ 21.1.8)
Time spent: 76% 2x write_smt2 (0 sec), 13% 2x read_rtlil (0 sec), ...


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