/* Formal Verification Harness for wishbone_ram */
`timescale 1ns / 1ps

module wishbone_ram_checker;

    // --- Parameters for the DUT ---
    localparam DATA_WIDTH = 32;
    localparam ADDR_WIDTH = 16;
    localparam SELECT_WIDTH = DATA_WIDTH / 8;

    // --- Free variables for the solver (DUT inputs) ---
    // Using spec-canonical names for clarity
    reg CLK_I;
    reg RST_I; // Spec: active-high reset
    reg [ADDR_WIDTH-1:0]   ADR_I;
    reg [DATA_WIDTH-1:0]   DAT_I;
    reg                    WE_I;
    reg [SELECT_WIDTH-1:0] SEL_I;
    reg                    STB_I;
    reg                    CYC_I;

    // --- DUT outputs ---
    wire [DATA_WIDTH-1:0]  DAT_O;
    wire                   ACK_O;
    // Note: This DUT does not implement ERR_O or RTY_O

    // --- DUT instantiation ---
    // Instance name MUST be `dut`
    wishbone_ram #(
        .DATA_WIDTH(DATA_WIDTH),
        .ADDR_WIDTH(ADDR_WIDTH),
        .SELECT_WIDTH(SELECT_WIDTH)
    ) dut (
        .clk    (CLK_I),
        .rst_n  (~RST_I), // DUT is active-low, spec's RST_I is active-high

        .adr_i  (ADR_I),
        .dat_i  (DAT_I),
        .dat_o  (DAT_O),
        .we_i   (WE_I),
        .sel_i  (SEL_I),
        .stb_i  (STB_I),
        .ack_o  (ACK_O),
        .cyc_i  (CYC_I)
    );

    // --- Formal logic ---

    // Rule 4: Force a reset at time 0 to ensure all traces start from a known state.
    reg init;
    initial init = 1;

    // Rule 3: Auxiliary registers for properties must have an initial value.
    reg prev_RST_I;
    initial prev_RST_I = 0;

    always @(posedge CLK_I) begin
        if (init) begin
            assume(RST_I == 1'b1); // Assert reset in the first cycle
        end
        init <= 0;

        // Update auxiliary state for properties
        prev_RST_I <= RST_I;

        // Target: reset-outputs
        // Spec: 3.1.1 Reset Operation. All interfaces initialize to a predefined state.
        // For a slave, this means termination signals must be inactive.
        // This property checks that if reset was active at the previous clock edge, ACK_O is low now.
        if (prev_RST_I) begin
            a_reset_outputs: assert(ACK_O == 1'b0);
        end
        // Rule 6: Cover the trigger condition for the assertion.
        c_reset_outputs: cover(prev_RST_I);

        // Target: 3.35
        // A termination signal (ACK_O) must only be asserted if the corresponding
        // cycle (CYC_I) and strobe (STB_I) signals were asserted in the prior cycle.
        // This check is only active after the initial cycle, as $past is not valid at time 0.
        if (!init && ACK_O)
            a_ack_implies_cyc_stb: assert($past(CYC_I) && $past(STB_I));
        c_ack_implies_cyc_stb: cover(!init && ACK_O);

        // Target: liveness-response
        // For every qualified strobe (CYC_I & STB_I high), the slave must respond with an ACK.
        // For this simple RAM without wait states, the response is required in the very next cycle.
        if (!init && $past(!RST_I && CYC_I && STB_I))
            a_stb_cyc_gets_ack: assert(ACK_O);
        c_stb_cyc_gets_ack: cover(!init && $past(!RST_I && CYC_I && STB_I));

        // Target: 3.30
        // The slave must not respond to bus signals (other than reset) when CYC_I is deasserted.
        // Here, we check that the data output bus DAT_O remains stable.
        if (!init && !$past(RST_I) && !$past(CYC_I)) begin
            a_dat_o_stable_no_cyc: assert(DAT_O == $past(DAT_O));
        end
        c_dat_o_stable_no_cyc: cover(!init && !$past(RST_I) && !$past(CYC_I));

        // Target: 3.65
        // Rule 3.65 states that DAT_O is qualified by a termination signal during a read.
        // This implies that DAT_O should not present new data during other active cycles, such as a write.
        // This property asserts that DAT_O remains stable during a write transaction.
        if (!init && $past(!RST_I && CYC_I && STB_I && WE_I)) begin
            a_dat_o_stable_on_write: assert(DAT_O == $past(DAT_O));
        end
        c_dat_o_stable_on_write: cover(!init && $past(!RST_I && CYC_I && STB_I && WE_I));

        // Target: 3.00
        // The slave's outputs must enter an initialized state during reset.
        // Rule 3.00 requires all interface signals to initialize. While reset is active,
        // DAT_O should be driven to a known, inactive state (all zeros for this DUT).
        if (prev_RST_I) begin
            a_dat_o_reset: assert(DAT_O == '0);
        end
        c_dat_o_reset: cover(prev_RST_I);

        // === NEW PROPERTIES INSERTED ABOVE THIS LINE ===
    end

endmodule
