module gcd_binary #(
  parameter WIDTH = 32
) (
  input  logic             clk_i,
  input  logic             rst_i,
  input  logic             start_i,
  input  logic [WIDTH-1:0] a_i,
  input  logic [WIDTH-1:0] b_i,
  output logic [WIDTH-1:0] res_o,
  output logic             ready_o,
  output logic             done_o
);

  localparam SH_WIDTH = $clog2(WIDTH);

  typedef enum logic [2:0] {
    IDLE,
    INIT,
    TRIM_ZEROS,
    COMPUTE,
    DONE
  } state_t;

  state_t state_q, next_state;

  logic [WIDTH-1:0] a_q, b_q;
  logic [SH_WIDTH-1:0] shift_q;  // Counts common factors of 2

  // Output logic
  assign res_o = a_q << shift_q;
  assign ready_o = (state_q == IDLE);
  assign done_o = (state_q == DONE);

  // Update state
  always_ff @(posedge clk_i or posedge rst_i) begin
    if (rst_i)
      state_q <= IDLE;
    else
      state_q <= next_state;
  end

  // FSM next state logic
  always_comb begin
    case (state_q)
      IDLE:         next_state = start_i ? INIT : IDLE;
      INIT:         next_state = (a_q == '0 || b_q == '0) ? DONE : ((a_q[0] | b_q[0]) ? COMPUTE : TRIM_ZEROS);
      TRIM_ZEROS:   next_state = (a_q[1] | b_q[1]) ? COMPUTE : TRIM_ZEROS;
      COMPUTE:      next_state = (a_q == b_q) ? DONE : COMPUTE;
      DONE:         next_state = IDLE;
      default:      next_state = IDLE;
    endcase
  end

  // Registers and core logic
  always_ff @(posedge clk_i or posedge rst_i) begin
    if (rst_i) begin
      a_q <= '0;
      b_q <= '0;
      shift_q <= '0;
    end else begin
      case (state_q)
      IDLE: begin
        if (start_i) begin
          a_q <= a_i;
          b_q <= b_i;
          shift_q <= '0;
        end
      end
      INIT: begin
        // If either operand is zero, we skip computation and output the other operand
        if (a_q == '0) begin
          a_q <= b_q;
          b_q <= '0;
        end
      end
      TRIM_ZEROS: begin
        // Remove common factors of 2
        if ((a_q[0] == 1'b0) && (b_q[0] == 1'b0)) begin
          a_q <= a_q >> 1;
          b_q <= b_q >> 1;
          shift_q <= shift_q + 1'b1;
        end
      end
      COMPUTE: begin
        if (a_q[0] == 0) begin
          a_q <= a_q >> 1;
        end else if (b_q[0] == 1'b0) begin
          b_q <= b_q >> 1;
        end else if (a_q > b_q) begin
          a_q <= (a_q - b_q) >> 1;
        end else begin
          b_q <= (b_q - a_q) >> 1;
        end
      end
      DONE: begin
        // Output res_o = a_q << shift_q
      end
      default: begin
        // Should not occur
      end
      endcase
    end
  end

endmodule
