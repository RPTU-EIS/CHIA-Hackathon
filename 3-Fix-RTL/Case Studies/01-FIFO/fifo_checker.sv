// Checker for fifo.sv, ported from chia-hello-world/fifo.sv's own inline
// `ifdef FORMAL` property block (immediate assert/cover, no concurrent SVA
// - matches yosys' native formal support) into a standalone golden checker
// bound into `fifo` via SystemVerilog `bind`, matching the Processor/
// HMAC-DRBG case studies' own established pattern. Property text is
// unchanged from the original tutorial source (a well-known formal-FIFO
// example); only mechanical changes were needed:
//   - `initial x <= 0;` -> `initial x = 0;` (read_slang rejects a
//     non-blocking assignment inside an `initial` procedure; the two
//     mean the same thing here since there's no clock involved).
//   - `$changed(x)` -> `(x != $past(x))` (read_slang doesn't support
//     $changed as a plain system-function call the way it supports
//     $past, which this same file already uses elsewhere; equivalent
//     for our purposes since we only ever compare against the
//     immediately-prior cycle).
//   - The "observer" signals that lived inside the same `ifdef FORMAL`
//     block (addr_diff, past_nwen, past_nren) move into this checker
//     module, since they were already verification-only, never part of
//     the DUT's own synthesizable logic.
//
// PORT WIDTHS ARE DELIBERATELY OVERSIZED (8 bits for count/waddr/raddr,
// comfortably more than MAX_DATA=17 needs even after the real fix widens
// them): the intended fix for this case study's injected bug involves
// WIDENING the DUT's own count/address ports (see fifo.sv's own note),
// and since this checker file is golden (never edited by the LLM), its
// ports have to stay valid whether the DUT's actual port is still the
// original 4 bits or has been widened partway through fixing. Confirmed
// empirically (local docker test) that connecting a narrower DUT signal
// into a wider checker port zero-extends correctly and gives the same
// verdict as an exactly-matching-width port, at both ends of the fix.
module fifo_checker (
    input wire clk, rst,
    input wire wen, ren,
    input wire [7:0] wdata, rdata,
    input wire [7:0] count,
    input wire full, empty,
    input wire [7:0] waddr, raddr
);
    // Matches fifo.sv's own default (see that file's note on why this
    // case study deliberately runs at MAX_DATA=17, not the tutorial's
    // original default of 16 - a non-power-of-two depth that exercises
    // both the count-width bug and, once that alone is "fixed", a
    // deeper address-width bug too).
    localparam MAX_DATA = 17;

    // observers
    wire [$clog2(MAX_DATA):0] addr_diff;
    assign addr_diff = waddr >= raddr
                     ? waddr - raddr
                     : waddr + MAX_DATA - raddr;

    // tests
    always @(posedge clk) begin
        if (~rst) begin
            // waddr and raddr can only be non zero if reset is low
            w_nreset: cover (waddr || raddr);

            // count never more than max
            a_oflow:  assert (count <= MAX_DATA);
            a_oflow2: assert (waddr < MAX_DATA);

            // count should be equal to the difference between writer and reader address
            a_count_diff: assert (count == addr_diff
                               || count == MAX_DATA && addr_diff == 0);

            // count should only be able to increase or decrease by 1
            a_counts: assert (count == 0
                           || count == $past(count)
                           || count == $past(count) + 1
                           || count == $past(count) - 1);

            // read/write addresses can only increase (or stay the same)
            a_raddr: assert (raddr == 0
                          || raddr == $past(raddr)
                          || raddr == $past(raddr + 1));
            a_waddr: assert (waddr == 0
                          || waddr == $past(waddr)
                          || waddr == $past(waddr + 1));

            // full and empty work as expected
            a_full:  assert (!full || count == MAX_DATA);
            w_full:  cover  (wen && !ren && count == MAX_DATA-1);
            a_empty: assert (!empty || count == 0);
            w_empty: cover  (ren && !wen && count == 1);

            // reading/writing non zero values
            w_nzero_write: cover (wen && wdata);
            w_nzero_read:  cover (ren && rdata);
        end else begin
            // waddr and raddr are zero while reset is high
            a_reset: assert (!waddr && !raddr);
            w_reset: cover  (rst);

            // outputs are zero while reset is high
            a_zero_out: assert (!empty && !full && !count);
        end
    end

    // implementing w_underfill without properties
    // can't use !$past(wen) since it will always trigger in the first cycle
    reg past_nwen;
    initial past_nwen = 0;
    always @(posedge clk) begin
        if (rst) past_nwen <= 0;
        if (!rst) begin
            w_underfill: cover (past_nwen && (waddr != $past(waddr)));
            past_nwen <= !wen;
        end
    end
    // end w_underfill

    // w_overfill does the same, but has been separated so that w_underfill
    // can be included in the docs more cleanly
    reg past_nren;
    initial past_nren = 0;
    always @(posedge clk) begin
        if (rst) past_nren <= 0;
        if (!rst) begin
            w_overfill: cover (past_nren && (raddr != $past(raddr)));
            past_nren <= !ren;
        end
    end
endmodule

bind fifo fifo_checker fifo_checker_i (
    .clk(clk), .rst(rst), .wen(wen), .ren(ren),
    .wdata(wdata), .rdata(rdata), .count(count),
    .full(full), .empty(empty),
    .waddr(waddr), .raddr(raddr)
);
