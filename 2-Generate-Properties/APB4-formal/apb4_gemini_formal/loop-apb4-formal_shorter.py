from __future__ import annotations

import argparse
import fcntl
import os
import re
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.llm_call import QueryResult
from chia.models.vertex import RateLimitError, VertexGeminiLLM


# =============================================================================
# EDIT PROMPTS HERE: design brief, shared rules, and named repair stages.
#
# LOOP FLOW:
#   GENERATE_PROMPT -> prep (Yosys build check) -> cover
#   build error     -> BUILD_PROMPT -> prep again (no RTL repair)
#   cover failure   -> COVER_PROMPT -> cover again
#   cover pass      -> REVIEW_PROMPT -> 10-step reset BMC if accepted
#   review fix      -> prep -> cover again -> review again
#   BMC failure     -> PROPERTY_FIX: prep -> cover -> review -> BMC
#                   -> BUG_REPORT: record bugs -> add adjacent properties
#   BMC pass        -> add missing properties or finish discovery
#   incomplete SBY -> BUILD_PROMPT -> retry all validation gates (within budget)
# =============================================================================

DESIGN_PROMPT = """
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
Discovery focus: byte-strobe behavior and data integrity. Generate one small,
independent property per round, grounded in this brief. Choose the scenario
and checker yourself; justify assumptions and sampling timing, and cover its
trigger. Refine this property before expanding to another requirement within
this focus. Defer unrelated protocol/error discovery. Distinguish intended
requirements from suspicious RTL behavior.
Idle requires PSEL=PENABLE=0, not zero address/data/control outputs.

"""

COMMON = """
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
PRESETn. Keep history validity independent of reset: initialize past_valid to
zero and set it to one after the first clock, unconditionally. Never use
past_valid <= PRESETn as the history guard for reset assertions. Initialize
checker state and guard $past() with valid history; normal-operation checks
also require reset release. Check synchronous reset outputs on the clock
following asserted reset, outside normal-operation guards. Add a cover for
the exact reset-assertion trigger, including ALL enclosing guards, to verify
it can execute. Do not add assumptions to force the trigger or outputs true.
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
BMC PASS checks assertions only within depth 10, not an unbounded proof.
Keep both BMC and cover at depth 10 throughout generation and repairs.

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
depth 10
--
cover:
mode cover
depth 10
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
"""

GENERATE_PROMPT = DESIGN_PROMPT + """
STAGE 1: DISCOVER AND GENERATE PROPERTIES
1. Read the RTL below and briefly explain the data path, phase transitions,
   reset, and any mismatch with the intended behavior in the design brief.
2. Describe the one proposed property: requirement, environment assumption if needed,
   assertion, and cover that exercises its trigger. Check data after a known
   write; do not turn the RTL's expressions into tautological assertions.
3. Generate apb4.sby from scratch. Include covers for reset release and the
   selected property's trigger. Do not add a full-bus checklist or unrelated
   scenarios just to broaden coverage. Do not require wait-state coverage.

Allowed changes: generated SBY only. Next: build check, cover, property review, then
reset-constrained BMC at depth 10.

Current RTL:
{rtl}
""" + COMMON

BUILD_PROMPT = DESIGN_PROMPT + """
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
If exploration returned only a module, retain its justified new properties
and the previous candidate's valid checks. Return ALL SBY sections plus the
embedded module in ONE fenced apb4.sby; do not return just the missing pieces.
Do not change RTL, requirements, assertions, covers, reset, or depth merely
to silence an error. If the tool installation is broken, explain the blocker.
Return a complete corrected SBY. Preserve working code outside the diagnosed
error. Python checks the build before accepting the candidate and running cover.

Current apb4.sby:
{sby}

Build/format/timeout diagnostics:
{log}

Current RTL:
{rtl}
""" + COMMON

