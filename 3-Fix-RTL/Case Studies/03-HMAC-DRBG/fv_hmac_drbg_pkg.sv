// -------------------------------------------------
// Copyright(c) LUBIS EDA GmbH, All rights reserved
// Contact: contact@lubis-eda.com
// -------------------------------------------------

`default_nettype none

package fv_hmac_drbg_pkg;

    typedef struct {
        bit unsigned [383:0] entropy;
        bit unsigned [383:0] nonce;
        bit init;
        bit next;
    } st_drbg_block;

    typedef struct {
        bit unsigned [383:0] key;
        bit unsigned [1023:0] block_msg;
        bit init;
        bit next;
    } st_hmac_block;


    // Constants

    parameter bit unsigned [383:0] HMAC_DRBG_PRIME = 384'hFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFC7634D81F4372DDF581A0DB248B0A77AECEC196ACCC52973;

    parameter bit unsigned [383:0] K_INIT = 384'h0;

    parameter bit unsigned [383:0] MASK = 384'hFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF;

    parameter bit unsigned [383:0] V_INIT = 384'h10101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101;


    // Functions

    function bit unsigned [1023:0] k10_func(bit unsigned [383:0] V, bit unsigned [7:0] cnt, bit unsigned [383:0] entropy, bit unsigned [383:0] nonce);
        return ((((((V << 384'd8) | cnt) << 392'd384) | entropy) << 776'd248) | (nonce >> 384'd136));
    endfunction

    function bit unsigned [1023:0] k11_func(bit unsigned [383:0] nonce);
        return (((137'((mask_nonce(nonce) << 384'd1)) | 137'd1) << 137'd887) | 1024'd2184);
    endfunction

    function bit unsigned [1023:0] k3_func(bit unsigned [383:0] V);
        return ((((((((V << 384'd8) | 392'd0) << 392'd1) | 393'd1) << 393'd619) | 1012'd0) << 1012'd12) | 1024'd1400);
    endfunction

    function bit unsigned [383:0] mask_nonce(bit unsigned [383:0] nonce);
        return (nonce & MASK);
    endfunction

    function bit unsigned [1023:0] v1_func(bit unsigned [383:0] V);
        return ((((V << 384'd1) | 385'd1) << 639'd639) | unsigned'((('sd0 << 'sd11) | 'sd1408)));
    endfunction

endpackage
