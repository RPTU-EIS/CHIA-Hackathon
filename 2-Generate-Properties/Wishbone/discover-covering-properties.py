"""Property-SYNTHESIS loop for the Wishbone RAM slave, evaluated by `mcy`
mutation-coverage delta instead of by fixing anything.

This is a fundamentally different shape from discover-spec-property.py in
this same directory: that script treats a sby FAIL as a bug to triage and
fix (DUT or property), across many rounds, converging on a single golden
property SET. This script never fixes anything - wishbone_ram.sv (the DUT)
is never touched, and a property that turns out to be wrong or that the
DUT doesn't satisfy is simply DISCARDED, not diagnosed. Its only job is to
grow wishbone_ram_checker.sv one property at a time, round by round, each
new property proposed by an LLM (grounded in wishbone_ram.sv and the
attached Wishbone B4 spec PDF) and then judged by an OBJECTIVE, external
signal rather than by how plausible its own justification sounds:

    Does adding this property to the checker make `mcy` (yosys' mutation-
    coverage tool) newly classify at least one synthetic RTL mutation as
    COVERED (caught) that the property set held before this round did not
    already catch?

A property that never flips any mutation from UNCOVERED to COVERED earned
nothing - discard it, whatever its prose reasoning claims - even a
property that is TRUE of the DUT can still be redundant with what's
already covered. This also folds in the usual "cover the antecedent" style
vacuity check for free: a vacuously-true property, by construction, never
distinguishes a mutated DUT from the real one on any BMC trace, so it can
never contribute a positive coverage delta either.

ROUND SHAPE
Round 1 establishes the harness itself (a checker module instantiating
wishbone_ram as a single instance named exactly `dut`, driving its inputs
as free signals) plus one first candidate property. Every later round
proposes exactly one additional property, inserted via an exact string
match on a fixed sentinel comment the harness is required to keep at the
end of the file. Each round, in order:
  1. A FAST mode-prove sanity gate (depth 5) - catches a broken/malformed
     edit cheaply before ever paying for a full mcy run. A syntax/
     elaboration error here gets a small number of narrow "fix the syntax
     only" retries (not a real diagnosis - see SYNTAX_FIX_PROMPT); a
     genuine FAIL here is not triaged, just discarded (see below).
  2. If that gate is clean (PASS or an inconclusive UNKNOWN - not a real
     counterexample, see mcy config's rule 6 note), a full `mcy run`
     against the current checker, and the round's coverage delta decides
     keep vs. discard.
A discarded candidate reverts the checker to the last accepted content
before moving to the next round; a candidate that added coverage becomes
the new accepted content and its coverage becomes the new baseline the
next round is compared against.

FILESYSTEM
Same lesson as 3-Fix-RTL/fix-rtl.py (see that file's own docstring for
the full incident writeup, including why --working-dir has no default):
the driver process (this script's own main()) has real NFS access to
--working-dir, but ONLY when given as a real absolute path - confirmed
directly that the driver's own OS working directory is Ray's ephemeral
per-job snapshot, not the directory you actually ran `chia job submit`
from. A `resources={"sby": 1}`-tagged remote worker does not share
either filesystem, even on the same physical node. So `read_files`/
`apply_replacements` here are plain LOCAL functions run directly by the
driver, never remote `@ChiaFunction`s - and every sby/mcy invocation takes
an explicit `file_contents: dict` argument, built fresh by the driver
right before each launch and passed through Ray's object store, written
into that task's own node-local working directory. Nothing is assumed to
be visible via a shared path. (discover-spec-property.py in this same
directory predates this fix and still uses the older
remote-read/remote-write/snapshot-back pattern; this script deliberately
does not reuse it.)

MUTATION SELECTION
mcy's config.mcy restricts mutation candidates to `a:src=wishbone_ram.sv:*`
- everything whose `-src` attribute traces back to the DUT's own source
file, never the checker's. An earlier version tried `wishbone_ram_checker/
dut.*` (dotted-hierarchy name matching, mirroring chia-debug-proc/
config.mcy's own selection, where bind-flattening naturally gives checker
cells a name prefix to exclude) - confirmed BROKEN by a real local
reproduction: post-`flatten`, only a handful of NAMED wires (port-driven
regs, the `mem` cell) ever carry a `dut.`-prefixed name; every anonymous
gate cell implementing the DUT's actual combinational logic does not, so
that selection style silently starved `mutate -list` down to almost
nothing (a size-30 run produced exactly one candidate - the design's own
unmutated baseline - and reported a meaningless "0.00% coverage" for a
real, non-vacuous property). Source-attribute selection reaches anonymous
cells too and needs no instance-name convention or exclusion trick at all
to keep the checker's own logic out (it's simply a different source file).
Confirmed working: the same setup that produced 0.00%/1-candidate under
the old selection produces a real 10.00% (4/40 mutations, all of them
directly on the gates feeding the property's own signals) under this one.

Every round genuinely needs a fresh `mcy init`, not just a fresh `mcy
run`: confirmed by reading mcy's own source (`create_mutated.sh`'s
default `-d`/`--design` is `database/design.il`, a frozen RTLIL snapshot
written once by `mcy init`'s own `design.ys` step) that every mutation
test elaborates from that ONE frozen snapshot, never re-reading
wishbone_ram_checker.sv from disk. Reusing a stale `database/` across
rounds (to dodge the sampling noise below, say) would silently test
whatever checker content existed at the LAST `mcy init` - not the
current round's - which is exactly why run_mcy() always wipes and
reinitializes fresh (see its own docstring).

Note: `mcy init`'s mutation sampling is NOT seeded/deterministic by
default (reseeded from the current time on every call) - this was
initially a real, documented noise source in the round-to-round coverage
comparison. Fixed by pinning a fixed `seed` in config.mcy (see
mcy_config_content()'s docstring for the empirical confirmation: two
`mcy init` runs with the same seed but deliberately different checker
content selected the exact same mutation targets - identical -src/-port/
-portbit/-mode for all of them, in the same order - because the
candidate pool is DUT-only and the DUT never changes across a run).
--mcy-seed can override it for an independent sample if you want to
sanity-check a result isn't an artifact of the one particular sample.

ELABORATION HANG
wishbone_ram.sv's own memory zero-init loop (a nested `for` writing all
2**VALID_ADDR_WIDTH = 16384 entries of `mem[]` to 0 in an `initial` block)
hangs yosys elaboration outright - confirmed empirically: plain
`read_verilog -sv; hierarchy -top wishbone_ram` on the unmodified file
never finished (99% CPU, several minutes, killed); with just that block
removed, the identical command completes in 0.037s. There's no
read_verilog flag to skip `initial`-block processing, so
verification_dut_content() strips this one, exact, known block from the
copy of the DUT handed to sby/mcy only - never from the real
wishbone_ram.sv on disk, and never from the copy shown to the LLM in
prompts (which sees the true, unmodified source). See that function's own
docstring for the BMC-soundness argument.

COST
Same call_llm_with_usage pattern as every other loop in this project
family - per-round and cumulative token usage/estimated $ cost, using the
same verified Vertex AI Gemini 2.5 Pro pricing.

TIMEOUTS (recovering from a stuck solver)
Every sby/mcy call is bounded (--sby-timeout / --mcy-init-timeout /
--mcy-run-timeout); see _run_streamed's docstring for why this exists and
why it kills the whole process group, not just the top-level process. A
timeout is treated as "error" by classify_sby_result (no `DONE (...)`
line ever appears) but explicitly flagged via a `.timed_out` attribute so
the syntax-fix retry loop is skipped - retrying can't fix a solver that's
just stuck on a hard query, only a genuine syntax error.

SPEC CHECKLIST (completeness feedback)
Mutation coverage alone can't tell you a whole category of spec behavior
was never attempted - it only scores properties that already exist.
Before round 1, extract_spec_rules() makes one LLM call to turn the PDF
into an explicit, trackable checklist of individual testable requirements
(id + one-sentence description). Every round's prompt shows this
checklist's current status (unaddressed / addressed / finding - see
FINDINGS below) and requires the candidate property to name which id it
targets, so "complete relative to the spec" becomes a checkable list
instead of something left to chance. If extraction fails to parse, the
loop still runs - rounds just propose freely against the raw spec text,
same as before this feature existed.

TWO-TIER KEEP CRITERION (coverage delta is not the only path)
A candidate is eligible to keep if EITHER it improves mutation coverage
(the original bar - still the right one for a property revisiting
already-addressed ground, since it's the mechanism that actually catches
redundancy) OR it targets a checklist item still marked "unaddressed"
(checklist_item_status()) and holds cleanly on the real DUT. Added after
a real 40-round run reached 97.5% mutation coverage from just two broad
properties (write-read coherency, byte-lane masking) and then discarded
every one of the following ~30 rounds for zero coverage delta -
regardless of which spec rule each one formalized - leaving 15 of 19
checklist items unaddressed despite the excellent-looking coverage
number. Mutation-coverage-of-this-RTL and completeness-against-the-spec
are genuinely different goals once coverage is already high; a narrow
property closing a real checklist gap is worth keeping on its own merits
even when the specific mutations it would catch are already caught by an
unrelated broader property. Non-vacuity (below) is required on BOTH
paths regardless.

FINDINGS (genuine spec violations, not silently discarded)
Earlier versions of this loop discarded every sby FAIL identically,
whether it meant "the property is wrong" or "the DUT genuinely violates
the spec" - throwing away exactly the signal most worth keeping when the
RTL's correctness is what's actually in question (unlike the proc/HMAC
case studies, this DUT is not assumed golden). On a genuine FAIL, a
triage LLM call (triage_fail_prompt) now decides, grounded in the
property's exact wording and the concrete counterexample trace: a real
spec violation (recorded in `findings`, reported prominently in the final
summary - this is a report-don't-patch step, matching
4-Security-Verification's philosophy, NOT a re-introduction of the
fixing loop this script deliberately doesn't have) or a malformed
property (discarded as before, no finding). Either way the property is
never left in the live checker as a permanently-failing assert - a real
violation would otherwise break every subsequent round's sanity gate.

NON-VACUITY (explicit, not just implied by coverage delta)
Every property is now required to come with a paired cover() on its own
trigger condition. After a candidate clears either keep path above, an
additional `mode cover` sby run (COVER_SBY_FILE) checks every cover() in
the accumulated checker is actually reachable - confirmed empirically
(local docker test) that sby's cover mode reports `DONE (FAIL, rc=2)`
with an explicit `Unreached cover statement at <module>: file:line` line
per vacuous cover, and `DONE (PASS` when all are reachable. A candidate
that's otherwise eligible but introduces an unreachable cover() is
reverted just the same - the coverage/checklist eligibility check and
explicit reachability are independent signals, neither implies the
other.

PLATEAU FEEDBACK
Consecutive discards (any reason) are counted; past a small threshold the
next round's prompt explicitly lists the checklist's still-unaddressed
ids as suggested targets, instead of leaving the LLM to notice a rut on
its own.
"""
from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.llm_call import QueryResult
from pathlib import Path
from typing import Optional
import subprocess
import json
import re
import os
import shutil
import time
import signal
import threading
import argparse
from chia.models.vertex import VertexGeminiLLM, RateLimitError, ServerError

