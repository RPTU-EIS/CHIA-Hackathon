`default_nettype none

// Top-level module for SymbiYosys Formal Verification
module fv_gcd_sby
#(
  parameter WIDTH = 4
)
(
  // These inputs are driven by the SBY engine
  input wire clk_i,
  input wire rst_i,
  input wire start_i,
  input wire [WIDTH-1:0] a_i,
  input wire [WIDTH-1:0] b_i
);

  // Instantiate the DUT
  wire [WIDTH-1:0] res_o;
  wire             ready_o;
  wire             done_o;

  gcd_binary #(
    .WIDTH(WIDTH)
  ) dut (
    .clk_i(clk_i),
    .rst_i(rst_i),
    .start_i(start_i),
    .a_i(a_i),
    .b_i(b_i),
    .res_o(res_o),
    .ready_o(ready_o),
    .done_o(done_o)
  );

`ifdef FV_ASSERT
  // Helper function for gcd_p golden model
  function automatic [WIDTH-1:0] euclidean_gcd (
    input [WIDTH-1:0] a_in,
    input [WIDTH-1:0] b_in
  );
    logic [WIDTH-1:0] a, b, temp;
    begin
      a = a_in;
      b = b_in;
      // Loop bound needs to be generous but synthesizable.
      // O(WIDTH) iterations is sufficient for Euclidean algorithm.
      // 2*WIDTH is a safe upper bound.
      for (int i = 0; i < 2 * WIDTH; i = i + 1) begin
        if (b > 0) begin
          temp = b;
          b = a % b;
          a = temp;
        end
      end
      euclidean_gcd = a; // Verilog-2001 style return
    end
  endfunction

  // --- Formal Properties ---

  // Initialization: Assume reset is asserted in the first cycle
  reg init;
  initial init = 1'b1;
  always @(posedge clk_i) begin
    if (init) begin
      assume(rst_i);
    end
    init <= 1'b0;
  end

  // Property reset_p: After reset, DUT is ready
  // reset_sequence |=> ##0 (ready_o == 1'b1) && (done_o == 1'b0);
  always @(posedge clk_i) begin
    if (!init && $past(rst_i)) begin
      assert(ready_o == 1'b1);
      assert(done_o == 1'b0);
    end
  end

  // Property idle_p: If ready and not started, remain ready
  // (ready_o && !start_i) |=> (ready_o && !done_o)
  always @(posedge clk_i) begin
    if (rst_i) begin
      // Property disabled by reset
    end else if (!init && $past(ready_o && !start_i)) begin
      assert(ready_o == 1'b1);
      assert(done_o == 1'b0);
    end
  end

  // Property gcd_p: Correctness of a full transaction
  typedef enum logic[1:0] {
    P_GCD_IDLE,
    P_GCD_BUSY,
    P_GCD_DONE
  } p_gcd_state_t;

  p_gcd_state_t p_gcd_state;
  logic [WIDTH-1:0] p_gcd_op_a, p_gcd_op_b, p_gcd_golden_res;
  logic [$clog2(2*WIDTH+1)-1:0] p_gcd_cycle_count;

  initial p_gcd_state = P_GCD_IDLE;
  initial p_gcd_op_a = '0;
  initial p_gcd_op_b = '0;
  initial p_gcd_golden_res = '0;
  initial p_gcd_cycle_count = '0;

  always @(posedge clk_i) begin
    if (rst_i) begin
      p_gcd_state <= P_GCD_IDLE;
      p_gcd_cycle_count <= '0;
      p_gcd_op_a <= '0;
      p_gcd_op_b <= '0;
      p_gcd_golden_res <= '0;
    end else begin
      case(p_gcd_state)
        P_GCD_IDLE: begin
          if (ready_o && start_i) begin
            p_gcd_op_a <= a_i;
            p_gcd_op_b <= b_i;
            p_gcd_golden_res <= euclidean_gcd(a_i, b_i);
            p_gcd_cycle_count <= 0;
            p_gcd_state <= P_GCD_BUSY;
          end
        end
        P_GCD_BUSY: begin
          if (done_o) begin
            assert(p_gcd_cycle_count >= 1 && p_gcd_cycle_count <= 2*WIDTH);
            assert(ready_o == 1'b0);
            assert(res_o == p_gcd_golden_res);
            p_gcd_state <= P_GCD_IDLE;
          end else begin
            p_gcd_cycle_count <= p_gcd_cycle_count + 1;
            assert(p_gcd_cycle_count <= 2*WIDTH);
            assert(ready_o == 1'b0);
            assert(done_o == 1'b0);
          end
        end

      endcase
    end
  end

  // Property wcl_p: Worst-case latency is 2*WIDTH+1 cycles
  reg p_wcl_active;
  reg [$clog2(2*WIDTH+2)-1:0] p_wcl_counter;

  initial p_wcl_active = 1'b0;
  initial p_wcl_counter = '0;

  always @(posedge clk_i) begin
    if (rst_i) begin
      p_wcl_active <= 1'b0;
      p_wcl_counter <= '0;
    end else begin
      if (p_wcl_active) begin
        if (done_o) begin
          p_wcl_active <= 1'b0;
        end else begin
          // Latency is counter+1 cycles. Max latency is 2*WIDTH+1.
          // Thus, counter (which is cycles after start) must be <= 2*WIDTH.
          assert(p_wcl_counter <= 2*WIDTH);
          p_wcl_counter <= p_wcl_counter + 1;
        end
      end else begin
        if (ready_o && start_i) begin
          p_wcl_active <= 1'b1;
          p_wcl_counter <= '0;
        end
      end
    end
  end

`endif

endmodule
`default_nettype wire