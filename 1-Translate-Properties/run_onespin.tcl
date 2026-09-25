set script_path [file dirname [file normalize [info script]]]

read_verilog -golden -version sv2012 {$script_path/../rtl/gcd_binary.sv}

set_elaborate_option -verilog_parameter {WIDTH=4}
elaborate -golden

compile -golden 

set_mode mv

read_sva -version {sv2012} {$script_path/fv_gcd_binary.sva}

check -all [get_checks]