# Falls back to this literal project if VERTEX_PROJECT isn't set - the GCP
# project this was originally developed against. Anyone else running this
# (e.g. a hackathon judge with their own GCP project/quota) should set
# VERTEX_PROJECT rather than edit this file; --vertex-project overrides
# both for a single invocation.
DEFAULT_VERTEX_PROJECT = "project-be5ca9cc-e88a-41a1-81f"

DUT_FILE = "wishbone_ram.sv"
CHECKER_FILE = "wishbone_ram_checker.sv"
DUT_TOP = "wishbone_ram"
CHECKER_TOP = "wishbone_ram_checker"
DUT_INSTANCE_NAME = "dut"
SPEC_PDF = "wbspec_b4.pdf"

SBY_FILE = "wishbone_ram.sby"               # fast per-round sanity gate
COVER_SBY_FILE = "wishbone_ram_cover.sby"   # explicit non-vacuity check
MCY_CONFIG_FILE = "config.mcy"              # mcy's own default filename
MCY_TEST_SBY = "test_bmc.sby"
MCY_TEST_SCRIPT = "test_bmc.sh"

# Past this many consecutive discards (any reason), the next round's
# prompt explicitly nudges toward the checklist's still-unaddressed ids
# instead of leaving the LLM to notice a rut on its own.
PLATEAU_THRESHOLD = 2

SENTINEL = "    // === NEW PROPERTIES INSERTED ABOVE THIS LINE ==="

# wishbone_ram.sv's own memory zero-init loop (2**VALID_ADDR_WIDTH = 16384
# entries) hangs yosys's elaboration - confirmed empirically: plain
# `read_verilog -sv; hierarchy -top wishbone_ram` on the unmodified file
# never finished (99% CPU for 2m50s+, killed); with just this block
# removed, the identical command completes in 0.037s. There is no
# read_verilog flag to skip `initial`-block processing, so
# verification_dut_content() strips this EXACT block from the copy handed
# to sby/mcy only - never from the real wishbone_ram.sv on disk, and never
# from the copy shown to the LLM (which should see the true DUT). This is
# BMC-sound: it only widens the state space BMC explores for `mem[]`'s
# initial contents (from "exactly zero" to "anything"), which can only
# ADD potential counterexamples, never hide a real one - the tradeoff is
# a possible false FAIL for some future property that specifically
# assumes mem starts zeroed before any write, which no currently-kept
# property does.
_DUT_MEM_INIT_BLOCK = (
    "initial begin\n"
    "    // two nested loops for smaller number of iterations per loop\n"
    "    // workaround for synthesizer complaints about large loop counts\n"
    "    for (i = 0; i < 2**VALID_ADDR_WIDTH; i = i + 2**(VALID_ADDR_WIDTH/2)) begin\n"
    "        for (j = i; j < i + 2**(VALID_ADDR_WIDTH/2); j = j + 1) begin\n"
    "            mem[j] = 0;\n"
    "        end\n"
    "    end\n"
    "end"
)


def verification_dut_content(dut_content: str) -> str:
    """The DUT content actually written for sby/mcy elaboration - see the
    _DUT_MEM_INIT_BLOCK note above. Falls back to the content unchanged
    (with a warning) if the exact block isn't found, since the DUT is
    meant to be static for the whole run and a silent mismatch here would
    otherwise reintroduce the hang without any visible explanation."""
    if _DUT_MEM_INIT_BLOCK in dut_content:
        return dut_content.replace(
            _DUT_MEM_INIT_BLOCK,
            "// (mem zero-init loop stripped for formal elaboration only - "
            "see verification_dut_content()'s docstring)")
    print("WARNING: expected mem zero-init block not found verbatim in "
          "wishbone_ram.sv; passing it unmodified to sby/mcy (this may "
          "reintroduce the multi-minute elaboration hang documented at "
          "_DUT_MEM_INIT_BLOCK)")
    return dut_content

DEFAULT_ROUNDS = 5
DEFAULT_MUTATION_SIZE = 40
DEFAULT_MCY_JOBS = 4
# Fixed by default so every round samples the SAME mutation targets (see
# mcy_config_content()'s own comment for the empirical confirmation of
# why this is safe here) - override via --mcy-seed for a genuinely
# independent sample, e.g. to sanity-check a result isn't an artifact of
# this one particular sample.
DEFAULT_MCY_SEED = 424242
MAX_SYNTAX_FIX_ATTEMPTS = 3

# Every normal round in real runs so far resolved in seconds; these are
# generous multiples of that, not a tight budget - the point is only to
# guarantee eventual recovery from a genuinely stuck solver (see
# _run_streamed's docstring), not to rush a legitimately-slower-than-usual
# but still-tractable check.
DEFAULT_SBY_TIMEOUT_SECONDS = 600
DEFAULT_MCY_INIT_TIMEOUT_SECONDS = 300
DEFAULT_MCY_RUN_TIMEOUT_SECONDS = 1800

USD_PER_1M_INPUT_TOKENS = 1.25
USD_PER_1M_OUTPUT_TOKENS = 10.00


TOOLCHAIN_RULES = "" \
"This is the same open-source toolchain (yosys + smtbmc, no `-verific` " \
"plugin) used for prior formal work in this project family, which hit " \
"several sharp edges. Apply these rules directly rather than " \
"rediscovering them by trial and error:\n\n" \
"1. Open-source yosys's native SVA support has NO concurrent-SVA " \
"operators at all: no `##`, `|->`, `|=>`, no `property`/`endproperty`, " \
"no `sequence`. Only a plain immediate `assert(expr)`/`assume(expr)`/" \
"`cover(expr)` inside an ordinary `always @(posedge clk)` block is " \
"understood. Write every property directly in this style.\n" \
"2. MOST IMPORTANT, and the one thing that will silently produce a " \
"MEANINGLESS result if skipped: DO NOT USE `bind` AT ALL. The checker " \
f"module ({CHECKER_TOP}) must directly instantiate {DUT_TOP} (as a " \
"single instance named EXACTLY `dut`) and contain all the assert/" \
"assume/cover statements itself, read together in the same " \
"`read_verilog` invocation - never bind a separate module across a " \
"different read, and never reference the DUT instance's INTERNAL " \
"(non-port) signals via dot-notation; only reference its real ports.\n" \
"3. Any auxiliary register you add purely for verification needs an " \
"explicit initial value (`initial x = 0;`). Without one, BMC/k-" \
"induction is free to pick an arbitrary power-up value and can use that " \
"freedom to fabricate a violation that never involved any real " \
"triggering event.\n" \
"4. To restrict properties to reachable (from-reset) executions, force " \
"a genuine reset at the start of every trace with an explicit `assume`, " \
"not merely a gate on \"having observed reset at some point\": " \
"`reg init; initial init = 1; always @(posedge clk) begin if (init) " \
"assume (<reset condition>); ... init <= 0; end`. Without this, BMC can " \
"explore a trace where reset is simply never asserted.\n" \
"5. Do not name an explicit solver on the `[engines]` line - plain " \
"`smtbmc` uses sby's default solver, the usually-fastest choice for " \
"this class of problem.\n" \
"6. Pair every `assert(expr)` with a `cover(<the assert's own trigger " \
"condition>)` - e.g. if you assert something conditioned on `ack_o`, " \
"also `cover(ack_o)`. This is checked separately afterward: a property " \
"whose trigger never actually fires on any real trace is vacuously " \
"true and worthless regardless of what its assert claims, and this is " \
"the mechanism that catches that.\n\n"


def checklist_section(checklist: list) -> str:
    """Render the spec-requirement checklist's current status for a
    prompt. Empty/absent checklist degrades gracefully - the loop still
    works, just without a completeness target to check candidates
    against (see extract_spec_rules())."""
    if not checklist:
        return "(no spec checklist available this run - ground your property in the spec directly)"
    return "\n".join(f"- [{item['status']}] {item['id']}: {item['description']}" for item in checklist)