COVER_PROMPT = DESIGN_PROMPT + """
STAGE 2: DIAGNOSE AND REPAIR COVERAGE
The cover task did not pass. BMC has not been accepted for this candidate.
1. Read the log first. Distinguish a setup/parse/tool error, timeout, and a
   completed cover search with unreached goals. An ERROR is not an RTL bug;
   an unreached goal at depth 10 does not prove it is unreachable forever.
2. Identify the exact affected cover or setup line. Check reset release,
   signal connections, history guards, and contradictory assumptions.
   Use the numbered emitted apb4_formal.sv below, NOT line numbers in the SBY.
   Quote each UNREACHED line and ALL its enclosing guards before diagnosing.
   Distinguish it from REACHED goals; leave already reached goals unchanged
   unless a shared faulty guard must move. A sticky flag records any earlier
   event, not necessarily an event on the immediately preceding cycle.
   For a reset cover, evaluate the guards cycle by cycle from initial reset.
   Move a valid reset cover out of a contradictory guard; do not delete it.
3. Correct only the faulty harness/configuration/cover definition or an
   unjustified environment assumption. Explain each change. Preserve valid
   goals, assertions, and depth 10. If a goal needs more cycles or appears
   blocked by the RTL, report that limitation instead of forcing a pass.

Allowed changes: generated SBY only. Return the complete SBY, including the
embedded harness. Next: Python retries cover before permitting BMC.

Previous cover repairs and outcomes (avoid repeating an unsuccessful edit):
{cover_history}

Current apb4.sby:
{sby}

Cover log:
{log}

Current RTL:
{rtl}
""" + COMMON

REVIEW_PROMPT = DESIGN_PROMPT + COMMON + """
PROPERTY REVIEW AFTER COVER (these response rules override the shared format)
Cover passed. Audit the CURRENT assertions against the design brief before
BMC. Passing cover is only evidence that the selected scenarios are reachable.
For each assertion, briefly identify its intended requirement and check:
- Reset guards and $past history: a request during reset is not accepted.
  Verify history validity advances independently of reset. Evaluate the full
  synchronous-reset assertion trigger on the first post-reset clock, including
  all enclosing guards, and require a matching cover. Reject contradictory
  guards rather than approving an assertion that never executes.
- Clock timing: show a short edge-by-edge table for read A followed immediately
  by read B with different data. Include phase/address, sampled PRDATA, read
  trigger/delayed flag, expected data, and the assertion edge. Reject a checker
  that compares B's response with A's expected word. A registered read flag
  followed by $past(flag) adds a second delay; do not approve it by inspection
  of comments alone. Account for this slave's SETUP read update.
- Scope: verify an actual assumption on SADDR limits traffic to 0..1023,
  full-address matches gate the scoreboard/read checks, and covers stay in
  scope. A range assumption on watched_addr alone is insufficient.
- Requirements: identify the design-brief requirement for each assertion.
  Reject invented mandatory errors for zero-strobe writes/out-of-range
  addresses or a ban on back-to-back transfers after an error. Preserve the
  valid zero-strobe memory-preservation requirement instead.
- Data readback is checked only for legal reads with PSTRB==0; an invalid
  read may leave PRDATA unchanged while reporting PSLVERR.
- The expected-data model implements the intended byte-lane behavior. Reject
  any model that copies the DUT's case(PSTRB) expressions or other suspected
  RTL behavior, because comparing the implementation with itself hides bugs.
- Assumptions constrain legal requester behavior, not DUT outputs, and do
  not exclude the cases the assertion must detect.
- The check is not a tautology; its cover exercises the actual antecedent.
- A legal trace should satisfy it and a violation should fail it. Explain
  a concrete example for any suspicious check. Do not invent requirements.
A valid assertion may expose an RTL bug: retain it for BMC. Do not rewrite
the RTL during this review or weaken a requirement to match the current RTL.

The first line must contain only REVIEW_OK, REVIEW_FIX, or REVIEW_BLOCKED,
with no Markdown or other text on that line.
REVIEW_OK means all the checks above are justified; give the audit and timing
table only. Missing timing/scope evidence requires REVIEW_FIX or REVIEW_BLOCKED.
REVIEW_FIX means repairs are needed; explain and return the complete corrected
apb4.sby in the shared format. No RTL files.
REVIEW_BLOCKED means a requirement is ambiguous; explain what is missing.
For REVIEW_OK, return no files. For REVIEW_FIX, Python reruns the build check,
cover, and this review before BMC. This is an LLM assessment, not a proof.

Current apb4.sby:
{sby}

Current RTL:
{rtl}
"""

