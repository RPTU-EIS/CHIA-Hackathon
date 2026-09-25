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


BUILD REPAIR: MAKE THE CURRENT CANDIDATE EXECUTABLE
No property verdict is available. Read the detailed Yosys logs, not just
'base: task failed'. Quote the first actual error and its source location.
Use the numbered emitted harness to identify the exact failing construct.
Check declaration scope, begin/end balance, symbolic-input drivers, and
checker clock/reset syntax against the shared example. Fix the cause rather
than guessing an unrelated construct or changing requirements. Keep the
explanation to five sentences; return the complete file with localized edits.
Repair only the SBY configuration, syntax, or harness wiring/clocking.
For unsupported process-memory sync, check for gclk and use the ordinary
input clock and single-word checker described below. For a timeout, identify
the slow stage; simplify checker implementation without weakening checks.
For a malformed response, return the complete required embedded-file format.
If exploration returned only a module, retain its justified new properties
and the previous candidate's valid checks. Return ALL SBY sections plus the
embedded module in ONE fenced apb4.sby; do not return just the missing pieces.
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
  wire at_watched_addr = (PADDR == watched_addr);

  // Transaction completion signals (in ACCESS phase with PREADY)
  wire write_complete = is_access && PWRITE && PREADY;
  wire read_complete  = is_access && !PWRITE && PREADY;
  
  // --- Scoreboard, Check Triggers ---
  
  // Triggers for assertions that need to be delayed one cycle
  reg fire_check_legal_read;
  reg fire_check_illegal_read;
  reg fire_check_write;
  
  // Latched data for delayed assertions
  reg [31:0] expected_data_for_check;
  reg [3:0]  known_bytes_for_check;

  always @(posedge PCLK) begin
    if (!PRESETn) begin
      known_bytes <= 4'b0;
      fire_check_legal_read <= 1'b0;
      fire_check_illegal_read <= 1'b0;
      fire_check_write <= 1'b0;
    end else begin
      // --- Scoreboard Update ---
      // Update on completed write to watched_addr.
      if (write_complete && at_watched_addr) begin
        if (PSTRB[0]) expected_data[7:0]   <= PWDATA[7:0];
        if (PSTRB[1]) expected_data[15:8]  <= PWDATA[15:8];
        if (PSTRB[2]) expected_data[23:16] <= PWDATA[23:16];
        if (PSTRB[3]) expected_data[31:24] <= PWDATA[31:24];
        known_bytes <= known_bytes | PSTRB;
      end

      // --- Set up assertion triggers for the NEXT cycle ---
      // These fire in the ACCESS phase to capture state for checking in the following cycle.

      // 1. Legal read data integrity check
      reg trigger_legal_read_now = 1'b0;
      trigger_legal_read_now = past_valid && read_complete && at_watched_addr && $past(is_setup && !PWRITE && PSTRB == 4'b0 && at_watched_addr);
      fire_check_legal_read <= trigger_legal_read_now;
      if (trigger_legal_read_now) begin
        expected_data_for_check <= expected_data;
        known_bytes_for_check   <= known_bytes;
      end

      // 2. Illegal read PSLVERR check
      fire_check_illegal_read <= past_valid && read_complete && $past(is_setup && !PWRITE && PSTRB != 4'b0);

      // 3. Write PSLVERR check
      fire_check_write <= write_complete;
    end
  end

  // --- Assertions (Delayed) ---
  always @(posedge PCLK) begin
    // These check the registered slave outputs (PRDATA, PSLVERR) one cycle after the ACCESS phase
    // to ensure the values have propagated through the slave's registers.
    if (past_valid && past_PRESETn) begin // Guards for normal operation after reset release
      
      // Requirement: For a legal read, data from known-written bytes must match.
      if (fire_check_legal_read) begin
        if (known_bytes_for_check[0]) a_data_byte0: assert(PRDATA[7:0]   == expected_data_for_check[7:0]);
        if (known_bytes_for_check[1]) a_data_byte1: assert(PRDATA[15:8]  == expected_data_for_check[15:8]);
        if (known_bytes_for_check[2]) a_data_byte2: assert(PRDATA[23:16] == expected_data_for_check[23:16]);
        if (known_bytes_for_check[3]) a_data_byte3: assert(PRDATA[31:24] == expected_data_for_check[31:24]);
      end

      // Requirement: An invalid read (PSTRB != 0) should be flagged with PSLVERR.
      if (fire_check_illegal_read) begin
        a_illegal_read_slverr: assert(PSLVERR == 1'b1);
      end
      
      // Requirement: The slave design never generates an error on a write transaction.
      if (fire_check_write) begin
        a_no_slverr_on_write: assert(PSLVERR == 1'b0);
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

            // Cover the trigger for the data integrity assertion.
            // This is the cycle *before* the assertion fires.
            if (past_valid && read_complete && at_watched_addr && $past(is_setup && !PWRITE && PSTRB == 4'b0 && at_watched_addr) && |known_bytes) begin
                c_read_after_write: cover(1);
            end

            // Cover the trigger for the illegal read assertion.
            if (past_valid && read_complete && $past(is_setup && !PWRITE && PSTRB != 4'b0)) begin
                c_illegal_read_access: cover(1);
            end
            
            // Cover the trigger for the write error assertion.
            if (write_complete) begin
                c_any_write_complete: cover(1);
            end
        end
    end
  end

endmodule


Build/format/timeout diagnostics:
Key diagnostics:
SBY  8:07:10 [apb4_prep] base: apb4_formal.sv:117: ERROR: Invalid nesting of always blocks and/or initializations.
SBY  8:07:10 [apb4_prep] base: task failed. ERROR.
SBY  8:07:10 [apb4_prep] DONE (ERROR, rc=16)
SBY  8:07:10 The following tasks failed: ['prep']
apb4_formal.sv:117: ERROR: Invalid nesting of always blocks and/or initializations.
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
79:   wire at_watched_addr = (PADDR == watched_addr);
80: 
81:   // Transaction completion signals (in ACCESS phase with PREADY)
82:   wire write_complete = is_access && PWRITE && PREADY;
83:   wire read_complete  = is_access && !PWRITE && PREADY;
84:   
85:   // --- Scoreboard, Check Triggers ---
86:   
87:   // Triggers for assertions that need to be delayed one cycle
88:   reg fire_check_legal_read;
89:   reg fire_check_illegal_read;
90:   reg fire_check_write;
91:   
92:   // Latched data for delayed assertions
93:   reg [31:0] expected_data_for_check;
94:   reg [3:0]  known_bytes_for_check;
95: 
96:   always @(posedge PCLK) begin
97:     if (!PRESETn) begin
98:       known_bytes <= 4'b0;
99:       fire_check_legal_read <= 1'b0;
100:       fire_check_illegal_read <= 1'b0;
101:       fire_check_write <= 1'b0;
102:     end else begin
103:       // --- Scoreboard Update ---
104:       // Update on completed write to watched_addr.
105:       if (write_complete && at_watched_addr) begin
106:         if (PSTRB[0]) expected_data[7:0]   <= PWDATA[7:0];
107:         if (PSTRB[1]) expected_data[15:8]  <= PWDATA[15:8];
108:         if (PSTRB[2]) expected_data[23:16] <= PWDATA[23:16];
109:         if (PSTRB[3]) expected_data[31:24] <= PWDATA[31:24];
110:         known_bytes <= known_bytes | PSTRB;
111:       end
112: 
113:       // --- Set up assertion triggers for the NEXT cycle ---
114:       // These fire in the ACCESS phase to capture state for checking in the following cycle.
115: 
116:       // 1. Legal read data integrity check
117:       reg trigger_legal_read_now = 1'b0;
118:       trigger_legal_read_now = past_valid && read_complete && at_watched_addr && $past(is_setup && !PWRITE && PSTRB == 4'b0 && at_watched_addr);
119:       fire_check_legal_read <= trigger_legal_read_now;
120:       if (trigger_legal_read_now) begin
121:         expected_data_for_check <= expected_data;
122:         known_bytes_for_check   <= known_bytes;
123:       end
124: 
125:       // 2. Illegal read PSLVERR check
126:       fire_check_illegal_read <= past_valid && read_complete && $past(is_setup && !PWRITE && PSTRB != 4'b0);
127: 
128:       // 3. Write PSLVERR check
129:       fire_check_write <= write_complete;
130:     end
131:   end
132: 
133:   // --- Assertions (Delayed) ---
134:   always @(posedge PCLK) begin
135:     // These check the registered slave outputs (PRDATA, PSLVERR) one cycle after the ACCESS phase
136:     // to ensure the values have propagated through the slave's registers.
137:     if (past_valid && past_PRESETn) begin // Guards for normal operation after reset release
138:       
139:       // Requirement: For a legal read, data from known-written bytes must match.
140:       if (fire_check_legal_read) begin
141:         if (known_bytes_for_check[0]) a_data_byte0: assert(PRDATA[7:0]   == expected_data_for_check[7:0]);
142:         if (known_bytes_for_check[1]) a_data_byte1: assert(PRDATA[15:8]  == expected_data_for_check[15:8]);
143:         if (known_bytes_for_check[2]) a_data_byte2: assert(PRDATA[23:16] == expected_data_for_check[23:16]);
144:         if (known_bytes_for_check[3]) a_data_byte3: assert(PRDATA[31:24] == expected_data_for_check[31:24]);
145:       end
146: 
147:       // Requirement: An invalid read (PSTRB != 0) should be flagged with PSLVERR.
148:       if (fire_check_illegal_read) begin
149:         a_illegal_read_slverr: assert(PSLVERR == 1'b1);
150:       end
151:       
152:       // Requirement: The slave design never generates an error on a write transaction.
153:       if (fire_check_write) begin
154:         a_no_slverr_on_write: assert(PSLVERR == 1'b0);
155:       end
156:     end
157:   end
158: 
159:   // --- Covers ---
160:   always @(posedge PCLK) begin
161:     if (past_valid) begin
162:         if (!past_PRESETn && PRESETn) begin
163:             c_reset_release: cover(1);
164:         end
165: 
166:         if (PRESETn) begin
167:             // Cover a full write, a partial write, and a zero-strobe write to the watched address
168:             if (write_complete && at_watched_addr) begin
169:                 c_full_write:   cover(PSTRB == 4'b1111);
170:                 c_partial_write:cover(PSTRB != 4'b1111 && PSTRB != 4'b0000);
171:                 c_zero_strobe_write: cover(PSTRB == 4'b0000);
172:             end
173: 
174:             // Cover the trigger for the data integrity assertion.
175:             // This is the cycle *before* the assertion fires.
176:             if (past_valid && read_complete && at_watched_addr && $past(is_setup && !PWRITE && PSTRB == 4'b0 && at_watched_addr) && |known_bytes) begin
177:                 c_read_after_write: cover(1);
178:             end
179: 
180:             // Cover the trigger for the illegal read assertion.
181:             if (past_valid && read_complete && $past(is_setup && !PWRITE && PSTRB != 4'b0)) begin
182:                 c_illegal_read_access: cover(1);
183:             end
184:             
185:             // Cover the trigger for the write error assertion.
186:             if (write_complete) begin
187:                 c_any_write_complete: cover(1);
188:             end
189:         end
190:     end
191:   end
192: 
193: endmodule
Log tail:
SBY  8:07:10 [apb4_prep] Removing directory '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_b450153c93af2e96/apb4_gemini_formal/loop_work_shorter/apb4_prep'.
SBY  8:07:10 [apb4_prep] Writing '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_b450153c93af2e96/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/apb4_formal.sv'.
SBY  8:07:10 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_b450153c93af2e96/apb4_gemini_formal/loop_work_shorter/APB_Master.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_b450153c93af2e96/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Master.v'.
SBY  8:07:10 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_b450153c93af2e96/apb4_gemini_formal/loop_work_shorter/APB_Slave.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_b450153c93af2e96/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Slave.v'.
SBY  8:07:10 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_b450153c93af2e96/apb4_gemini_formal/loop_work_shorter/APB_Wrapper.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_b450153c93af2e96/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Wrapper.v'.
SBY  8:07:10 [apb4_prep] base: starting process "cd apb4_prep/src; yosys -ql ../model/design.log ../model/design.ys"
SBY  8:07:10 [apb4_prep] base: apb4_formal.sv:117: ERROR: Invalid nesting of always blocks and/or initializations.
SBY  8:07:10 [apb4_prep] base: finished (returncode=1)
SBY  8:07:10 [apb4_prep] base: task failed. ERROR.
SBY  8:07:10 [apb4_prep] summary: Elapsed clock time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  8:07:10 [apb4_prep] summary: Elapsed process time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  8:07:10 [apb4_prep] summary: engine_0 (smtbmc) did not return a status
SBY  8:07:10 [apb4_prep] summary: engine_0 did not produce any traces
SBY  8:07:10 [apb4_prep] DONE (ERROR, rc=16)
SBY  8:07:10 The following tasks failed: ['prep']

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
Generating RTLIL representation for module `\apb4_formal'.
apb4_formal.sv:117: ERROR: Invalid nesting of always blocks and/or initializations.


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