def harness_prompt(checklist: list) -> str:
    return "" \
    "We're building a mutation-coverage-guided property-SYNTHESIS harness " \
    "for a Wishbone-slave RAM " \
    f"({DUT_FILE}, shown below) against its bus protocol specification (the " \
    "attached PDF - read it directly, including its timing diagrams and " \
    "signal tables, not just any text you can infer from context). This DUT " \
    "is a single-cycle, non-pipelined 'CLASSIC' Wishbone slave interface " \
    "(no burst/pipelined-feedback signals).\n\n" \
    "This is NOT a bug-fixing loop: the DUT will NEVER be edited by you or " \
    "by us, no matter what a property finds. Your only job, across many " \
    "rounds, is to author ONE new SVA property per round, added to a " \
    f"growing checker file ({CHECKER_FILE}). Each property's real value is " \
    "judged afterward by an independent tool (mcy, yosys' mutation-coverage " \
    "tool): does adding this property newly catch, as a FAIL, at least one " \
    "synthetic RTL mutation of the DUT that the property set held before " \
    "this round did not already catch? A property that sounds plausible but " \
    "never flips a single mutation from uncovered to covered earned nothing " \
    "and will be discarded, regardless of how reasonable its justification " \
    "sounds - and a property that is true of the DUT but redundant with " \
    "what's already covered earns exactly as little as one that's simply " \
    "wrong.\n\n" \
    "This is the FIRST round: establish the harness itself, plus your FIRST " \
    "candidate property.\n\n" \
    "Below is a checklist of individual, testable requirements extracted " \
    "from the spec ahead of time. Pick ONE unaddressed item to target with " \
    "your first property - this keeps the eventual property set traceable " \
    "against the spec instead of whatever occurs to you first:\n" \
    f"{checklist_section(checklist)}\n\n" \
    f"1. Instantiate {DUT_TOP} directly inside a new top module named " \
    f"exactly {CHECKER_TOP}, as a SINGLE instance named EXACTLY " \
    f"`{DUT_INSTANCE_NAME}` (lowercase, this literal name - e.g. " \
    f"`{DUT_TOP} #(...) {DUT_INSTANCE_NAME} (...);`) - a fixed, " \
    "predictable instance name keeps logs/debugging consistent across " \
    "rounds, do not use any other name.\n" \
    "2. Map the DUT's actual port names to the specification's canonical " \
    "signal names (note any polarity difference from the spec's convention, " \
    "such as an active-low reset named differently than the spec's " \
    "canonical name - a legitimate implementation choice, not a violation, " \
    "as long as it's handled correctly).\n" \
    "3. Drive every DUT input as a free/unconstrained `reg`, changed only by " \
    "the solver's exploration - do not constrain them beyond what's needed " \
    "for a genuine reset (see toolchain rule 4 below).\n" \
    "4. End the module with this EXACT sentinel comment, verbatim, " \
    "immediately before `endmodule` - every later round inserts a new " \
    "property directly above this line via an exact string match on it, so " \
    "it must appear in the file exactly once, exactly as written here " \
    "(including its leading whitespace):\n" \
    f"{SENTINEL}\n" \
    "5. Write your first property as a plain immediate assert/assume/cover " \
    "statement (see toolchain rule 1 below), placed directly above that " \
    "sentinel line, targeting the checklist item you picked. Ground it in " \
    "the specific part of the spec you can point to (e.g. \"the spec " \
    "states in its description of the ACK_O signal that...\") - do not " \
    "invent a property that sounds reasonable but isn't actually stated " \
    "or implied by the spec.\n\n" \
    + TOOLCHAIN_RULES + \
    "Respond with ONLY a JSON array of {\"file\": \"" + CHECKER_FILE + "\", " \
    "\"find\": \"\", \"replace\": ..., \"targets_rule\": \"<the checklist " \
    "id you targeted>\"} - a SINGLE entry creating the whole file from " \
    "scratch (empty \"find\" since it doesn't exist yet). No prose, no " \
    "markdown, nothing else."


def add_property_prompt(round_num: int, checker_content: str, current_coverage: Optional[float],
                          kept: list, discarded: list, uncovered_hints: list, checklist: list,
                          consecutive_no_progress: int) -> str:
    coverage_desc = f"{current_coverage:.2f}%" if current_coverage is not None else "not yet measured"
    kept_section = "\n".join(f"- {p}" for p in kept) if kept else "(none yet)"
    discarded_section = "\n".join(f"- {p}" for p in discarded) if discarded else "(none yet)"
    hints_section = (
        "\n\nSome RTL mutations the current property set still does NOT "
        "catch (id: mutate command, best-effort context - use as a hint "
        "for where to target your next property, not a requirement):\n"
        + "\n".join(f"- {h}" for h in uncovered_hints)
        if uncovered_hints else ""
    )
    unaddressed = [item for item in checklist if item["status"] == "unaddressed"]
    unaddressed_section = (
        "\n".join(f"- {item['id']}: {item['description']}" for item in unaddressed)
        if unaddressed else "(none - every checklist item has already been addressed or reported as a finding)"
    )
    plateau_section = ""
    if consecutive_no_progress >= PLATEAU_THRESHOLD and unaddressed:
        plateau_section = (
            f"\n\nThe last {consecutive_no_progress} rounds in a row made no "
            "progress (discarded for one reason or another). Rather than "
            "retrying a variation of something already tried, target one of "
            "the still-UNADDRESSED checklist items listed above specifically."
        )
    return (
        f"Round {round_num} of this property-synthesis loop. The checker "
        f"file below currently holds {len(kept)} propert(y/ies) that "
        "already earned their place. Current cumulative mutation "
        f"coverage: {coverage_desc}.\n\n"
        "IMPORTANT - how a property earns its place has TWO paths, not "
        "one: (a) it newly catches, as a FAIL, at least one synthetic "
        "RTL mutation the property set held before this round did not "
        "already catch (the original bar), OR (b) it targets a checklist "
        "item still marked 'unaddressed' below AND holds on the real DUT "
        "AND its cover() is reachable - path (b) does NOT require a "
        "mutation-coverage improvement. This exists because mutation "
        "coverage saturates fast on a design this size (confirmed: one "
        "run reached 97.5% coverage from just two broad properties, "
        "after which every later property - regardless of which spec "
        "rule it formalized - got discarded for zero coverage delta, "
        "leaving most of the checklist unaddressed even though coverage "
        "looked excellent). A narrow, specific property that closes a "
        "real gap in the checklist is valuable on its own merits, even "
        "if the exact mutations it would catch happen to already be "
        "caught by an unrelated broader property already kept.\n\n"
        "Given that, STRONGLY prefer targeting one of these still-"
        f"UNADDRESSED checklist items over refining/restating something "
        f"already addressed - each is a real gap:\n{unaddressed_section}\n\n"
        "Full checklist status for context:\n"
        f"{checklist_section(checklist)}\n\n"
        "Propose exactly ONE new candidate property to add, grounded in "
        "a specific part of the attached specification you can point to."
        + hints_section + plateau_section +
        "\n\nPreviously kept properties (do not just restate one of "
        f"these):\n{kept_section}\n\n"
        "Previously discarded properties and why (do not repeat one of "
        f"these either - each already earned nothing, or the DUT/spec "
        f"triage already went against it, or it broke the build):\n"
        f"{discarded_section}\n\n"
        "Add your property as a find/replace edit that inserts it "
        "directly above the sentinel line, keeping the sentinel line "
        "itself present afterward so future rounds can still find it:\n"
        f'"find": {json.dumps(SENTINEL)}\n'
        '"replace": "<your new assert/assume/cover statement(s), each '
        'inside its own always @(posedge clk) block if new, or added to '
        f'an existing one>\\n{SENTINEL}"\n'
        '"targets_rule": "<the checklist id you targeted>"\n\n'
        + TOOLCHAIN_RULES +
        f"Current {CHECKER_FILE} content:\n{checker_content}\n\n"
        "Respond with ONLY a JSON array containing this single edit - no "
        "prose, no markdown, nothing else."
    )


def syntax_fix_prompt(checker_content: str, sby_stdout: str, sby_stderr: str) -> str:
    return (
        f"Your last edit to {CHECKER_FILE} made sby fail to even run (a "
        "setup/syntax/elaboration error, shown below - NOT a property "
        "violation, and not a question of whether the property is "
        "correct). Propose a minimal fix to " + CHECKER_FILE + " ONLY "
        f"(never {DUT_FILE}) that gets it parsing/elaborating again, "
        "keeping your new property's intent the same if at all possible. "
        "If you cannot identify a fix with confidence, respond with an "
        "empty JSON array [] and this round's candidate will be "
        "discarded instead.\n\n"
        + TOOLCHAIN_RULES +
        f"Current {CHECKER_FILE} content:\n{checker_content}\n\n"
        f"sby stdout:\n{sby_stdout}\n\nsby stderr:\n{sby_stderr}\n\n"
        "Respond with ONLY a JSON array of {\"file\": \"" + CHECKER_FILE +
        "\", \"find\": ..., \"replace\": ...} edits (\"find\" must be an "
        "exact, unique, literal snippet copied verbatim from the current "
        "content above) - no prose, no markdown, nothing else."
    )


EXTRACT_SPEC_RULES_PROMPT = "" \
"We're about to build a formal property set for a Wishbone-slave RAM " \
"against the ATTACHED Wishbone B4 specification PDF (read it directly, " \
"including its tables and timing diagrams, not just text you can infer " \
"from context). Before writing any properties, extract a CHECKLIST of " \
"individual, testable normative requirements the spec states for a " \
"WISHBONE SLAVE interface. This DUT is a single-cycle, non-pipelined " \
"'CLASSIC' slave (no burst/pipelined-feedback signals) - skip " \
"requirements that only apply to a MASTER, or to burst/pipelined " \
"interfaces this DUT doesn't implement.\n\n" \
"For each requirement, give a short stable id (use the spec's own rule " \
"numbering if it has one, e.g. \"3.35\"; otherwise a short slug like " \
"\"ack-timing\") and a one-sentence description precise enough to write " \
"a property from later without re-reading the spec each time.\n\n" \
"Respond with ONLY a JSON array of {\"id\": ..., \"description\": ...} " \
"objects, ordered however you like - no prose, no markdown, nothing " \
"else."


