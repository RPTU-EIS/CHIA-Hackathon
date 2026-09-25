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

        // --- Data Integrity Logic ---
        wire is_access  = PSEL && PENABLE;
        wire is_complete = is_access && PREADY;
        wire is_write_complete = is_complete && PWRITE;
        wire is_read_complete  = is_complete && !PWRITE && PSTRB == 4'b0;

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


Build/format/timeout diagnostics:
Key diagnostics:
SBY  6:52:09 [apb4_prep] base: apb4_formal.sv:98: ERROR: Found continuous assignment in always/initial block!
SBY  6:52:09 [apb4_prep] base: task failed. ERROR.
SBY  6:52:09 [apb4_prep] DONE (ERROR, rc=16)
SBY  6:52:09 The following tasks failed: ['prep']
apb4_formal.sv:98: ERROR: Found continuous assignment in always/initial block!
Already reached goals:

Numbered emitted apb4_formal.sv (solver line numbers):
1: module apb4_formal(input wire PCLK);
2:     // APB Interface Wires
3:     wire PSEL, PENABLE, PWRITE;
4:     wire [31:0] PADDR, PWDATA;
5:     wire [3:0] PSTRB;
6:     wire [2:0] PPROT;
7:     wire PREADY, PSLVERR;
8:     wire [31:0] PRDATA;
9: 
10:     // Requester (Environment) Inputs
11:     (* anyseq *) reg transfer, SWRITE;
12:     (* anyseq *) reg [31:0] SADDR, SWDATA;
13:     (* anyseq *) reg [3:0] SSTRB;
14:     (* anyseq *) reg [2:0] SPROT;
15: 
16:     // Reset and History Generation
17:     reg first_cycle = 1'b1;
18:     wire PRESETn = !first_cycle;
19:     reg past_valid = 1'b0;
20: 
21:     always @(posedge PCLK) begin
22:         first_cycle <= 1'b0;
23:         past_valid <= 1'b1;
24:     end
25: 
26:     // DUT Instantiation
27:     APB_Master Master (
28:         .PCLK(PCLK), .PRESETn(PRESETn), .SWRITE(SWRITE), .SADDR(SADDR),
29:         .SWDATA(SWDATA), .SSTRB(SSTRB), .SPROT(SPROT), .transfer(transfer),
30:         .PSEL(PSEL), .PENABLE(PENABLE), .PWRITE(PWRITE), .PADDR(PADDR),
31:         .PWDATA(PWDATA), .PSTRB(PSTRB), .PPROT(PPROT), .PREADY(PREADY),
32:         .PSLVERR(PSLVERR)
33:     );
34: 
35:     APB_Slave Slave (
36:         .PCLK(PCLK), .PRESETn(PRESETn), .PSEL(PSEL), .PENABLE(PENABLE),
37:         .PWRITE(PWRITE), .PADDR(PADDR), .PWDATA(PWDATA), .PSTRB(PSTRB),
38:         .PPROT(PPROT), .PREADY(PREADY), .PSLVERR(PSLVERR), .PRDATA(PRDATA)
39:     );
40: 
41:     // --- Data Integrity Checker Model ---
42:     (* anyconst *) reg [31:0] watched_addr;
43:     initial assume(watched_addr < 1024);
44: 
45:     reg [31:0] expected_data;
46:     reg [3:0] byte_valid;
47: 
48:     initial byte_valid = 4'b0;
49: 
50:     // --- Formal Properties and Covers ---
51:     always @(posedge PCLK) begin
52:         // --- Reset Logic for Checker ---
53:         if (!PRESETn) begin
54:             byte_valid <= 4'b0;
55:         end
56: 
57:         // --- Environment Assumptions ---
58:         // A1: Assume valid address from requester when not in reset
59:         if (PRESETn) begin
60:             assume_a1_addr_range: assume(SADDR < 1024);
61:         end
62: 
63:         // --- Protocol Assertions and Covers ---
64:         // C1: PSEL/PENABLE must be low during reset
65:         if (!PRESETn) begin
66:             assert_c1_reset_idle: assert(PSEL == 0 && PENABLE == 0);
67:         end
68:         
69:         // --- Checks requiring past state (guarded by past_valid and PRESETn) ---
70:         if (past_valid && $past(PRESETn)) begin
71:             // C2: SETUP phase must be followed by ACCESS phase
72:             if ($past(PSEL && !PENABLE)) begin
73:                 assert_c2_setup_to_access: assert(PSEL == 1 && PENABLE == 1);
74:             end
75: 
76:             // C3: Address and control signals must be stable from SETUP to ACCESS
77:             if ((PSEL && PENABLE) && $past(PSEL && !PENABLE)) begin
78:                 assert_c3_stable_addr:   assert(PADDR   == $past(PADDR));
79:                 assert_c3_stable_pwrite: assert(PWRITE  == $past(PWRITE));
80:                 assert_c3_stable_pwdata: assert(PWDATA  == $past(PWDATA));
81:                 assert_c3_stable_pstrb:  assert(PSTRB   == $past(PSTRB));
82:                 assert_c3_stable_pprot:  assert(PPROT   == $past(PPROT));
83:             end
84:             
85:             // C4/C5: PSLVERR response is registered, check one cycle after condition
86:             if ($past(PSEL && !PWRITE)) begin
87:                 if ($past(PSTRB != 4'b0)) begin
88:                     // C4: Invalid read must cause an error
89:                     assert_c4_invalid_read_slverr: assert(PSLVERR == 1);
90:                 end else begin
91:                     // C5: Valid read must not cause an error
92:                     assert_c5_valid_read_no_slverr: assert(PSLVERR == 0);
93:                 end
94:             end
95:         end
96: 
97:         // --- Data Integrity Logic ---
98:         wire is_access  = PSEL && PENABLE;
99:         wire is_complete = is_access && PREADY;
100:         wire is_write_complete = is_complete && PWRITE;
101:         wire is_read_complete  = is_complete && !PWRITE && PSTRB == 4'b0;
102: 
103:         if (PRESETn) begin
104:             // Update checker model on completed write to watched_addr
105:             if (is_write_complete && PADDR == watched_addr) begin
106:                 if (PSTRB[0]) expected_data[7:0]   <= PWDATA[7:0];
107:                 if (PSTRB[1]) expected_data[15:8]  <= PWDATA[15:8];
108:                 if (PSTRB[2]) expected_data[23:16] <= PWDATA[23:16];
109:                 if (PSTRB[3]) expected_data[31:24] <= PWDATA[31:24];
110:                 
111:                 if (PSTRB[0]) byte_valid[0] <= 1'b1;
112:                 if (PSTRB[1]) byte_valid[1] <= 1'b1;
113:                 if (PSTRB[2]) byte_valid[2] <= 1'b1;
114:                 if (PSTRB[3]) byte_valid[3] <= 1'b1;
115:             end
116:             
117:             // C6: Read data integrity check for known bytes
118:             if (is_read_complete && PADDR == watched_addr) begin
119:                 if (byte_valid[0]) assert_c6_read_byte0: assert(PRDATA[7:0]   == expected_data[7:0]);
120:                 if (byte_valid[1]) assert_c6_read_byte1: assert(PRDATA[15:8]  == expected_data[15:8]);
121:                 if (byte_valid[2]) assert_c6_read_byte2: assert(PRDATA[23:16] == expected_data[23:16]);
122:                 if (byte_valid[3]) assert_c6_read_byte3: assert(PRDATA[31:24] == expected_data[31:24]);
123:             end
124:         end
125:         
126:         // --- Cover Points ---
127:         cover_cov1_reset_assert: cover(!PRESETn);
128:         if (past_valid) begin
129:             if ($past(!PRESETn) && PRESETn) cover_cov1_reset_release: cover(1);
130:         end
131: 
132:         cover_cov2_setup: cover(PSEL && !PENABLE);
133:         cover_cov2_access: cover(PSEL && PENABLE);
134: 
135:         if (is_write_complete && PSTRB == 4'b1111) cover_cov3_full_write: cover(1);
136:         if (is_read_complete) cover_cov3_legal_read: cover(1);
137:         if (past_valid && $past(is_complete) && (PSEL && !PENABLE)) cover_cov4_b2b: cover(1);
138:         if (is_complete && !PWRITE && PSTRB != 4'b0) cover_cov5_invalid_read: cover(1);
139:         if (is_read_complete && PADDR == watched_addr && |byte_valid) cover_cov6_read_known: cover(1);
140:         if (is_write_complete && PADDR == watched_addr && PSTRB != 4'b1111 && PSTRB != 4'b0 && $past(byte_valid == 4'b1111))
141:             cover_cov7_partial_overwrite: cover(1);
142:         if (is_write_complete && PSTRB == 4'b0) cover_cov8_pstrb0_write: cover(1);
143:     end
144: endmodule
Log tail:
SBY  6:52:09 [apb4_prep] Removing directory '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_72b16cf0d2d96857/apb4_gemini_formal/loop_work_shorter/apb4_prep'.
SBY  6:52:09 [apb4_prep] Writing '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_72b16cf0d2d96857/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/apb4_formal.sv'.
SBY  6:52:09 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_72b16cf0d2d96857/apb4_gemini_formal/loop_work_shorter/APB_Master.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_72b16cf0d2d96857/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Master.v'.
SBY  6:52:09 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_72b16cf0d2d96857/apb4_gemini_formal/loop_work_shorter/APB_Slave.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_72b16cf0d2d96857/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Slave.v'.
SBY  6:52:09 [apb4_prep] Copy '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_72b16cf0d2d96857/apb4_gemini_formal/loop_work_shorter/APB_Wrapper.v' to '/tmp/ray/session_2026-09-16_17-22-51_832242_3414195/runtime_resources/working_dir_files/_ray_pkg_72b16cf0d2d96857/apb4_gemini_formal/loop_work_shorter/apb4_prep/src/APB_Wrapper.v'.
SBY  6:52:09 [apb4_prep] base: starting process "cd apb4_prep/src; yosys -ql ../model/design.log ../model/design.ys"
SBY  6:52:09 [apb4_prep] base: apb4_formal.sv:98: ERROR: Found continuous assignment in always/initial block!
SBY  6:52:09 [apb4_prep] base: finished (returncode=1)
SBY  6:52:09 [apb4_prep] base: task failed. ERROR.
SBY  6:52:09 [apb4_prep] summary: Elapsed clock time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  6:52:09 [apb4_prep] summary: Elapsed process time [H:MM:SS (secs)]: 0:00:00 (0)
SBY  6:52:09 [apb4_prep] summary: engine_0 (smtbmc) did not return a status
SBY  6:52:09 [apb4_prep] summary: engine_0 did not produce any traces
SBY  6:52:09 [apb4_prep] DONE (ERROR, rc=16)
SBY  6:52:09 The following tasks failed: ['prep']

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
apb4_formal.sv:98: ERROR: Found continuous assignment in always/initial block!


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