PROOF_PROMPT = DESIGN_PROMPT + """
STAGE 3: DIAGNOSE BMC, REPAIR PROPERTIES, OR REPORT RTL BUGS
(these response rules override the shared output format)
Cover passed, but BMC did not. Coverage does not establish property correctness.
The prior property review is fallible; recheck the failing property against
the actual counterexample before deciding to modify the RTL.
1. Classify the log as setup ERROR, timeout/UNKNOWN, or assertion FAIL. The
   BMC engine uses --keep-going, so diagnose EVERY reported failed assertion,
   grouping failures with the same root cause. Quote each assertion and step.
   Evaluate its timing, reset,
   assumptions, and DUT connections against the current RTL. Use only supplied
   evidence; if waveform values are absent, do not invent a counterexample.
   Diagnose the assertion named in the tool log first. Comments saying a
   different check is 'expected to fail' are not failure evidence. For early
   failures, check whether the antecedent incorrectly includes a reset cycle.
   Recheck the read-A/read-B timing table and full-address scope from review.
   A delayed checker comparing another transaction's PRDATA is PROPERTY_FIX,
   not an RTL bug. Unspecified error policies are not bug evidence. When a
   data-corruption claim depends on unavailable waveform values, use
   PROOF_BLOCKED unless the supplied RTL and property establish the violation
   independently; clearly distinguish static reasoning from observed values.
2. Decide which outcome is justified:
   - Setup/property error: correct the SBY/harness and explain why the old
     check misrepresented the requirement. Preserve the intended requirement.
   - RTL bug: preserve the valid assertion and report the broken requirement,
     failing assertion, counterexample step, faulty RTL location, and smallest
     suggested repair in prose. Do not return or modify any RTL file.
   - Inconclusive: explain the missing evidence; do not suppress the failure.
3. Never weaken assumptions or assertions to make buggy RTL pass.

The first line must contain exactly one of:
PROPERTY_FIX  when only the harness/property is changed; return no RTL files.
BUG_REPORT    when valid properties expose one or more RTL bugs. Report every
              evidenced bug found by this BMC run and return no files.
PROOF_BLOCKED when the evidence cannot justify either repair; explain why.
For PROPERTY_FIX, return the complete corrected SBY. For BUG_REPORT and
PROOF_BLOCKED, return analysis only. Python reruns validation only after a
PROPERTY_FIX. The RTL remains exactly as supplied.

Already reported failures (do not report these again):
{known_bugs}

Current apb4.sby:
{sby}

BMC log:
{log}

Current RTL:
{rtl}
""" + COMMON

EXPLORE_PROMPT = DESIGN_PROMPT + """
PROPERTY DISCOVERY AFTER A BMC ROUND
(these response rules override the shared output format)
Use the current properties and accumulated bug reports to look for meaningful
byte-strobe/data-integrity requirements that are still missing. A discovered
bug is a lead: consider an adjacent, independent property within that focus.
Choose the scenario yourself; do not expand to unrelated protocol checks.
Do not duplicate an existing assertion or specialize it to the same trace.
Do not modify RTL, remove known failing assertions, weaken assumptions, or
encode current RTL behavior as the expected result. Add only one independent
property per round, with a reachable cover for its antecedent. Preserve
all valid existing properties and the complete SBY structure.
Stay within the same address scope and stated requirements. Do not invent
error policies or out-of-range tests to create new failing assertions.

The first line must contain exactly one of:
EXPLORE_MORE  when useful independent properties are missing; explain them and
              return one complete updated apb4.sby.
EXPLORE_DONE  when no useful independent property remains within the current
              focus; explain the limits of this search and return no files.
For EXPLORE_MORE, after the explanation write === apb4.sby === and ONE fenced
text block containing [tasks], [options], [engines], [script], [files], and
[file apb4_formal.sv] with the full module. Copy the working configuration
from the current SBY. A standalone Verilog block or embedded module alone is
NOT a complete response. Python will ask you to repair it, not assemble it.

Accumulated bug reports:
{bug_reports}

Current apb4.sby:
{sby}

Current RTL (read-only):
{rtl}
""" + COMMON


