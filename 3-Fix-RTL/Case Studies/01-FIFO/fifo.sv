// address generator/counter
module addr_gen
#(  parameter MAX_DATA=17
) ( input en, clk, rst,
    output reg [3:0] addr
);
    initial addr = 0;

    // async reset
    // increment address when enabled
    always @(posedge clk or posedge rst)
        if (rst)
            addr <= 0;
        else if (en) begin
            if (addr == MAX_DATA-1)
                addr <= 0;
            else
                addr <= addr + 1;
        end
endmodule

// Define our top level fifo entity
module fifo
#(  parameter MAX_DATA=17
) ( input wen, ren, clk, rst,
    input [7:0] wdata,
    output [7:0] rdata,
    output [3:0] count,
    output full, empty
);
    wire wskip, rskip;
    reg [3:0] data_count;

    // fifo storage
    // async read, sync write
    wire [3:0] waddr, raddr;
    reg [7:0] data [MAX_DATA-1:0];
    always @(posedge clk)
        if (wen)
            data[waddr] <= wdata;
    assign rdata = data[raddr];
    // end storage

    // addr_gen for both write and read addresses
    addr_gen #(.MAX_DATA(MAX_DATA))
    fifo_writer (
        .en     (wen || wskip),
        .clk    (clk  ),
        .rst    (rst),
        .addr   (waddr)
    );

    addr_gen #(.MAX_DATA(MAX_DATA))
    fifo_reader (
        .en     (ren || rskip),
        .clk    (clk  ),
        .rst    (rst),
        .addr   (raddr)
    );

    // status signals
    initial data_count = 0;

    always @(posedge clk or posedge rst) begin
        if (rst)
            data_count <= 0;
        else if (wen && !ren && data_count < MAX_DATA)
            data_count <= data_count + 1;
        else if (ren && !wen && data_count > 0)
            data_count <= data_count - 1;
    end

    assign full  = data_count == MAX_DATA;
    assign empty = (data_count == 0) && ~rst;
    assign count = data_count;

    // overflow protection
`ifndef NO_FULL_SKIP
    // write while full => overwrite oldest data, move read pointer
    assign rskip = wen && !ren && data_count >= MAX_DATA;
    // read while empty => read invalid data, keep write pointer in sync
    assign wskip = ren && !wen && data_count == 0;
`else
    assign rskip = 0;
    assign wskip = 0;
`endif // NO_FULL_SKIP

endmodule