def triage_fail_prompt(new_property_text: str, sby_stdout: str, trace: Optional[str]) -> str:
    trace_section = (
        f"=== Concrete counterexample testbench (the actual stimulus that "
        f"triggers this) ===\n{trace}"
        if trace else "(no counterexample testbench available for this result)"
    )
    return (
        "sby found a genuine counterexample (a real FAIL, not a setup "
        f"error) for the propert(y/ies) added this round, shown below. "
        f"Since the DUT ({DUT_FILE}) is NEVER edited by this loop, there "
        "are exactly two possible explanations - use the SPECIFICATION to "
        "decide which:\n\n"
        "(a) GENUINE SPEC VIOLATION: the property is a correct "
        "formalization of a real requirement, and the DUT's actual "
        "behavior breaks it. This is a real finding worth reporting - "
        "cite the specific spec requirement it breaks.\n"
        "(b) BAD PROPERTY: the property misreads the spec passage it was "
        "meant to encode (wrong polarity, missing a legitimate exception "
        "the spec allows, wrong signal, an assumption that isn't "
        "actually required). Cite the passage that shows the property "
        "is wrong.\n\n"
        "IMPORTANT KNOWN CAVEAT, check this FIRST before anything else: "
        "for elaboration-speed reasons, the copy of the DUT used for "
        "formal verification has its memory zero-init loop stripped (see "
        "verification_dut_content()'s docstring) - the real, synthesizable "
        "RTL DOES zero-initialize on power-up, but in THIS verification "
        "model, `mem[]` starts genuinely free/unconstrained. If the "
        "counterexample's failure depends on the CONTENTS of a memory "
        "location that was NEVER explicitly written earlier in the SAME "
        "trace (check this directly: scan the trace for a write to that "
        "exact address before the failing read), an assert like "
        "`!$isunknown(DAT_O)` or any check assuming a specific power-up "
        "value can fail purely from this verification-only "
        "approximation - that is NOT a real hardware bug, classify it as "
        "BAD PROPERTY (the property implicitly assumes something about "
        "an address's content that was never established in this trace, "
        "not a real spec violation). Only reads of addresses that WERE "
        "written earlier in the same trace are safe to judge normally.\n\n"
        "Ground your triage in the property's EXACT wording and the "
        "CONCRETE counterexample trace below - not a fresh re-derivation "
        "from memory of what the property 'should' do.\n\n"
        f"New propert(y/ies) added this round:\n{new_property_text}\n\n"
        + trace_section +
        f"\n\nsby stdout (tail):\n{sby_stdout[-3000:]}\n\n"
        "Respond in exactly this format:\n"
        "REASONING:\n<a few sentences - which case, and why, citing the "
        "specific spec passage that settles it>\n\n"
        "VERDICT:\n<exactly one word: VIOLATION or BAD_PROPERTY>"
    )


def verify_finding_prompt(new_property_text: str, initial_reasoning: str, sby_stdout: str,
                            trace: Optional[str]) -> str:
    trace_section = (
        f"=== Concrete counterexample testbench ===\n{trace}"
        if trace else "(no counterexample testbench available for this result)"
    )
    return (
        "A previous triage step concluded the counterexample below represents "
        "a GENUINE specification violation, not a bad property. Your job is to "
        "actively look for reasons that conclusion might be WRONG before it "
        "gets reported as a confirmed bug - this is the single most "
        "consequential output of this entire verification run (the DUT's "
        "correctness is exactly what's in question here, unlike a case where "
        "the RTL is already trusted golden), so it deserves real scrutiny, "
        "not a rubber stamp of the earlier call's own conclusion.\n\n"
        "Specifically rule out, one at a time, citing the spec directly:\n"
        "- Is the property demanding something STRICTER than the spec "
        "actually requires - e.g. treating ordinary ONE-CYCLE SYNCHRONOUS "
        "DESIGN LATENCY (a register write takes effect on the NEXT clock "
        "edge, never instantaneously/combinationally) as a violation, when a "
        "standard synchronous implementation with that latency is what most "
        "real Wishbone slaves do and is a completely normal reading of the "
        "spec's intent?\n"
        "- Does the property check the EXACT signal/storage the spec names, "
        "or something else that merely happened to also change in the trace "
        "(e.g. a read-data or acknowledge register updating for its own, "
        "unrelated reason, mistaken for the specific storage/signal the "
        "property claims changed)? Trace the counterexample's stimulus "
        "cycle by cycle against the property's exact asserted expression to "
        "check this, rather than trusting the earlier summary of it.\n"
        "- Is there a legitimate exception, qualifier, or alternate-but-"
        "compliant behavior described elsewhere in the spec that the "
        "property or the earlier triage missed?\n"
        "- KNOWN CAVEAT specific to this project: the verification model's "
        "copy of the DUT has its memory zero-init loop stripped for "
        "elaboration-speed reasons (real hardware DOES zero-initialize; "
        "this formal model does not - see verification_dut_content()'s "
        "docstring). If the failure depends on the contents of a memory "
        "location that was NEVER explicitly written earlier in the SAME "
        "trace, an unknown/'X' value there is an artifact of this "
        "approximation, not a real bug - scan the trace directly for a "
        "prior write to that exact address before trusting a value-"
        "dependent assertion on a read from it. A read of an address "
        "that WAS written earlier in the same trace is not affected by "
        "this and should be judged normally.\n\n"
        f"Original triage reasoning:\n{initial_reasoning}\n\n"
        f"Propert(y/ies):\n{new_property_text}\n\n"
        + trace_section +
        f"\n\nsby stdout (tail):\n{sby_stdout[-3000:]}\n\n"
        "Respond in exactly this format:\n"
        "REASONING:\n<a few sentences - does the original verdict hold up "
        "to this scrutiny, and why, citing the spec directly - if you "
        "disagree with the earlier call, say specifically where its "
        "reasoning went wrong>\n\n"
        "VERDICT:\n<exactly one word: CONFIRMED or NOT_CONFIRMED>"
    )


SUMMARY_PROMPT = "" \
"The automated Wishbone-slave property-SYNTHESIS run for this design has " \
"finished. Below is the round-by-round history: which property was " \
"proposed each round, whether it was kept (it newly caught at least one " \
"RTL mutation, and every cover() in the checker stayed reachable) or " \
"discarded (it broke the build, the DUT/spec triage went against it, it " \
"introduced a vacuous/unreachable cover, or it added zero mutation-" \
"coverage); the coverage percentage after each round; the final spec-" \
"requirement checklist status; and any genuine spec-violation FINDINGS " \
"(a property that was a correct formalization of a real requirement, but " \
"the DUT's actual behavior broke it - these are the run's most important " \
"output if any exist, distinct from ordinary discards).\n\n" \
"Write a concise final summary in plain English - a few short " \
"paragraphs, no JSON, no markdown formatting - covering:\n" \
"1. Any genuine spec-violation FINDINGS first and prominently, if any " \
"exist - what requirement was broken, and what the counterexample " \
"showed. If there are none, say so plainly (a clean run is a real, " \
"useful result too).\n" \
"2. What the final kept property set actually checks, in your own " \
"words (not just restating the log), how mutation coverage progressed " \
"round to round, and how much of the extracted spec checklist ended up " \
"addressed vs. still unaddressed - use the EXACT \"X/Y addressed\" count " \
"given below verbatim, do not recount the checklist list yourself (it's " \
"easy to miscount a list by eye; the given count is authoritative).\n" \
"3. Anything notable about what got discarded and why - a property " \
"that seemed reasonable but was redundant with something already " \
"covered, or vacuous despite passing its assert, is a genuinely useful " \
"negative result here, not a failure.\n" \
"4. Whether this run's final coverage number AND checklist completeness " \
"suggest there's meaningful headroom left, or whether the checked " \
"properties already explain most of what's both spec-relevant and " \
"mutation-distinguishable for this design.\n\n" \
"Write this summary NO MATTER THE RESULT - including if no property " \
"was ever kept. This is a closing report for someone who did not watch " \
"the run happen live, not a new proposal - do not propose further " \
"edits or JSON here."


def extract_json_array(text: str) -> Optional[list]:
    """Pull a JSON array of edits out of *text*. Tolerates a fenced code
    block or stray prose around the JSON. Returns None on parse failure,
    distinct from a valid empty [] (a deliberate "discard" signal)."""
    text = text.strip()
    candidates = []
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        candidates.append(match.group(1))
    candidates.append(text)
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    return None


def _run_streamed(cmd, on_line, cwd=None, env=None, timeout_seconds=None):
    """Run *cmd* to completion, calling on_line(line) for each line of its
    live merged stdout+stderr. Returns (returncode, combined_output,
    timed_out).

    If timeout_seconds is given and exceeded, the ENTIRE process GROUP is
    killed, not just the top-level process - sby spawns yosys-smtbmc,
    which spawns the actual SMT solver (yices/z3/...) as a further child;
    killing only the top process leaves those orphaned, silently burning
    a full CPU core forever on the worker node. Confirmed via a real
    incident: a single hard property's sanity-gate solver ran for 38+
    hours with zero progress, and stopping the *driving* Ray job (a
    separate dispatch from this remote call) did nothing to it - the
    orphaned yices process had to be hunted down and killed by hand
    inside the worker container afterward. `preexec_fn=os.setsid` makes
    this process a new session/process-group leader so `os.killpg` here
    reliably reaches the whole tree, however deep."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1, cwd=cwd, env=env,
                             preexec_fn=os.setsid if timeout_seconds else None)
    timed_out = {"value": False}
    timer = None
    if timeout_seconds is not None:
        def _kill_on_timeout():
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                timed_out["value"] = True
            except ProcessLookupError:
                pass  # already exited on its own - not a timeout
        timer = threading.Timer(timeout_seconds, _kill_on_timeout)
        timer.start()

    lines = []
    for line in proc.stdout:
        lines.append(line)
        on_line(line)
    proc.wait()
    if timer is not None:
        timer.cancel()
    if timed_out["value"]:
        on_line(f"\n[TIMED OUT after {timeout_seconds}s - process group killed]\n")
    return proc.returncode, "".join(lines), timed_out["value"]


def read_files(base_dir: Path, filenames: list) -> dict:
    """Plain LOCAL read from the driver's own NFS-mounted directory - see
    the module docstring's FILESYSTEM note for why this must not be a
    remote ChiaFunction. None for a file that doesn't exist yet."""
    result = {}
    for name in filenames:
        path = base_dir / name
        result[name] = path.read_text(encoding="utf-8") if path.exists() else None
    return result


def apply_replacements_locally(base_dir: Path, content: str, replacements: list) -> str:
    """Apply {find, replace} edits to *content* in memory (no disk I/O -
    the caller decides whether/when to persist), matching the same
    find/replace edit format used everywhere else in this project.
    Raises ValueError with a clear message if a "find" isn't present."""
    for r in replacements:
        find, replace = r.get("find", ""), r["replace"]
        if find:
            if find not in content:
                raise ValueError(f"find text not present in {CHECKER_FILE}: {find!r}")
            content = content.replace(find, replace)
        else:
            content = replace
    return content


def write_files(base_dir: Path, files: dict):
    for name, content in files.items():
        (base_dir / name).write_text(content, encoding="utf-8")


MAX_LLM_RETRIES = 3
LLM_RETRY_BASE_DELAY_SECONDS = 20


