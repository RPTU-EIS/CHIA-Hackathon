# APB4 formal run snapshot

`loop_result_shorter/apb4.sby` is the complete generated formal file; its
`[file apb4_formal.sv]` section contains the properties. The adjacent RTL
files are copies from the checked run, so the SBY can run from that directory.
The original RTL and simulation testbench are under `../AMPA_APB4_Protocol/`.
The testbench was not part of this formal run.

`loop_result_shorter/bug-report.txt` is Gemini's unedited report. Its three
entries repeat the same partial-write issue; they are not three independently
established bugs. BMC finished with FAIL at depth 10. A partial write can
overwrite unselected bytes, and some strobe cases also misplace selected
bytes. The report should be reviewed alongside `logs/bmc-final.log` and the
generated properties. This run does not prove the entire bus: the final
harness has no reset assertion, and the requester address assumption does
not constrain every SETUP cycle.

`loop_result_shorter/prompts/` and `responses/` hold the latest run's Gemini
exchanges. `logs/` holds the final tool logs. `archive/` retains earlier-run
exchanges and the final candidate copy; these are not additional results from
the latest run. The transient `.run.lock` was not copied.

To recheck the saved candidate in an environment with SymbiYosys installed,
run `sby -f apb4.sby bmc` from `loop_result_shorter/`; the expected outcome
against this RTL is FAIL. The loop script lives beside this note and expects
an existing CHIA/Ray cluster with a formal worker and Gemini credentials.
For new runs, set `APB4_RESULT_DIR` to an absolute writable shared directory
on your machine. Without it, this defaults to a `live_result_shorter/`
directory next to the script itself, separate from this snapshot.
