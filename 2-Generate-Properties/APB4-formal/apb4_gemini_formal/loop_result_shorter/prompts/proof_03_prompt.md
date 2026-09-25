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


BMC log:
Key diagnostics:
SBY  7:51:18 [apb4_bmc] engine_0: ##   0:00:32  BMC failed!
SBY  7:51:18 [apb4_bmc] engine_0: ##   0:00:32  Assert failed in apb4_formal: a_data_byte3
SBY  7:51:25 [apb4_bmc] engine_0: ##   0:00:40  Assert failed in apb4_formal: a_data_byte2
SBY  7:54:22 [apb4_bmc] engine_0: ##   0:03:37  Assert failed in apb4_formal: a_data_byte1
SBY  7:55:19 [apb4_bmc] engine_0: ##   0:04:34  Assert failed in apb4_formal: a_data_byte0
SBY  7:55:19 [apb4_bmc] engine_0: ##   0:04:34  Assert failed in apb4_formal: a_data_byte2 [failed before]
SBY  7:55:19 [apb4_bmc] engine_0: ##   0:04:34  Assert failed in apb4_formal: a_data_byte3 [failed before]
SBY  7:55:20 [apb4_bmc] engine_0: ##   0:04:35  Status: failed
SBY  7:55:20 [apb4_bmc] engine_0: Status returned by engine: FAIL
SBY  7:55:20 [apb4_bmc] summary: engine_0 (smtbmc --keep-going) returned FAIL
SBY  7:55:20 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_byte3 at apb4_formal.sv:113.29-113.88 step 6
SBY  7:55:20 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_byte2 at apb4_formal.sv:112.29-112.88 step 6
SBY  7:55:20 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_byte1 at apb4_formal.sv:111.29-111.87 step 8
SBY  7:55:20 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_byte0 at apb4_formal.sv:110.29-110.86 step 8
SBY  7:55:20 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_byte2 at apb4_formal.sv:112.29-112.88 step 8
SBY  7:55:20 [apb4_bmc] summary:   failed assertion apb4_formal.a_data_byte3 at apb4_formal.sv:113.29-113.88 step 8
SBY  7:55:20 [apb4_bmc] DONE (FAIL, rc=2)
SBY  7:55:20 The following tasks failed: ['bmc']
Already reached goals:

Numbered emitted apb4_formal.sv (solver line numbers):
1: module apb4_formal(input wire PCLK);
2:   // --- External System / Requester Driver ---
3:   (* anyseq *) reg transfer, SWRITE;
4:   (* anyseq *) reg [31:0] SADDR, SWDATA;
5:   (* anyseq *) reg [3:0] SSTRB;
6:   (* anyseq *) reg [2:0] SPROT;
7: 
8:   // --- Reset and History Generation ---
9:   reg first_cycle = 1'b1;
10:   reg past_valid = 1'b0;
11:   reg past_PRESETn = 1'b0;
12:   wire PRESETn = !first_cycle;
13: 
14:   always @(posedge PCLK) begin
15:     first_cycle <= 1'b0;
16:     past_valid <= 1'b1;
17:     past_PRESETn <= PRESETn;
18:   end
19: 
20:   // --- DUT Connections ---
21:   wire PSEL, PENABLE, PWRITE;
22:   wire [31:0] PADDR, PWDATA, PRDATA;
23:   wire [3:0] PSTRB;
24:   wire [2:0] PPROT;
25:   wire PREADY, PSLVERR;
26: 
27:   APB_Master Master (
28:       .PCLK(PCLK),
29:       .PRESETn(PRESETn),
30:       .SWRITE(SWRITE),
31:       .SADDR(SADDR),
32:       .SWDATA(SWDATA),
33:       .SSTRB(SSTRB),
34:       .SPROT(SPROT),
35:       .transfer(transfer),
36:       .PSEL(PSEL),
37:       .PENABLE(PENABLE),
38:       .PWRITE(PWRITE),
39:       .PADDR(PADDR),
40:       .PWDATA(PWDATA),
41:       .PSTRB(PSTRB),
42:       .PPROT(PPROT),
43:       .PREADY(PREADY),
44:       .PSLVERR(PSLVERR)
45:   );
46: 
47:   APB_Slave Slave (
48:       .PCLK(PCLK),
49:       .PRESETn(PRESETn),
50:       .PSEL(PSEL),
51:       .PENABLE(PENABLE),
52:       .PWRITE(PWRITE),
53:       .PADDR(PADDR),
54:       .PWDATA(PWDATA),
55:       .PSTRB(PSTRB),
56:       .PPROT(PPROT),
57:       .PREADY(PREADY),
58:       .PSLVERR(PSLVERR),
59:       .PRDATA(PRDATA)
60:   );
61: 
62:   // --- Requester Contract (Assumptions) ---
63:   always @(posedge PCLK) begin
64:     // Assume valid addresses are used for transfers after reset.
65:     if (PRESETn && transfer) begin
66:       assume(SADDR < 32'd1024);
67:     end
68:   end
69: 
70:   // --- Properties and Scoreboard ---
71:   (* anyconst *) reg [31:0] watched_addr;
72:   initial assume(watched_addr[31:10] == 22'b0); // Constrain to 0..1023
73: 
74:   reg [31:0] expected_data;
75:   reg [3:0]  known_bytes;
76: 
77:   wire is_setup   = PSEL && !PENABLE;
78:   wire is_access  = PSEL && PENABLE;
79:   wire write_complete = is_access && PWRITE && PREADY;
80:   wire read_complete  = is_access && !PWRITE && PREADY;
81:   wire at_watched_addr = (PADDR == watched_addr);
82:   
83:   reg saw_legal_read_setup_at_watched;
84: 
85:   always @(posedge PCLK) begin
86:     if (!PRESETn) begin
87:       known_bytes <= 4'b0;
88:       saw_legal_read_setup_at_watched <= 1'b0;
89:       // expected_data does not need reset, its validity is tracked by known_bytes
90:     end else begin
91:       // --- Helper flag for legal read setup ---
92:       saw_legal_read_setup_at_watched <= is_setup && !PWRITE && at_watched_addr && (PSTRB == 4'b0);
93: 
94:       // --- Scoreboard Update ---
95:       // Update on completed write to watched_addr. The model correctly preserves
96:       // un-strobed bytes, which the RTL is suspected of not doing.
97:       if (write_complete && at_watched_addr) begin
98:         if (PSTRB[0]) expected_data[7:0]   <= PWDATA[7:0];
99:         if (PSTRB[1]) expected_data[15:8]  <= PWDATA[15:8];
100:         if (PSTRB[2]) expected_data[23:16] <= PWDATA[23:16];
101:         if (PSTRB[3]) expected_data[31:24] <= PWDATA[31:24];
102:         known_bytes <= known_bytes | PSTRB;
103:       end
104: 
105:       // --- Data Integrity Assertion (Legal Read) ---
106:       // Check on the ACCESS cycle of a legal read to a watched address.
107:       if (read_complete && at_watched_addr && saw_legal_read_setup_at_watched) begin
108:         // Requirement: For a legal read, data from known-written bytes must match
109:         // the expected value from the scoreboard.
110:         if (known_bytes[0]) a_data_byte0: assert(PRDATA[7:0]   == expected_data[7:0]);
111:         if (known_bytes[1]) a_data_byte1: assert(PRDATA[15:8]  == expected_data[15:8]);
112:         if (known_bytes[2]) a_data_byte2: assert(PRDATA[23:16] == expected_data[23:16]);
113:         if (known_bytes[3]) a_data_byte3: assert(PRDATA[31:24] == expected_data[31:24]);
114:       end
115: 
116:       // --- Illegal Read Error Assertion ---
117:       // An invalid read is one where PWRITE=0 but PSTRB is not zero. This should
118:       // be flagged by the slave with PSLVERR on the following cycle.
119:       if (past_valid && $past(is_setup && !PWRITE && PSTRB != 4'b0)) begin
120:         // Requirement: An invalid read (PSTRB != 0) should be flagged with PSLVERR.
121:         a_illegal_read_slverr: assert(PSLVERR == 1'b1);
122:       end
123:     end
124:   end
125: 
126:   // --- Covers ---
127:   always @(posedge PCLK) begin
128:     if (past_valid) begin
129:         if (!past_PRESETn && PRESETn) begin
130:             c_reset_release: cover(1);
131:         end
132: 
133:         if (PRESETn) begin
134:             // Cover a full write, a partial write, and a zero-strobe write to the watched address
135:             if (write_complete && at_watched_addr) begin
136:                 c_full_write:   cover(PSTRB == 4'b1111);
137:                 c_partial_write:cover(PSTRB != 4'b1111 && PSTRB != 4'b0000);
138:                 c_zero_strobe_write: cover(PSTRB == 4'b0000);
139:             end
140: 
141:             // Cover the data integrity assertion trigger for at least one known byte.
142:             if (read_complete && at_watched_addr && saw_legal_read_setup_at_watched && |known_bytes) begin
143:                 c_read_after_write: cover(1);
144:             end
145: 
146:             // Cover the trigger for the illegal read check
147:             if (is_setup && !PWRITE && PSTRB != 4'b0) begin
148:                 c_illegal_read_setup: cover(1);
149:             end
150:         end
151:     end
152:   end
153: 
154: endmodule
Log tail:
roblems).
Checking module APB_Master...
Checking module APB_Slave...
Checking module apb4_formal...
Found and reported 0 problems.

10. Executing SETUNDEF pass (replace undef values with defined constants).

11. Executing OPT pass (performing simple optimizations).

11.1. Executing OPT_EXPR pass (perform const folding).
Optimizing module APB_Master.
Optimizing module APB_Slave.
Optimizing module apb4_formal.
<suppressed ~1 debug messages>

11.2. Executing OPT_MERGE pass (detect identical cells).
Finding identical cells in module `\APB_Master'.
Computing hashes of 43 cells of `\APB_Master'.
Finding duplicate cells in `\APB_Master'.
Finding identical cells in module `\APB_Slave'.
Computing hashes of 342 cells of `\APB_Slave'.
Finding duplicate cells in `\APB_Slave'.
Finding identical cells in module `\apb4_formal'.
Computing hashes of 91 cells of `\apb4_formal'.
Finding duplicate cells in `\apb4_formal'.
Removed a total of 0 cells.

11.3. Executing OPT_DFF pass (perform DFF optimizations).

11.4. Executing OPT_CLEAN pass (remove unused cells and wires).
Finding unused cells or wires in module \APB_Master..
Finding unused cells or wires in module \APB_Slave..
Finding unused cells or wires in module \apb4_formal..
Removed 0 unused cells and 1 unused wires.
<suppressed ~1 debug messages>

11.5. Finished fast OPT passes.

12. Executing OPT_CLEAN pass (remove unused cells and wires).
Finding unused cells or wires in module \APB_Master..
Finding unused cells or wires in module \APB_Slave..
Finding unused cells or wires in module \apb4_formal..
Removed 0 unused cells and 133 unused wires.
<suppressed ~3 debug messages>

13. Executing RTLIL backend.
Output filename: ../model/design_prep.il

End of script. Logfile hash: 8462d87a2d, time: 0.06s, user: 0.06s, system: 0.01s, MEM: 21.23 MB peak
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
       91 wires
      417 wire bits
       35 public wires
      265 public wire bits
        1 ports
        1 port bits
       91 cells
        1   $anyconst
        4   $anyinit
        6   $anyseq
        5   $assert
        3   $assume
        5   $eq
       12   $ff
        1   $initstate
       15   $logic_and
        5   $logic_not
        1   $lt
       30   $mux
        1   $not
        1   $or
        1   $reduce_bool
        2 submodules
        1   APB_Master
        1   APB_Slave

=== design hierarchy ===

        +----------Count including submodules.
        | 
      480 apb4_formal
       45 APB_Master
      344 APB_Slave

        +----------Count including submodules.
        | 
      497 wires
     8206 wire bits
      198 public wires
     3166 public wire bits
       30 ports
      262 port bits
        - memories
        - memory bits
        - processes
      480 cells
        3   $and
        1   $anyconst
       14   $anyinit
      130   $anyseq
        5   $assert
        5   $assume
       22   $eq
       12   $ff
        1   $initstate
       18   $logic_and
        7   $logic_not
        1   $lt
        1   $mem_v2
      248   $mux
        6   $not
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

End of script. Logfile hash: 9c98794f3c, time: 0.02s, user: 0.02s, system: 0.01s, MEM: 17.88 MB peak
Yosys 0.68+130 (git sha1 dd83bbad2-dirty, Release, Clang /usr/bin/clang++ 21.1.8)
Time spent: 76% 2x write_smt2 (0 sec), 12% 2x read_rtlil (0 sec), ...


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