@ChiaFunction(resources={"vertex_creds": 0.01})
def call_llm_with_usage(llm, prompt_text: str, pdf_bytes: Optional[bytes] = None):
    """Call VertexGeminiLLM.prompt() as a plain synchronous call from
    INSIDE this already-remote function, then read back llm._last_metadata
    in the SAME process right after - the only way to get real token-usage
    numbers back to the driver (a bare .chia_remote() dispatch of prompt()
    itself silently discards them on the worker's own copy of `llm`).

    RateLimitError/ServerError (chia.models.vertex) are specifically
    transient (HTTP 429/5xx) - confirmed via a real 20-round run where 3
    rounds (15% of the round budget) were silently burned by a
    RateLimitError that a short retry would very likely have ridden out,
    each one costing a whole round's worth of sby/mcy compute for
    nothing since the caller can't tell "LLM never responded" apart from
    "LLM responded with unparseable JSON" without this. Retried here,
    bounded, with linear backoff; any other exception (a real content
    block, a malformed request, ...) is NOT retried - re-sending the
    same prompt would just fail the same way - and still degrades to an
    empty, unsuccessful QueryResult so one bad attempt doesn't kill the
    whole multi-hour job."""
    last_exc = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            result = llm.prompt(prompt_text, pdf_bytes=pdf_bytes)
            meta = getattr(llm, "_last_metadata", {}) or {}
            usage = {
                "input_tokens": meta.get("input_tokens", 0),
                "output_tokens": meta.get("output_tokens", 0),
            }
            return result, usage
        except (RateLimitError, ServerError) as exc:
            last_exc = exc
            if attempt == MAX_LLM_RETRIES:
                break
            delay = LLM_RETRY_BASE_DELAY_SECONDS * attempt
            print(f"WARNING: transient LLM error on attempt {attempt}/{MAX_LLM_RETRIES} "
                  f"({exc!r}); retrying in {delay}s...")
            time.sleep(delay)
        except Exception as exc:
            print(f"WARNING: LLM call failed ({exc!r}); treating this attempt as a no-op")
            empty = QueryResult(result="", returncode=-1, stderr=str(exc), stream_result="", success=False)
            return empty, {"input_tokens": 0, "output_tokens": 0}
    print(f"WARNING: LLM call still failing after {MAX_LLM_RETRIES} attempts ({last_exc!r}); "
          "treating this attempt as a no-op")
    empty = QueryResult(result="", returncode=-1, stderr=str(last_exc), stream_result="", success=False)
    return empty, {"input_tokens": 0, "output_tokens": 0}


RUN_USAGE_TOTALS = {"input_tokens": 0, "output_tokens": 0, "num_calls": 0}


def call_llm(llm, prompt_text: str, pdf_bytes: Optional[bytes] = None):
    resp, usage = get(call_llm_with_usage.chia_remote(llm, prompt_text, pdf_bytes=pdf_bytes))
    RUN_USAGE_TOTALS["input_tokens"] += usage.get("input_tokens", 0)
    RUN_USAGE_TOTALS["output_tokens"] += usage.get("output_tokens", 0)
    RUN_USAGE_TOTALS["num_calls"] += 1
    return resp, usage


def format_usage(usage_totals: dict) -> str:
    input_tokens = usage_totals.get("input_tokens", 0)
    output_tokens = usage_totals.get("output_tokens", 0)
    cost = (input_tokens / 1_000_000) * USD_PER_1M_INPUT_TOKENS \
        + (output_tokens / 1_000_000) * USD_PER_1M_OUTPUT_TOKENS
    prefix = f"{usage_totals['num_calls']} call(s), " if "num_calls" in usage_totals else ""
    return f"{prefix}{input_tokens} input tok, {output_tokens} output tok, ~${cost:.4f}"


@ChiaFunction(resources={"sby": 1})
def run_sby(sby_file: str, file_contents: dict, timeout_seconds: int):
    """Run an sby task (the fast sanity gate, or the cover-mode non-
    vacuity check - same mechanics either way), written into a fresh
    node-local flat directory (no cwd override, no NFS assumption - see
    FILESYSTEM note). Also captures sby's own generated counterexample
    testbench, if any, as a `.trace` attribute on the returned
    CompletedProcess (plain attribute assignment - subprocess.
    CompletedProcess has no __slots__, so this works fine) - grounds the
    triage step (triage_fail_prompt) in the actual concrete stimulus that
    triggered a FAIL rather than a re-derivation from memory. sby names
    the task directory after the .sby file's stem; tries the BMC/
    basecase testbench first (a "fail" result), then the induction one
    (an "unknown" result) - None if neither exists (e.g. on a pass).

    timeout_seconds bounds the whole call - see _run_streamed's own
    docstring for why this exists. A timeout produces output with no
    `DONE (...)` line, so classify_sby_result's existing fallback already
    reports it as "error" with no special-casing needed there; `.timed_out`
    is set explicitly too so callers can skip the syntax-fix retry loop
    (retrying can't fix a solver that's just stuck on a hard query, only
    a genuine setup/syntax error - see main())."""
    for name, content in file_contents.items():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    cmd = ["/opt/oss-cad-suite/bin/sby", "-f", sby_file]
    returncode, output, timed_out = _run_streamed(cmd, lambda line: print(line, end=""),
                                                    timeout_seconds=timeout_seconds)
    result = subprocess.CompletedProcess(cmd, returncode, output, "")
    result.timed_out = timed_out

    task_dir = Path(sby_file).stem
    trace = None
    for name in ("trace_tb.v", "trace_induct_tb.v"):
        trace_path = Path(task_dir) / "engine_0" / name
        if trace_path.exists():
            trace = trace_path.read_text(encoding="utf-8")
            break
    result.trace = trace
    return result


@ChiaFunction(resources={"sby": 1})
def run_mcy(file_contents: dict, mutation_size: int, jobs: int, init_timeout_seconds: int,
            run_timeout_seconds: int):
    """Full mcy init+run pass, in its own fresh node-local flat directory.
    Returns a dict: coverage_pct (float or None if unparseable), the
    combined stdout, a best-effort list of still-uncovered mutations'
    raw database lines (never load-bearing - wrapped so a CLI/format
    surprise here degrades to an empty hint list, not a crash), and
    timed_out (see run_sby's docstring for why this exists). init and
    run get SEPARATE bounds since their expected durations are wildly
    different (init should be near-instant elaboration; run is the full
    mutation-testing sweep) - `mcy run`'s bound also covers whatever
    individual mutation-test solver invocations mcy spawns internally,
    since they're descendants of this same process group."""
    for name, content in file_contents.items():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # A worker process (and its cwd) can be reused by Ray across rounds -
    # without this, a stale `database/`/`tasks/` dir left over from an
    # earlier round's mcy invocation on the same worker makes `mcy init`
    # fail outright ("Found existing database directory").
    for stale in ("database", "tasks"):
        shutil.rmtree(stale, ignore_errors=True)

    init_rc, init_out, init_timed_out = _run_streamed(
        ["/opt/oss-cad-suite/bin/mcy", "init"], lambda line: print(line, end=""),
        timeout_seconds=init_timeout_seconds)
    run_rc, run_out, run_timed_out = _run_streamed(
        ["/opt/oss-cad-suite/bin/mcy", "run", "-j", str(jobs)],
        lambda line: print(line, end=""), timeout_seconds=run_timeout_seconds)
    timed_out = init_timed_out or run_timed_out
    combined = init_out + run_out

    coverage_pct = None
    m = re.search(r"Mutation coverage:\s*([\d.]+)%", combined)
    if m:
        coverage_pct = float(m.group(1))

    uncovered_hints = []
    try:
        _, list_out, _ = _run_streamed(["/opt/oss-cad-suite/bin/mcy", "list", "UNCOVERED"],
                                        lambda line: None, timeout_seconds=60)
        ids = [ln.split(":", 1)[0].strip() for ln in list_out.splitlines()
               if ln.split(":", 1)[0].strip().isdigit()]
        mutations_txt = Path("database/mutations.txt")
        if mutations_txt.exists() and ids:
            lines_by_id = {}
            for line in mutations_txt.read_text(encoding="utf-8").splitlines():
                if ":" in line:
                    mid = line.split(":", 1)[0].strip()
                    lines_by_id[mid] = line.strip()
            for mid in ids[:15]:
                if mid in lines_by_id:
                    uncovered_hints.append(lines_by_id[mid])
    except Exception as exc:
        print(f"WARNING: could not collect uncovered-mutation hints ({exc!r}); continuing without them")

    return {
        "init_returncode": init_rc,
        "run_returncode": run_rc,
        "coverage_pct": coverage_pct,
        "stdout": combined,
        "uncovered_hints": uncovered_hints,
        "timed_out": timed_out,
    }


def classify_sby_result(response) -> str:
    if "DONE (PASS" in response.stdout:
        return "pass"
    if "DONE (FAIL" in response.stdout:
        return "fail"
    if "DONE (UNKNOWN" in response.stdout:
        return "unknown"
    return "error"


def sby_content() -> str:
    return (
        "[tasks]\nprove\n\n"
        "[options]\nmode prove\ndepth 5\n\n"
        "[engines]\nsmtbmc\n\n"
        "[script]\n"
        f"read_verilog -formal -sv {DUT_FILE} {CHECKER_FILE}\n"
        f"prep -top {CHECKER_TOP}\n\n"
        f"[files]\n{DUT_FILE}\n{CHECKER_FILE}\n"
    )


def cover_sby_content() -> str:
    """Explicit non-vacuity check: `mode cover` fails the whole task if
    ANY cover() in the checker is unreachable, reporting exactly which
    one - confirmed empirically (local docker test) via `DONE (FAIL,
    rc=2)` plus an `Unreached cover statement at <module>: file:line`
    line per vacuous cover, vs. `DONE (PASS` when all are reachable."""
    return (
        "[tasks]\ncov\n\n"
        "[options]\nmode cover\ndepth 10\n\n"
        "[engines]\nsmtbmc\n\n"
        "[script]\n"
        f"read_verilog -formal -sv {DUT_FILE} {CHECKER_FILE}\n"
        f"prep -top {CHECKER_TOP}\n\n"
        f"[files]\n{DUT_FILE}\n{CHECKER_FILE}\n"
    )