HERE = Path(__file__).resolve().parent
WORK = HERE / "loop_work_shorter"
RESULT = Path(os.environ.get("APB4_RESULT_DIR", str(HERE / "live_result_shorter")))
RTL_NAMES = ["APB_Master.v", "APB_Slave.v", "APB_Wrapper.v"]
MODEL = "gemini-2.5-pro"
PROJECT = "project-be5ca9cc-e88a-41a1-81f"


def fill(template: str, **values: str) -> str:
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template.strip()


def clip(text: str, size: int = 12000) -> str:
    return text if len(text) <= size else text[-size:]


def feedback(log: str, source: str = "") -> str:
    important = "\n".join(dict.fromkeys(line for line in log.splitlines()
                         if re.search(r"error|fail|unreached|unknown|timeout|DONE|undriven|no driver|implicitly declared", line, re.I)))
    reached = "\n".join(dict.fromkeys(line for line in log.splitlines() if "Reached cover" in line))
    numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(source.splitlines(), 1))
    return ("Key diagnostics:\n" + important[:6000]
            + "\nAlready reached goals:\n" + reached[:3000]
            + "\nNumbered emitted apb4_formal.sv (solver line numbers):\n" + numbered
            + "\nLog tail:\n" + clip(log, 6000))


def failed_assertions(log: str) -> set[str]:
    # Preserve line/column ranges for unnamed checks, including on timeout.
    names = re.findall(r"Assert failed in [^\n:]+:[ \t]*(\S+)", log)
    names += re.findall(r"failed assertion[ \t]+\S+[ \t]+at[ \t]+(\S+)", log)
    return set(names)


def sources() -> dict[str, str]:
    candidates = [HERE.parent / "AMPA_APB4_Protocol", ROOT / "chia-hello-world" / "AMPA_APB4_Protocol"]
    src = next((p for p in candidates if (p / "RTL" / RTL_NAMES[0]).exists()), None)
    if src is None:
        raise FileNotFoundError("AMPA_APB4_Protocol RTL not found")
    return {name: (src / "RTL" / name).read_text() for name in RTL_NAMES}