def mcy_config_content(mutation_size: int, seed: int) -> str:
    return (
        "[options]\n"
        f"size {mutation_size}\n"
        f"# Fixed (not auto-generated) seed: `mutate -list` is otherwise\n"
        f"# reseeded from the current time on every `mcy init`, so two\n"
        f"# rounds' coverage numbers would be sampled from DIFFERENT\n"
        f"# mutation subsets - a real noise source (see module docstring's\n"
        f"# note on this). Confirmed via a real local diff (two `mcy init`\n"
        f"# runs, same seed, deliberately DIFFERENT checker content each\n"
        f"# time) that a fixed seed reproduces the exact same mutation\n"
        f"# TARGETS round to round (identical -src/-port/-portbit/-mode\n"
        f"# for all {mutation_size}, in the same order) even though the\n"
        f"# checker changed - only yosys's internal auto-numbered cell\n"
        f"# names shifted (cosmetic, from more/fewer checker-side cells\n"
        f"# existing ahead of them at elaboration time), which doesn't\n"
        f"# affect what's being mutated. This is safe specifically because\n"
        f"# the candidate pool (`a:src={DUT_FILE}:*`) is DUT-only and the\n"
        f"# DUT never changes across a run - re-seeding is what would be\n"
        f"# needed if the pool itself could shift.\n"
        f"seed {seed}\n"
        f"# DUT-only mutation candidates, filtered by SOURCE FILE\n"
        f"# attribute rather than dotted-hierarchy name matching - see\n"
        f"# module docstring's MUTATION SELECTION note. Confirmed\n"
        f"# empirically (local docker repro) that a `{CHECKER_TOP}/\n"
        f"# {DUT_INSTANCE_NAME}.*`-style dotted select only ever matches\n"
        f"# a handful of NAMED wires post-flatten (port-driven regs like\n"
        f"# ack_o_reg, the mem cell itself) - every anonymous gate cell\n"
        f"# yosys generates for the DUT's actual combinational logic\n"
        f"# (the AND/NOT/mux cells that do the real work) never gets a\n"
        f"# `{DUT_INSTANCE_NAME}.`-prefixed name, so that selection style\n"
        f"# silently starved `mutate -list` down to almost nothing. This\n"
        f"# is an `a:` (attribute) selection instead, matching every\n"
        f"# object (named or anonymous) whose `-src` attribute starts\n"
        f"# with {DUT_FILE!r} - naturally excludes the checker's own\n"
        f"# logic too (a different source file), no separate diff/%d\n"
        f"# exclusion needed.\n"
        f"select a:src={DUT_FILE}:*\n"
        "tags COVERED UNCOVERED\n\n"
        "[script]\n"
        f"read_verilog -formal -sv {DUT_FILE} {CHECKER_FILE}\n"
        f"prep -top {CHECKER_TOP}\n"
        "flatten\n\n"
        f"[files]\n{DUT_FILE}\n{CHECKER_FILE}\n\n"
        "[logic]\n"
        'if result("bmc") == "FAIL":\n'
        '    tag("COVERED")\n'
        "else:\n"
        '    tag("UNCOVERED")\n\n'
        "[report]\n"
        'if tags("COVERED")+tags("UNCOVERED"):\n'
        '    print("Mutation coverage: %.2f%%" % (100.0*tags("COVERED")/(tags("COVERED")+tags("UNCOVERED"))))\n\n'
        "[test bmc]\n"
        "expect PASS FAIL\n"
        f"run bash $PRJDIR/{MCY_TEST_SCRIPT}\n"
    )


def mcy_test_script_content() -> str:
    return (
        "#!/bin/bash\n"
        "exec 2>&1\n"
        "set -ex\n"
        "bash $SCRIPTS/create_mutated.sh -o mutated.il\n"
        f"ln -s ../../{MCY_TEST_SBY} .\n"
        f"sby -f {MCY_TEST_SBY}\n"
        f'awk "{{ print 1, \\$1; }}" {Path(MCY_TEST_SBY).stem}/status >> output.txt\n'
        "exit 0\n"
    )


def mcy_test_sby_content() -> str:
    return (
        "[options]\nmode bmc\ndepth 20\nexpect pass,fail\n\n"
        "[engines]\nsmtbmc --keep-going bitwuzla\n\n"
        "[script]\nread_rtlil mutated.il\n\n"
        "[files]\nmutated.il\n"
    )


def build_llm(vertex_project: str):
    return VertexGeminiLLM(model="gemini-2.5-pro", project=vertex_project, location="global",
                           timeout_seconds=3600, max_tokens=65536)


def apply_edits_guarded(replacements: list) -> list:
    """Hard guardrail: only CHECKER_FILE may ever be touched by this loop
    - never wishbone_ram.sv, no matter what the LLM proposes. Rejects
    (loudly) rather than silently dropping."""
    accepted, rejected = [], []
    for r in replacements:
        if r.get("file") == CHECKER_FILE:
            accepted.append(r)
        else:
            rejected.append(r)
    for r in rejected:
        print(f"WARNING: rejected an edit targeting {r.get('file')!r} - "
              f"this loop may only ever edit {CHECKER_FILE}")
    return accepted


def extract_spec_rules(llm, pdf_bytes: bytes) -> list:
    """One-time LLM call turning the spec PDF into an explicit, trackable
    checklist (see module docstring's SPEC CHECKLIST note). Degrades
    gracefully to an empty list (rounds propose freely against the raw
    spec, same as before this feature existed) if parsing fails - a
    missing checklist should never be fatal to the run."""
    resp, usage = call_llm(llm, EXTRACT_SPEC_RULES_PROMPT, pdf_bytes=pdf_bytes)
    print(f"LLM usage this call: {format_usage(usage)}")
    parsed = extract_json_array(resp.result)
    if not parsed:
        print("WARNING: could not parse a spec checklist from the LLM's "
              "response; proceeding without one (no completeness "
              "checklist to track candidates against this run)")
        return []
    checklist = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id", "")).strip()
        desc = str(item.get("description", "")).strip()
        if rid and desc:
            checklist.append({"id": rid, "description": desc, "status": "unaddressed"})
    print(f"Extracted {len(checklist)} spec checklist item(s):")
    print(checklist_section(checklist))
    return checklist


def mark_checklist(checklist: list, rule_id: str, status: str) -> bool:
    """Update the checklist item matching rule_id in place. Returns False
    (a no-op, not an error) if rule_id doesn't match anything - the LLM
    may echo back an id that doesn't exactly match the checklist's, which
    shouldn't crash the run, just leave completeness tracking a little
    less precise for that round."""
    for item in checklist:
        if item["id"] == rule_id:
            item["status"] = status
            return True
    return False


def checklist_item_status(checklist: list, rule_id: str) -> Optional[str]:
    """The current status of rule_id, or None if it doesn't match any
    checklist item (a fabricated/mismatched id from the LLM, or no
    checklist this run) - never crashes, just means the caller treats it
    as "not a known checklist item"."""
    for item in checklist:
        if item["id"] == rule_id:
            return item["status"]
    return None


_UNREACHED_COVER_RE = re.compile(r"Unreached cover statement at [^:]+:\s*(\S+)")


def parse_unreached_covers(sby_stdout: str) -> list:
    """Best-effort extraction of which cover()s were unreachable, from
    sby's own `mode cover` summary lines - never load-bearing for the
    pass/fail decision itself (that's classify_sby_result on the same
    response), just extra context for the log/history entry."""
    return _UNREACHED_COVER_RE.findall(sby_stdout)