def parse_sby(answer: str) -> str | None:
    named = parse_file(answer, "apb4.sby")
    if named is not None:
        return named
    blocks = re.findall(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$", answer, re.S | re.M)
    candidates = [b for b in blocks if b.lstrip().startswith("[tasks]")
                  and "[script]" in b and "[file apb4_formal.sv]" in b]
    return candidates[0].strip() + "\n" if len(candidates) == 1 else None


def parse_file(answer: str, name: str) -> str | None:
    name = re.escape(name)
    blocks = re.findall(
        rf"^[ \t]*(?:===[ \t]*{name}[ \t]*===|#{{1,6}}[ \t]+[`*]*{name}[`*]*)[ \t]*\n"
        r"[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$", answer, re.S | re.M | re.I)
    return blocks[0].strip() + "\n" if len(blocks) == 1 else None


@ChiaFunction(resources={"sby": 1})
def stage(files: dict[str, str]) -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    for name, text in files.items():
        (WORK / name).write_text(text)


@ChiaFunction(resources={"sby": 1})
def write_sby(text: str) -> None:
    (WORK / "apb4.sby").write_text(text)


@ChiaFunction(resources={"sby": 1})
def read_sby() -> str:
    path = WORK / "apb4.sby"
    return path.read_text() if path.exists() else "(missing)"


@ChiaFunction(resources={"sby": 1})
def read_rtl() -> dict[str, str]:
    return {name: (WORK / name).read_text() for name in RTL_NAMES}


@ChiaFunction(resources={"sby": 1})
def run_sby(task: str, timeout: int) -> dict:
    if task not in {"prep", "cover", "bmc"}:
        raise ValueError(f"Unexpected SBY task: {task}")
    tool = "/opt/oss-cad-suite/bin/sby" if Path("/opt/oss-cad-suite/bin/sby").exists() else "sby"
    out = WORK / f"apb4_{task}"
    shutil.rmtree(out, ignore_errors=True)  # Never feed stale diagnostics back.
    status, rc = "ERROR", -1
    try:
        with subprocess.Popen([tool, "-f", "apb4.sby", task], cwd=WORK,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, start_new_session=True) as proc:
            try:
                log, _ = proc.communicate(timeout=timeout)
                rc = proc.returncode
                match = re.search(r"DONE \((PASS|FAIL|UNKNOWN|ERROR)", log)
                status = match.group(1) if match else "ERROR"
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                log, _ = proc.communicate()
                log += "\n[TIMEOUT: stopped SBY and its solver process group]\n"
                if task == "bmc" and re.search(r"Assert failed|failed assertion", log, re.I):
                    status = "FAIL"
                    log += "[BMC assertion failure retained despite later timeout]\n"
                else:
                    status = "TIMEOUT"
    except OSError as exc:
        log = f"ERROR: {exc}\n"
    for path in sorted((out / "model").glob("*.log")):
        log += f"\n=== {path.relative_to(out)} ===\n" + path.read_text(errors="replace")
    source = out / "src" / "apb4_formal.sv"
    return {"pass": rc == 0 and status == "PASS", "status": status,
            "returncode": rc, "log": log,
            "source": source.read_text(errors="replace") if source.is_file() else ""}


def ask(llm: VertexGeminiLLM, prompt: str, tag: str, attempt: int) -> str:
    RESULT.mkdir(parents=True, exist_ok=True)
    (RESULT / f"{tag}_{attempt:02d}_prompt.md").write_text(prompt)
    for retry in range(3):
        try:
            response: QueryResult = get(llm.prompt.chia_remote(llm, prompt))
            if not response.success:
                raise RuntimeError(response.stderr or "Gemini returned an unsuccessful response")
            answer = response.result or ""
            break
        except Exception as exc:
            (RESULT / f"{tag}_{attempt:02d}_error.txt").write_text(repr(exc))
            if isinstance(exc, RateLimitError) and retry < 2:
                delay = 15 * (retry + 1)
                print(f"Gemini rate limited; retrying the same prompt in {delay}s", flush=True)
                time.sleep(delay)
                continue
            with (RESULT / "history.txt").open("a") as history:
                history.write(f"LLM ERROR during {tag}: {exc}\nNOT VALIDATED: Gemini call failed\n")
            raise RuntimeError(f"Gemini {tag} request failed; see saved error log") from exc
    (RESULT / f"{tag}_{attempt:02d}_answer.txt").write_text(answer)
    print(f"Gemini {tag} response:\n{answer}", flush=True)
    return answer


def install_answer(answer: str) -> bool:
    sby = parse_sby(answer)
    required = {"tasks", "options", "engines", "script", "files", "file apb4_formal.sv"}
    if sby is None or not required.issubset(re.findall(r"^\s*\[([^\]\n]+)\]\s*$", sby, re.M)):
        return False
    candidate = RESULT / "candidate"
    candidate.mkdir(parents=True, exist_ok=True)
    get(write_sby.chia_remote(sby))
    (candidate / "apb4.sby").write_text(sby)
    return True


def save_history(history: list[str]) -> None:
    RESULT.mkdir(parents=True, exist_ok=True)
    (RESULT / "history.txt").write_text("\n".join(history) + "\n")


def save_bug_reports(reports: list[str]) -> None:
    if reports:
        text = "\n\n".join(f"===== BUG REPORT {i} =====\n{report.strip()}"
                             for i, report in enumerate(reports, 1))
        (RESULT / "bug-report.txt").write_text(text + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=6,
                        help="maximum property discovery and repair rounds")
    parser.add_argument("--sby-timeout", type=int, default=600)
    parser.add_argument("--llm-timeout", type=int, default=600)
    parser.add_argument("--resume-sby", type=Path,
                        help="start from an existing SBY; rerun all validation gates")
    args = parser.parse_args()
    if args.iterations < 0 or min(args.sby_timeout, args.llm_timeout) <= 0:
        parser.error("iterations must be nonnegative and timeouts positive")

    RESULT.mkdir(parents=True, exist_ok=True)
    run_lock = (RESULT / ".run.lock").open("w")
    try:
        fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        run_lock.close()
        raise SystemExit("another shorter APB formal loop is already running")

    resume = args.resume_sby.read_text() if args.resume_sby else None
    rtl = sources()
    rtl_text = "\n\n".join(f"=== {name} ===\n{text}" for name, text in rtl.items())
    get(stage.chia_remote(rtl))
    RESULT.mkdir(parents=True, exist_ok=True)
    (RESULT / "candidate").mkdir(exist_ok=True)
    for name, text in rtl.items():
        (RESULT / name).write_text(text)
        (RESULT / "candidate" / name).write_text(text)
    for name in ("apb4.sby", "prep-final.log", "cover-final.log", "bmc-final.log"):
        (RESULT / name).unlink(missing_ok=True)
    save_history(["started: no candidate has passed yet"])
    llm = VertexGeminiLLM(model=MODEL, project=os.getenv("VERTEX_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", PROJECT)),
                          location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"),
                          timeout_seconds=args.llm_timeout, max_tokens=65536)

    answer = ("=== apb4.sby ===\n```text\n" + resume + "\n```" if resume is not None
              else ask(llm, fill(GENERATE_PROMPT, rtl=rtl_text), "generate", 1))
    installed = install_answer(answer)

    history = []
    cover_history = []
    reports: list[str] = []
    seen_failures: set[str] = set()
    reported_sby = None
    success = False
    complete = False
    for attempt in range(1, args.iterations + 2):
        current_rtl = get(read_rtl.chia_remote())
        rtl_text = "\n\n".join(f"=== {name} ===\n{text}" for name, text in current_rtl.items())
        task = "prep"
        result = {"pass": False, "status": "FORMAT", "returncode": -1,
                  "log": "Missing complete apb4.sby or required stage verdict. Last reply:\n" + answer}
        if installed:
            for task in ("prep", "cover", "bmc"):
                print(f"Iteration {attempt}: running {task} (limit {args.sby_timeout}s)", flush=True)
                result = get(run_sby.chia_remote(task, args.sby_timeout))
                (RESULT / f"{task}-final.log").write_text(result["log"])
                history.append(f"iteration {attempt}: {task} {result['status']} rc={result['returncode']}")
                save_history(history)
                print(history[-1], flush=True)
                if not result["pass"]:
                    break
                if task == "prep":
                    # Publish the complete last-compiling snapshot; failed candidates stay separate.
                    for name, text in {"apb4.sby": get(read_sby.chia_remote()), **current_rtl}.items():
                        (RESULT / name).write_text(text)
                if task == "cover":
                    prompt = fill(REVIEW_PROMPT, sby=get(read_sby.chia_remote()), rtl=rtl_text)
                    answer = ask(llm, prompt, "review", attempt)
                    decision = answer.strip().splitlines()[0] if answer.strip() else ""
                    history.append(f"iteration {attempt}: property review {decision}")
                    save_history(history)
                    if decision != "REVIEW_OK":
                        status = "REVIEW_FIX" if decision == "REVIEW_FIX" else "REVIEW_BLOCKED"
                        result = {"pass": False, "status": status, "log": answer}
                        break
        else:
            history.append(f"iteration {attempt}: FORMAT error")
        if result["status"] == "REVIEW_BLOCKED":
            history.append("stopped: property review blocked or returned no recognized verdict")
            break
        if result["status"] == "REVIEW_FIX" and attempt <= args.iterations:
            installed = install_answer(answer)
            continue
        if attempt > args.iterations and not (
                task == "bmc" and (result["pass"] or result["status"] == "FAIL")):
            history.append(f"stopped: {task} {result['status']} after final validation")
            break
        if task == "bmc" and result["pass"]:
            success = True
            if attempt >= args.iterations:
                complete = True
                break
            explore = ask(llm, fill(EXPLORE_PROMPT,
                                    sby=get(read_sby.chia_remote()), rtl=rtl_text,
                                    bug_reports=clip("\n\n".join(reports), 8000)),
                          "explore", attempt)
            decision = explore.strip().splitlines()[0] if explore.strip() else ""
            history.append(f"iteration {attempt}: property discovery {decision}")
            save_history(history)
            if decision == "EXPLORE_DONE":
                complete = True
                break
            answer = explore
            installed = decision == "EXPLORE_MORE" and install_answer(answer)
            if not installed:
                history.append(f"iteration {attempt}: exploration FORMAT; queued for Gemini repair")
                save_history(history)
            continue
        tag, template = ("cover", COVER_PROMPT) if task == "cover" else ("proof", PROOF_PROMPT)
        if task == "prep" or result["status"] in {"ERROR", "TIMEOUT", "FORMAT"}:
            tag, template = "build", BUILD_PROMPT
        log = (result["log"] if result["status"] == "FORMAT"
               else feedback(result["log"], result.get("source", "")))
        if task == "bmc" and result["status"] == "FAIL":
            current_sby = get(read_sby.chia_remote())
            current_failures = failed_assertions(result["log"])
            if current_sby != reported_sby:
                seen_failures.clear()  # Same label/location can now mean a different check.
            new_failures = current_failures - seen_failures
            if current_failures and not new_failures:
                history.append(f"iteration {attempt}: no newly failing assertions")
                decision = "BUG_REPORT"
                answer = ""
            else:
                prompt = fill(template, sby=get(read_sby.chia_remote()), log=log, rtl=rtl_text,
                              known_bugs=clip("\n\n".join(reports), 8000))
                answer = ask(llm, prompt, tag, attempt)
                decision = answer.strip().splitlines()[0] if answer.strip() else ""
            history.append(f"iteration {attempt}: proof repair {decision}")
            save_history(history)
            if decision == "BUG_REPORT":
                if new_failures or not current_failures:
                    failed = ", ".join(sorted(current_failures)) or "(name not parsed from log)"
                    reports.append(f"Solver failed assertions: {failed}\n\n{answer}")
                    seen_failures.update(current_failures)
                    reported_sby = current_sby
                    save_bug_reports(reports)
                if attempt >= args.iterations:
                    complete = True
                    break
                explore = ask(llm, fill(EXPLORE_PROMPT,
                                        sby=get(read_sby.chia_remote()), rtl=rtl_text,
                                        bug_reports=clip("\n\n".join(reports), 8000)),
                              "explore", attempt)
                explore_decision = explore.strip().splitlines()[0] if explore.strip() else ""
                history.append(f"iteration {attempt}: property discovery {explore_decision}")
                save_history(history)
                if explore_decision == "EXPLORE_DONE":
                    complete = True
                    break
                answer = explore
                installed = explore_decision == "EXPLORE_MORE" and install_answer(answer)
                if not installed:
                    history.append(f"iteration {attempt}: exploration FORMAT; queued for Gemini repair")
                    save_history(history)
                continue
            if decision == "PROOF_BLOCKED":
                history.append("stopped: Gemini could not justify a property correction or bug report")
                break
            if decision != "PROPERTY_FIX":
                history.append("stopped: proof response had no recognized repair verdict")
                break
            installed = install_answer(answer)
        else:
            prompt = fill(template, sby=get(read_sby.chia_remote()), log=log, rtl=rtl_text,
                          known_bugs=clip("\n\n".join(reports), 8000),
                          cover_history="\n\n".join(cover_history[-3:]) or "None yet.")
            answer = ask(llm, prompt, tag, attempt)
            if tag == "cover":
                diagnostics = "\n".join(line for line in result["log"].splitlines()
                                        if "Unreached cover" in line)
                explanation = re.split(r"=== apb4.sby ===|```|^#{1,6} ", answer, maxsplit=1)[0]
                cover_history.append(f"Iteration {attempt}: {diagnostics}\n"
                                     f"Proposed repair (not yet validated): {explanation[:3000]}")
            installed = install_answer(answer)

    if complete and task == "bmc" and result.get("returncode") == -1:
        complete = False
        history.append("INCOMPLETE: BMC timed out; reported failures retained, depth 10 not completed")
    save_bug_reports(reports)
    if reports and complete:
        history.append(f"BUG SEARCH FINISHED: {len(reports)} report(s), RTL unchanged; not exhaustive")
    elif reports:
        history.append(f"BUG SEARCH INCOMPLETE: {len(reports)} report(s), RTL unchanged")
    elif success and complete:
        history.append("PASS: no assertion failures within BMC depth 10")
    else:
        history.append("NOT VALIDATED: property discovery did not complete")
    save_history(history)
    print("\n".join(history), flush=True)
    print(f"Results: {RESULT}", flush=True)
    run_lock.close()
    if not complete:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