def main():
    parser = argparse.ArgumentParser(description="Property-synthesis loop for the Wishbone RAM slave, "
                                                   "judged by mcy mutation-coverage delta.")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS,
                         help="Number of candidate properties to attempt (default: %(default)s).")
    parser.add_argument("--mutation-size", type=int, default=DEFAULT_MUTATION_SIZE,
                         help="mcy mutation sample size per round (default: %(default)s).")
    parser.add_argument("--mcy-jobs", type=int, default=DEFAULT_MCY_JOBS,
                         help="Parallel mcy test jobs (default: %(default)s).")
    parser.add_argument("--mcy-seed", type=int, default=DEFAULT_MCY_SEED,
                         help="Fixed mcy mutation-sampling seed, so every round samples the same "
                              "targets (default: %(default)s; see mcy_config_content()'s docstring).")
    parser.add_argument("--sby-timeout", type=int, default=DEFAULT_SBY_TIMEOUT_SECONDS,
                         help="Per-call timeout in seconds for the sanity-gate/cover-check sby runs "
                              "(default: %(default)s; see _run_streamed's docstring for why this "
                              "exists).")
    parser.add_argument("--mcy-init-timeout", type=int, default=DEFAULT_MCY_INIT_TIMEOUT_SECONDS,
                         help="Timeout in seconds for `mcy init` (default: %(default)s).")
    parser.add_argument("--mcy-run-timeout", type=int, default=DEFAULT_MCY_RUN_TIMEOUT_SECONDS,
                         help="Timeout in seconds for `mcy run` (default: %(default)s).")
    parser.add_argument("--working-dir", required=True,
                         help="Absolute path to THIS machine's real checkout of this project "
                              "(e.g. the same directory you ran `chia job submit --working-dir .` "
                              "from). Deliberately has no default: the driver's own OS working "
                              "directory is Ray's ephemeral per-job snapshot, NOT this real path "
                              "(confirmed directly - a file written there never appears back on "
                              "disk once the job ends), so there is no safe value to fall back to "
                              "automatically.")
    parser.add_argument("--vertex-project", default=os.environ.get("VERTEX_PROJECT", DEFAULT_VERTEX_PROJECT),
                         help="GCP project for Vertex AI Gemini calls (default: $VERTEX_PROJECT if "
                              "set, else the project this was developed against).")
    args = parser.parse_args()

    working_dir = Path(args.working_dir)
    llm = build_llm(args.vertex_project)
    spec_pdf_bytes = (working_dir / SPEC_PDF).read_bytes()
    print(f"Loaded {SPEC_PDF} ({len(spec_pdf_bytes)} bytes) for native attachment to spec-grounded prompts")

    static_files = {
        SBY_FILE: sby_content(),
        COVER_SBY_FILE: cover_sby_content(),
        MCY_CONFIG_FILE: mcy_config_content(args.mutation_size, args.mcy_seed),
        MCY_TEST_SCRIPT: mcy_test_script_content(),
        MCY_TEST_SBY: mcy_test_sby_content(),
    }
    write_files(working_dir, static_files)
    print(f"Wrote static harness files ({', '.join(static_files)}) to {working_dir}")

    dut_content = read_files(working_dir, [DUT_FILE])[DUT_FILE]
    if dut_content is None:
        raise FileNotFoundError(f"{DUT_FILE} not found in {working_dir}")
    # dut_content (unmodified) goes into LLM prompts; verification_dut
    # (mem-init loop stripped) goes into every sby/mcy file_contents dict -
    # see verification_dut_content()'s docstring.
    verification_dut = verification_dut_content(dut_content)

    print(f"\n{'=' * 80}\nExtracting spec checklist\n{'=' * 80}")
    checklist = extract_spec_rules(llm, spec_pdf_bytes)

    checker_content: Optional[str] = None
    last_good_checker: Optional[str] = None
    # Zero properties guarantees zero mutation coverage - starting this at
    # 0.0 (not None) means round 1's candidate must clear the same "did
    # coverage actually improve" bar every later round does, rather than
    # getting a free pass just because there's no prior round to compare
    # against.
    current_coverage: float = 0.0
    uncovered_hints: list = []
    kept: list = []
    discarded: list = []
    findings: list = []
    history: list = []
    consecutive_no_progress = 0

    round_num = 0
    while round_num < args.rounds:
        round_num += 1
        is_first = checker_content is None
        print(f"\n{'=' * 80}\nRound {round_num}/{args.rounds} "
              f"({'establishing harness + first property' if is_first else 'proposing a new property'})\n{'=' * 80}")

        if is_first:
            prompt = harness_prompt(checklist) + f"\n\n=== {DUT_FILE} ===\n{dut_content}"
        else:
            prompt = add_property_prompt(round_num, checker_content, current_coverage,
                                          kept, discarded, uncovered_hints, checklist,
                                          consecutive_no_progress) \
                + f"\n\n=== {DUT_FILE} ===\n{dut_content}"

        resp, usage = call_llm(llm, prompt, pdf_bytes=spec_pdf_bytes)
        print(f"LLM usage this call: {format_usage(usage)}")
        replacements = extract_json_array(resp.result)
        if not replacements:
            if getattr(resp, "success", True) is False:
                # call_llm_with_usage already printed the specific
                # exception (and retried transient ones) - this is an
                # empty QueryResult from a call that never got a real
                # response, not the LLM answering with bad JSON.
                print("WARNING: the LLM call itself failed this round (see error above, already "
                      "retried if transient); skipping this round")
                history.append(f"Round {round_num}: LLM call failed; skipped.")
            else:
                print("WARNING: could not parse a JSON edit from the LLM's response; skipping this round")
                history.append(f"Round {round_num}: could not parse a property proposal; skipped.")
            consecutive_no_progress += 1
            continue

        replacements = apply_edits_guarded(replacements)
        if not replacements:
            print("No valid edit remained after the file guard; skipping this round")
            history.append(f"Round {round_num}: proposed edit(s) rejected by the file guard; skipped.")
            consecutive_no_progress += 1
            continue

        # "targets_rule" is an informational extra key on the edit object
        # (apply_replacements_locally only reads "find"/"replace" and
        # ignores it) - which checklist item this round's edit is meant
        # to formalize, per harness_prompt/add_property_prompt's response
        # format. Despite the prompt asking for a SINGLE edit, the LLM
        # sometimes still splits one property across multiple {file,
        # find, replace} entries (e.g. a helper register in one edit, the
        # actual assert in another) - confirmed via a real round where
        # replacements[-1] alone picked an unrelated helper-register edit
        # instead of the entry containing the actual failing assert,
        # making the triage prompt (and the recorded finding) show the
        # wrong snippet even though the triage verdict itself, grounded
        # separately in the real counterexample trace, was still correct.
        # Joining every edit's replace text avoids that regardless of how
        # many entries the LLM actually sent.
        targets_rule = next((str(r.get("targets_rule", "")).strip() for r in reversed(replacements)
                             if r.get("targets_rule")), "")
        new_property_text = "\n\n".join(
            f"--- edit to {r.get('file', CHECKER_FILE)} ---\n{r.get('replace', '')}"
            for r in replacements
        )

        base_content = checker_content or ""
        try:
            candidate_content = apply_replacements_locally(working_dir, base_content, replacements)
        except ValueError as exc:
            print(f"WARNING: {exc}; skipping this round")
            history.append(f"Round {round_num}: {exc}; skipped.")
            consecutive_no_progress += 1
            continue

        # Fast sanity gate, with a small number of syntax-only retries.
        # A TIMEOUT is deliberately never retried via the syntax-fix path
        # below - see _run_streamed's docstring for why this bound exists
        # at all. Retrying can only help a genuine syntax/elaboration
        # error; a solver stuck on a hard query isn't a syntax problem,
        # and re-running the same (or a barely-tweaked) query would very
        # likely just time out
        # again, burning the full timeout MAX_SYNTAX_FIX_ATTEMPTS more
        # times for nothing.
        gate_result = None
        response = None
        for attempt in range(MAX_SYNTAX_FIX_ATTEMPTS + 1):
            file_contents = {DUT_FILE: verification_dut, CHECKER_FILE: candidate_content, SBY_FILE: static_files[SBY_FILE]}
            response = get(run_sby.chia_remote(SBY_FILE, file_contents, args.sby_timeout))
            gate_result = classify_sby_result(response)
            print(f"Sanity gate result: {gate_result}"
                  + (" (TIMED OUT)" if getattr(response, "timed_out", False) else ""))
            if gate_result != "error" or getattr(response, "timed_out", False):
                break
            if attempt == MAX_SYNTAX_FIX_ATTEMPTS:
                break
            print(f"Sanity gate hit a setup/syntax error; asking for a narrow fix "
                  f"(attempt {attempt + 1}/{MAX_SYNTAX_FIX_ATTEMPTS})")
            fix_resp, fix_usage = call_llm(llm, syntax_fix_prompt(candidate_content, response.stdout, response.stderr))
            print(f"LLM usage this call: {format_usage(fix_usage)}")
            fix_edits = extract_json_array(fix_resp.result)
            if not fix_edits:
                print("LLM proposed no syntax fix; discarding this round's candidate")
                break
            fix_edits = apply_edits_guarded(fix_edits)
            if not fix_edits:
                break
            try:
                candidate_content = apply_replacements_locally(working_dir, candidate_content, fix_edits)
            except ValueError as exc:
                print(f"WARNING: {exc}; discarding this round's candidate")
                break

        if gate_result == "error":
            if getattr(response, "timed_out", False):
                print(f"Sanity gate's solver got stuck (exceeded --sby-timeout={args.sby_timeout}s) - "
                      "not a syntax error, not retried; discarding this round's candidate")
                discarded.append(f"Round {round_num}: discarded - sanity gate solver timed out "
                                  f"(>{args.sby_timeout}s), likely a pathologically hard SMT query "
                                  "from this candidate.")
                history.append(f"Round {round_num}: discarded (sanity gate solver timeout).")
            else:
                print("Sanity gate still broken after retries; discarding this round's candidate")
                discarded.append(f"Round {round_num}: discarded - sby setup/syntax error persisted after retries.")
                history.append(f"Round {round_num}: discarded (persistent setup error).")
            consecutive_no_progress += 1
            continue

        if gate_result == "fail":
            print("Sanity gate reported a genuine FAIL - triaging (violation vs. bad property) "
                  "rather than discarding blind...")
            triage_resp, triage_usage = call_llm(
                llm, triage_fail_prompt(new_property_text, response.stdout, getattr(response, "trace", None)),
                pdf_bytes=spec_pdf_bytes)
            print(f"LLM usage this call: {format_usage(triage_usage)}")
            triage_text = triage_resp.result
            verdict_match = re.search(r"VERDICT:\s*(\w+)", triage_text)
            verdict = verdict_match.group(1).upper() if verdict_match else "UNCLEAR"
            reasoning_match = re.search(r"REASONING:\s*(.*?)(?:\n\s*VERDICT:|\Z)", triage_text, re.DOTALL)
            reasoning = reasoning_match.group(1).strip() if reasoning_match else triage_text.strip()
            print(f"Triage verdict: {verdict}")
            print(f"Triage reasoning: {reasoning[:500]}")

            if verdict == "VIOLATION":
                # A VIOLATION verdict is the single most consequential
                # output of this loop - unlike a kept property (which
                # needs BOTH a coverage-delta AND an explicit vacuity
                # signal to agree before it's trusted), a bare triage
                # call was previously trusted alone. Confirmed via a real
                # run that this can go wrong both ways: a property
                # checking the wrong signal (a read-data register that
                # updates for its own reasons, misattributed to "storage
                # changed") and a property that's simply stricter than
                # the spec requires (treating ordinary one-cycle
                # synchronous reset latency as a violation). A second,
                # deliberately skeptical call has to independently agree
                # before this gets recorded as a finding.
                print("Triage says VIOLATION - getting an independent second opinion before "
                      "recording it as a finding...")
                verify_resp, verify_usage = call_llm(
                    llm, verify_finding_prompt(new_property_text, reasoning, response.stdout,
                                                getattr(response, "trace", None)),
                    pdf_bytes=spec_pdf_bytes)
                print(f"LLM usage this call: {format_usage(verify_usage)}")
                verify_text = verify_resp.result
                verify_verdict_match = re.search(r"VERDICT:\s*(\w+)", verify_text)
                verify_verdict = verify_verdict_match.group(1).upper() if verify_verdict_match else "UNCLEAR"
                verify_reasoning_match = re.search(r"REASONING:\s*(.*?)(?:\n\s*VERDICT:|\Z)", verify_text, re.DOTALL)
                verify_reasoning = verify_reasoning_match.group(1).strip() if verify_reasoning_match else verify_text.strip()
                print(f"Second-opinion verdict: {verify_verdict}")
                print(f"Second-opinion reasoning: {verify_reasoning[:500]}")

                if verify_verdict == "CONFIRMED":
                    findings.append(
                        f"Round {round_num} (targets rule {targets_rule or 'unspecified'}): {reasoning[:600]}\n"
                        f"  Confirmed on second opinion: {verify_reasoning[:400]}\n"
                        f"  Property: {new_property_text[:400]!r}"
                    )
                    if targets_rule:
                        mark_checklist(checklist, targets_rule, "finding")
                    history.append(f"Round {round_num}: GENUINE SPEC VIOLATION FOUND (targets rule "
                                    f"{targets_rule or 'unspecified'}), confirmed on independent second "
                                    "opinion - reported as a finding, property not kept in the live "
                                    "checker (would permanently fail every future round's gate).")
                    # A finding is real information gained, not a stall -
                    # reset the plateau counter even though the checker
                    # itself didn't grow this round.
                    consecutive_no_progress = 0
                else:
                    discarded.append(f"Round {round_num}: discarded - sby FAIL initially triaged as a "
                                      f"VIOLATION but NOT CONFIRMED on independent second opinion: "
                                      f"{verify_reasoning[:300]}")
                    history.append(f"Round {round_num}: discarded (VIOLATION verdict not confirmed on "
                                    "second opinion).")
                    consecutive_no_progress += 1
            else:
                discarded.append(f"Round {round_num}: discarded - sby FAIL triaged as a bad property "
                                  f"(verdict={verdict}): {reasoning[:300]}")
                history.append(f"Round {round_num}: discarded (sanity gate FAIL, triaged as bad property).")
                consecutive_no_progress += 1
            continue

        # gate_result is "pass" or "unknown" (inconclusive induction, not a
        # real counterexample) - proceed to the full mcy evaluation.
        print(f"Sanity gate clean ({gate_result}); running mcy for a coverage-delta verdict...")
        file_contents = {
            DUT_FILE: verification_dut,
            CHECKER_FILE: candidate_content,
            MCY_CONFIG_FILE: static_files[MCY_CONFIG_FILE],
            MCY_TEST_SCRIPT: static_files[MCY_TEST_SCRIPT],
            MCY_TEST_SBY: static_files[MCY_TEST_SBY],
        }
        mcy_result = get(run_mcy.chia_remote(file_contents, args.mutation_size, args.mcy_jobs,
                                              args.mcy_init_timeout, args.mcy_run_timeout))
        new_coverage = mcy_result["coverage_pct"]
        uncovered_hints = mcy_result["uncovered_hints"]
        print(f"mcy coverage: {new_coverage}% (previous: {current_coverage}%)"
              + (" (TIMED OUT)" if mcy_result.get("timed_out") else ""))

        if mcy_result.get("timed_out"):
            print(f"WARNING: mcy timed out (init={args.mcy_init_timeout}s, run={args.mcy_run_timeout}s "
                  "budgets) - likely a pathologically hard mutation for this candidate; discarding")
            discarded.append(f"Round {round_num}: discarded - mcy timed out, likely a pathologically "
                              "hard mutation test for this candidate.")
            history.append(f"Round {round_num}: discarded (mcy timeout).")
            consecutive_no_progress += 1
            continue

        if new_coverage is None:
            print("WARNING: could not parse a coverage number from mcy's output; discarding this round's candidate")
            discarded.append(f"Round {round_num}: discarded - mcy did not report a parseable coverage number.")
            history.append(f"Round {round_num}: discarded (unparseable mcy output).")
            consecutive_no_progress += 1
            continue

        # Two ways a candidate earns a shot at being kept:
        #  (a) it improved mutation coverage - the original bar, still the
        #      right one for a property that revisits already-addressed
        #      ground (catches genuine redundancy: the coverage-delta
        #      idea's whole point).
        #  (b) it targets a checklist item still marked "unaddressed" -
        #      confirmed via a real 40-round run that this is NOT a rare
        #      case: once a couple of broad properties (e.g. write-read
        #      coherency) push coverage into the 90s%, mutation coverage
        #      saturates and EVERY later property gets discarded for zero
        #      delta regardless of which spec requirement it formalizes -
        #      that run ended with 97.5% coverage but only 4/19 checklist
        #      items addressed, because completeness-against-the-spec and
        #      mutation-coverage-of-this-one-RTL are genuinely different
        #      goals once coverage is already high. A property closing a
        #      real gap in the checklist is worth keeping on its own
        #      merits (it holds, it's non-vacuous) even if the specific
        #      mutations it would catch happen to already be caught by an
        #      unrelated broader property.
        # Non-vacuity (below) is still required either way - this only
        # changes whether a coverage bump is ALSO required.
        targets_unaddressed = bool(targets_rule) and checklist_item_status(checklist, targets_rule) == "unaddressed"
        coverage_improved = new_coverage > current_coverage

        if not coverage_improved and not targets_unaddressed:
            print(f"No coverage improvement ({current_coverage} -> {new_coverage}) and this candidate "
                  "doesn't target a still-unaddressed checklist item; discarding")
            checker_content = last_good_checker
            discarded.append(f"Round {round_num}: discarded - zero/negative mutation-coverage delta "
                              f"({current_coverage}% -> {new_coverage}%) and not targeting a fresh "
                              "checklist item.")
            history.append(f"Round {round_num}: discarded (no coverage delta, no checklist progress).")
            consecutive_no_progress += 1
            continue

        keep_reason = "coverage improved" if coverage_improved else "targets an unaddressed checklist item"
        print(f"Candidate eligible to keep ({keep_reason}); confirming every cover() is still "
              "reachable before committing...")
        cover_file_contents = {DUT_FILE: verification_dut, CHECKER_FILE: candidate_content,
                                COVER_SBY_FILE: static_files[COVER_SBY_FILE]}
        cover_response = get(run_sby.chia_remote(COVER_SBY_FILE, cover_file_contents, args.sby_timeout))
        cover_result = classify_sby_result(cover_response)
        print(f"Non-vacuity cover check: {cover_result}"
              + (" (TIMED OUT)" if getattr(cover_response, "timed_out", False) else ""))

        if cover_result != "pass":
            unreached = parse_unreached_covers(cover_response.stdout)
            print(f"Cover check did not cleanly pass ({cover_result}); unreached: {unreached or '(none parsed)'}; "
                  f"discarding this round's candidate despite being eligible ({keep_reason})")
            checker_content = last_good_checker
            discarded.append(f"Round {round_num}: discarded - eligible to keep ({keep_reason}, coverage "
                              f"{current_coverage}% -> {new_coverage}%) but the non-vacuity cover check "
                              f"did not pass ({cover_result}); unreached cover(s): {unreached or 'unparsed'}.")
            history.append(f"Round {round_num}: discarded (vacuous - cover check {cover_result}).")
            consecutive_no_progress += 1
            continue

        print(f"Keeping this property ({keep_reason}, coverage {current_coverage} -> {new_coverage}, non-vacuous)")
        old_coverage = current_coverage
        checker_content = candidate_content
        last_good_checker = candidate_content
        current_coverage = new_coverage
        reasoning_excerpt = resp.result.strip()[:300]
        kept.append(f"Round {round_num} (targets rule {targets_rule or 'unspecified'}, kept because "
                    f"{keep_reason}, coverage now {new_coverage:.2f}%): {reasoning_excerpt!r}")
        history.append(f"Round {round_num}: kept ({keep_reason}), coverage {old_coverage} -> {new_coverage}.")
        if targets_rule:
            mark_checklist(checklist, targets_rule, "addressed")
        consecutive_no_progress = 0

    # Persist the final accepted checker (or the harness-only state if
    # nothing was ever kept) to the real filesystem for human inspection.
    if checker_content is not None:
        write_files(working_dir, {CHECKER_FILE: checker_content})
        print(f"\nWrote final {CHECKER_FILE} ({len(kept)} kept propert(y/ies)) to {working_dir}")
    else:
        print("\nNo checker content was ever established; nothing written.")

    addressed_count = sum(1 for item in checklist if item["status"] != "unaddressed")
    print(f"\n{'=' * 80}\nDone: {round_num} round(s), {len(kept)} kept, {len(discarded)} discarded, "
          f"{len(findings)} genuine finding(s), checklist {addressed_count}/{len(checklist)} addressed, "
          f"final coverage: {current_coverage}%\nTotal LLM usage: {format_usage(RUN_USAGE_TOTALS)}\n{'=' * 80}")
    if checklist:
        print("\nFinal spec checklist status:")
        print(checklist_section(checklist))
    if findings:
        print("\nGENUINE SPEC VIOLATIONS FOUND:")
        for f in findings:
            print(f"  {f}")

    history_section = "\n".join(f"- {h}" for h in history) if history else "(no rounds ran)"
    kept_section = "\n".join(f"- {k}" for k in kept) if kept else "(none)"
    discarded_section = "\n".join(f"- {d}" for d in discarded) if discarded else "(none)"
    findings_section = "\n".join(f"- {f}" for f in findings) if findings else "(none)"
    checklist_final_section = checklist_section(checklist) if checklist else "(no checklist was extracted this run)"
    summary_prompt = (
        SUMMARY_PROMPT
        + "\n\nRound-by-round history:\n" + history_section
        + "\n\nKept properties:\n" + kept_section
        + "\n\nDiscarded properties:\n" + discarded_section
        + "\n\nGenuine spec-violation findings:\n" + findings_section
        + "\n\nFinal spec checklist status:\n" + checklist_final_section
        + f"\n\nSpec checklist completeness (authoritative, use this exact count): "
          f"{addressed_count}/{len(checklist)} addressed"
        + f"\n\nFinal mutation coverage: {current_coverage}%"
        + f"\n\nFinal {CHECKER_FILE} content:\n{checker_content or '(none established)'}"
    )
    summary_resp, summary_usage = call_llm(llm, summary_prompt)
    print(f"\n{'=' * 80}\nFinal summary (LLM-authored):\n{'=' * 80}")
    print(summary_resp.result.strip() or "(LLM produced no summary)")
    print(f"\nGrand total LLM usage: {format_usage(RUN_USAGE_TOTALS)}")


if __name__ == "__main__":
    main()
