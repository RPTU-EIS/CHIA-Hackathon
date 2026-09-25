from chia.base.ChiaFunction import ChiaFunction, get
from pathlib import Path
from typing import Optional
import subprocess
import json
import re
import os
import threading
import time
import argparse
from chia.models.antigravity import *
from chia.models.opencode import OpenCodeLLM
from chia.models.vertex import VertexGeminiLLM, MaxOutputTokensError, RateLimitError

MAX_FIX_ATTEMPTS = 15 # Give up after this many LLM attempts fixing the sby setup
MAX_COUNTEREXAMPLE_FIX_ATTEMPTS = 15 # Give up after this many LLM attempts diagnosing/fixing a genuine counterexample (translation-only - see DESIGN_BUG_PRIOR)

# Falls back to this literal project if VERTEX_PROJECT isn't set - the GCP
# project this was originally developed against. Anyone else running this
# (e.g. a hackathon judge with their own GCP project/quota) should set
# VERTEX_PROJECT rather than edit this file; --vertex-project overrides
# both for a single invocation.
DEFAULT_VERTEX_PROJECT = "project-be5ca9cc-e88a-41a1-81f"

# The sby job file the LLM is asked to create; doesn't exist yet at the start.
SBY_FILE = "gcd.sby"

# The one task name run_sby always requests explicitly (see its "cmd" list).
# This is what actually decides which task's result classify_sby_result
# sees - NOT a "taskname: default" tag inside SBY_FILE. If gcd.sby's
# [tasks] section ever ends up with no (or the wrong) task marked default,
# `sby -f gcd.sby` with no task argument silently runs every task in the
# file back to back, and a later, unrelated task can produce a "DONE
# (PASS...)" that masks a real ERROR/FAIL from the one that actually
# matters - confirmed directly: an empty `cover` task with zero cover()
# statements still reports "DONE (PASS, rc=0)". Always passing this name on
# the command line removes that whole failure class: PRIMARY_TASK is
# exactly the property-checking task the loop cares about, regardless of
# how any other task in the file is (mis)configured.
PRIMARY_TASK = "prove"

# The single Verilog module sby actually elaborates: the LLM's from-scratch,
# SBY-compatible reimplementation of every property in REFERENCE_SVA_FILE.
VERIFICATION_TOP = "fv_gcd_sby.sv"

# Design + verification-top sources the LLM may instrument/rewrite - i.e. the
# destination files of the translation, not the original OneSpin ones (see
# REFERENCE_FILES below, which are read-only context and never edited).
SOURCE_FILES = ["gcd_binary.sv", VERIFICATION_TOP]

# The original (OneSpin/commercial-tool) flow script and property set being
# ported - shown for context only, never edited by the LLM.
REFERENCE_SVA_FILE = "fv_gcd_binary.sva"
REFERENCE_FILES = ["run_onespin.tcl", REFERENCE_SVA_FILE]

# The one part of the prompts below that's actually specific to this design:
# which properties REFERENCE_SVA_FILE defines. To retarget everything below
# at a different design/experiment, only this constant (plus the file-path
# constants above) needs to change - the rule text itself stays the same,
# including its fidelity guidance, which is written generically enough to
# apply to any design's worst-case-latency/golden-reference-function-style
# properties without naming this one's specifically.
DESIGN_PROPERTIES = "reset_p, idle_p, gcd_p, and wcl_p"

# Where a genuine defect is more likely to live, for this experiment
# specifically. Property Set Conversion experiments like this one port an
# already-verified design's property set - the properties above already
# held on this exact RTL under the original OneSpin flow, so this project
# isn't hunting for a pre-existing design bug. A counterexample here is far
# more likely to be a bug in the new translation (the checker's
# reimplementation of a property, or the .sby configuration) than a fresh
# RTL defect. Contrast with a "fix RTL against a golden property set" style
# experiment, where the property set is trusted and the RTL is what's
# expected to be buggy - there this prior would point the other way.
DESIGN_BUG_PRIOR = (
    "This design's RTL is assumed correct and is out of scope for this loop: these properties already "
    "held on it under the original OneSpin flow, and this project is porting that already-verified "
    "property set rather than hunting for a pre-existing design bug. Every counterexample you see is "
    "therefore a translation bug - most likely in the checker's reimplementation of a property, "
    "occasionally in the .sby configuration - never a genuine RTL defect. Do not propose changes to the "
    "design files under any circumstances; if you cannot find a checker-side explanation, say so and "
    "propose no fix rather than guessing at an RTL change."
)

# The only files apply_replacements is allowed to create/edit for this
# experiment - enforced there, not just by prompt wording (prompt
# instructions alone haven't proven reliable in this loop; see
# apply_replacements' docstring for the analogous absolute-path case).
# Property Set Conversion experiments like this one never touch the
# design, so DESIGN_BUG_PRIOR's reasoning is backed by a hard guarantee
# that gcd_binary.sv is read-only from the loop's perspective. A "fix RTL
# against a golden property set" style experiment would set this to
# include the design file(s) instead.
EDITABLE_FILES = [VERIFICATION_TOP, SBY_FILE]


# =============================================================================
# GENERAL, REUSABLE PROMPT CONTENT
#
# Everything from here down to "END GENERAL, REUSABLE PROMPT CONTENT"
# describes the OneSpin -> SymbiYosys translation methodology in general
# terms - it's built entirely from the constants above (file names,
# DESIGN_PROPERTIES, DESIGN_BUG_PRIOR, EDITABLE_FILES) and doesn't hardcode
# anything about this specific design. Candidate to lift into a shared
# module once the various chia-translate-* experiments' drivers get
# unified onto one.
# =============================================================================

TRANSLATE_PROMPT = (
    "We are porting a formal verification setup for a hardware design from a commercial tool (OneSpin) "
    "to open-source SymbiYosys (sby). The original run_onespin.tcl flow script and "
    f"{REFERENCE_SVA_FILE} property set (both included below) are provided for reference only: sby cannot "
    "execute Tcl scripts directly, and Yosys's native SVA subset does not support the concurrent operators "
    f"{REFERENCE_SVA_FILE} relies on (`##`, `|->`, `|=>`, `sequence`, `property`, `bind`) either - every "
    "property must be reimplemented from scratch following the rules below.\n\n"
    "Apply these strict hardware translation rules directly:\n\n"
    "1. **No Concurrent SVA**: Yosys native SVA does NOT support concurrent operators (`##`, `|->`, `|=>`, `sequence`, `property`). "
    "Rewrite every multi-cycle/temporal property using explicit auxiliary logic (e.g., delay shift registers, "
    "state flags) and immediate assertions (`assert(expr)`, `assume(expr)`, `cover(expr)`) inside standard `always @(posedge clk)` blocks.\n"
    "2. **No `bind` Constructs**: Do not use SystemVerilog `bind` statements. Instantiate verification logic directly "
    f"as inline assertions into the top-level {VERIFICATION_TOP} module.\n"
    "3. **Explicit Initial Values**: Every auxiliary register introduced for verification must have an explicit initial value "
    "(e.g., `initial reg_name = 1'b0;`). Without this, solver engines (BMC/k-induction) can assign uninitialized states "
    "and trigger false-positive counterexamples.\n"
    "4. **Reachable State Constraints**: Force a valid reset sequence at the start of every trace using an explicit `assume` on reset:\n"
    "   `reg init = 1; always @(posedge clk) begin if (init) assume(rst); init <= 0; end`\n"
    "5. **$past() Initialization Guard**: Native Yosys supports $past(), $stable(), $rose(), and $fell() "
    "inside procedural blocks, but $past() is undefined on cycle 0/1. Guard any $past() or temporal check "
    "with an initialization flag (e.g., `if (!init) assert(...)`) so solvers do not report false cycle-1 violations.\n"
    "6. **Depth & Engine Config**: Set `mode` (bmc/prove/cover) and `depth` in `[options]`. Ensure `depth` "
    "exceeds the max clock cycles of any temporal property being verified. Include a standard engine like `smtbmc z3` in `[engines]`.\n"
    "7. **Faithful, Property-by-Property Translation**: "
    f"{REFERENCE_SVA_FILE} defines the following properties: {DESIGN_PROPERTIES}. Translate each one "
    "individually and precisely; do not substitute a looser, easier-to-write approximation for what "
    "the original actually checks. Two concrete failure patterns to avoid, both seen in earlier "
    "translation attempts:\n"
    "   - For a property verifying worst-case latency (e.g. bounding how many cycles an operation may "
    "take before completing), a flat, hardcoded-cycle-count timeout is NOT an acceptable substitute for "
    "the real, parametrized bound the original property expresses - encode that bound symbolically, in "
    "terms of the design's own parameters, so it stays correct if those parameters change, and make sure "
    "`depth` in `[options]` (rule 6) is set large enough to exceed it for every task that checks this "
    "property.\n"
    "   - For a property verifying functional correctness against a golden reference function (an "
    "inlined model of what the design is supposed to compute), a weaker check that the correct result "
    "merely happens to satisfy - without actually being equivalent to it - is NOT an acceptable "
    "substitute, since an incorrect result could still pass such a check. Reimplement the golden "
    "reference function itself in checkable Verilog rather than replacing it with a weaker property.\n"
    "8. **Valid Multi-Task `.sby` Syntax**: When `[tasks]` lists more than one task, per-task overrides of "
    "`[options]`/`[script]`/`[engines]` content are written as inline `taskname:` (or negated `~taskname:`) line "
    "prefixes WITHIN those shared sections - never as nested/indented lines under `[tasks]` itself, and never as "
    "separate bracketed sections like `[options cover]` (not valid sby syntax). Each task's resolved section = "
    "every untagged line (in file order) plus that task's own tagged lines layered on top; a later line wins "
    "over an earlier one for the same key, so put shared defaults as untagged lines and only the actual "
    "per-task differences behind a tag. `[tasks]` MUST be the first section in the file, before `[options]`, "
    "`[script]`, and `[engines]` - sby parses sections top-to-bottom and does not recognize a name as a valid "
    "tag until it has already seen it declared in `[tasks]`; a tagged line like `cover: mode cover` inside "
    "`[options]` placed BEFORE `[tasks]` fails with a real, run-time \"sby file syntax error\" (confirmed: this "
    "does not show up in a `--dumpcfg` dry run, only in an actual invocation) even though the exact same line "
    "would be valid once `[tasks]` comes first. This matters because sby's `[tasks]` parser treats ANY line under "
    "`[tasks]` that isn't either a bare/tagged task-name line or a comment starting with `#` as its literal "
    "first character (no leading whitespace) as a candidate task/tag name - this exact mistake already happened "
    "once: indented explanatory comments and nested `mode cover`/`script` override lines written directly under "
    "`cover:`/`bmc:` were misread as bogus pseudo-tasks (including a literal `#` task, which became sby's "
    "DEFAULT task because the word \"default\" happened to appear unpunctuated in one of those comments), "
    "silently producing a stray work directory named after that misparsed token instead of running the "
    "intended task.\n"
    f"A task literally named \"{PRIMARY_TASK}\" MUST exist, and must be the task that checks every property "
    f"from {REFERENCE_SVA_FILE} - the harness that runs this always requests it by name "
    f"(`sby -f {SBY_FILE} {PRIMARY_TASK}`), so {PRIMARY_TASK}'s correctness never depends on any "
    "`taskname: default` tag in `[tasks]`. Tag it `default` anyway, as good practice for a human running "
    f"`sby -f {SBY_FILE}` with no task argument - just don't rely on that tag for {PRIMARY_TASK} itself to run.\n"
    f"By default, `[tasks]` should contain ONLY \"{PRIMARY_TASK}\" - do not add a `cover` task (or any other "
    f"extra task) unless {REFERENCE_SVA_FILE} itself defines a property that calls for demonstrating a specific "
    "reachable scenario. If every property in it is assertion-only (no cover-style intent anywhere in the "
    "original), a single task is the complete and correct translation - stop there, and do not add `cover` just "
    "because the syntax example below happens to illustrate it with one. An empty or trivial cover task is not "
    "harmless filler: it reports a vacuous \"DONE (PASS)\" even when nothing meaningful was checked, which is "
    "exactly the kind of degenerate shortcut rule 7 warns against, and every extra task is one more thing that "
    "can silently break (see the syntax pitfalls above). Only if the source genuinely calls for a second task, "
    "follow this shape:\n"
    "   [tasks]\n"
    f"   {PRIMARY_TASK}: default\n"
    "   cover\n"
    "\n"
    "   [options]\n"
    f"   mode {PRIMARY_TASK}\n"
    "   depth 40\n"
    "   cover: mode cover\n"
    "\n"
    "   [script]\n"
    f"   cover: read_verilog -sv -D FV_COVER {VERIFICATION_TOP}\n"
    f"   ~cover: read_verilog -sv -D FV_ASSERT {VERIFICATION_TOP}\n\n"
    "9. **Limited SystemVerilog Function Syntax**: Yosys's frontend only accepts a limited SystemVerilog subset "
    "even outside of SVA - notably, `return <expr>;` inside a function body is NOT supported and fails with a "
    "hard syntax error (confirmed directly: Yosys rejects it as `ERROR: syntax error, unexpected TOK_ID` at the "
    "`return` line). Any function ported from the original OneSpin property set - most commonly a golden-model "
    "reference function used by a functional-correctness property - must instead assign the result to the "
    "function's own name as its last statement (classic Verilog-2001 style: `function_name = <expr>;`, never "
    "`return <expr>;`).\n\n"

    "Your Tasks:\n"
    f"1. Instrument/rewrite {VERIFICATION_TOP}, reimplementing every property from "
    f"{REFERENCE_SVA_FILE} as SymbiYosys-compatible immediate assertions and auxiliary registers per rules "
    "1-7 and 9 above.\n"
    f"2. Generate {SBY_FILE}, a valid SymbiYosys configuration file (see rule 8), configured with the correct "
    "design files (Verilog via read_verilog), a `[files]` section listing every one of them (sby cannot find a "
    "file referenced by read_verilog unless it's also listed there), elaboration steps, and "
    "appropriate modes (bmc, prove, or cover).\n\n"
    "OUTPUT FORMAT REQUIREMENT:\n"
    "Respond ONLY with a valid, strictly formatted JSON array containing objects with keys \"file\", \"find\", and \"replace\". "
    "Do not include markdown code block wraps (` ```json `), preamble, or postscript prose.\n"
    "- Set \"file\" to a bare filename relative to the current working directory (e.g. \"" f"{VERIFICATION_TOP}" "\"), "
    "never an absolute path or one prefixed with a directory - the code applying these edits runs in a different "
    "working directory than any path shown above.\n"
    "- Set \"find\" to an exact, unique literal substring from the existing file to replace it.\n"
    "- Set \"find\" to \"\" (empty string) to create a new file or completely overwrite content.\n"
    "- Ensure all code inside \"replace\" properly escapes newlines (\\n) and internal quotes (\\\") to remain valid JSON."
)

EXPLAIN_AND_FIX_PROMPT = (
    "SymbiYosys executed successfully and identified a property violation. This is a translation bug, "
    "not a genuine RTL bug - see below for why.\n\n"
    f"The original OneSpin property set ({REFERENCE_SVA_FILE}, included below) is the authoritative "
    f"specification of intended behavior for these properties ({DESIGN_PROPERTIES}). {DESIGN_BUG_PRIOR}\n\n"
    "Perform the following two steps in exact order:\n\n"
    "1. **Reasoning Report**: Provide a concise explanation (3-5 sentences) detailing why the property "
    "failed and which part of the checker's translation is responsible. Trace it using the sby output "
    "log, failing assertion name, step number, and counterexample trace.\n"
    f"2. **Fix**: Propose a fix targeting {VERIFICATION_TOP} and/or {SBY_FILE} only - never the design "
    "files (edits to any other file are rejected automatically). If you cannot identify a checker-side "
    "explanation, say so in the reasoning report and provide an empty JSON array `[]` rather than "
    "guessing at a design change.\n\n"
    "STRICT OUTPUT FORMAT:\n"
    "REASONING:\n"
    "<your concise reasoning report>\n\n"
    "FIX:\n"
    "[{\"file\": \"path/to/file\", \"find\": \"exact string\", \"replace\": \"replacement string\"}]"
)
# END GENERAL, REUSABLE PROMPT CONTENT


def extract_json_array(text: str) -> Optional[list]:
    """Pull a JSON array of edits out of *text*.

    We ask the LLM for diagnosis/design + fix in one shot but never let it
    touch the files directly - relying on it to invoke a bash tool to apply
    a fix it found proved unreliable (see chia-hello-world/fix-fifo.py's
    history: it would narrate a fix, or even fabricate having run the tool,
    without the file changing). So it only proposes edits as data; we apply
    them ourselves deterministically. This tolerates a fenced code block or
    stray prose around the JSON, since the model doesn't always follow
    "only JSON" exactly.
    """
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
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    return None


def _run_streamed(cmd, timeout_seconds, on_line, env=None):
    """Run *cmd*, calling on_line(line) for each line of its live merged
    stdout+stderr as it arrives, and killing it if it hasn't finished after
    timeout_seconds.

    The timeout is enforced by a watchdog timer, not by timing out the read
    loop itself: `for line in proc.stdout` blocks on readline() waiting for
    more data, so a process that stalls without producing any more output
    (a stuck SMT solver, a hung generation) would never return from that
    loop and a `proc.wait(timeout=...)` placed after it would never get a
    chance to run. A timer that calls proc.kill() independently of the read
    loop's progress is what actually bounds the wall-clock time.

    Returns (returncode, combined_output, timed_out).
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1, env=env)
    timed_out = threading.Event()

    def _kill_on_timeout():
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout_seconds, _kill_on_timeout)
    timer.start()
    lines = []
    try:
        for line in proc.stdout:
            lines.append(line)
            on_line(line)
    finally:
        timer.cancel()
    proc.wait()
    return proc.returncode, "".join(lines), timed_out.is_set()


SBY_TIMEOUT_SECONDS = 3600 # Kill a hung/intractable sby run after this long

@ChiaFunction(resources={"sby": 1})
def run_sby(sby_file: str):
    """Run sby, printing its own progress output live instead of buffering
    it silently until the process exits. BMC/prove against an
    arithmetic-heavy design like this GCD core can legitimately run for a
    long time (or never converge), and capture_output=True gave no way to
    tell "still working" from "stuck". Killed after SBY_TIMEOUT_SECONDS if
    it hasn't finished by then, rather than blocking the job forever.

    Always requests PRIMARY_TASK explicitly rather than relying on
    sby_file's own "taskname: default" tagging - see PRIMARY_TASK's
    definition for why that distinction matters."""
    cmd = ["/opt/oss-cad-suite/bin/sby", "-f", sby_file, PRIMARY_TASK]
    returncode, output, timed_out = _run_streamed(cmd, SBY_TIMEOUT_SECONDS, lambda line: print(line, end=""))
    if timed_out:
        note = f"[TIMEOUT] sby did not finish within {SBY_TIMEOUT_SECONDS}s, killed\n"
        print(note)
        output += note
        returncode = -1
    return subprocess.CompletedProcess(cmd, returncode, output, "")

@ChiaFunction(resources={"sby": 1})
def read_files(filenames: list) -> dict:
    """Read each of `filenames` from the worker's cwd; None for one that
    doesn't exist yet (e.g. the .sby file, before the LLM creates it)."""
    result = {}
    for name in filenames:
        path = Path(name)
        result[name] = path.read_text(encoding="utf-8") if path.exists() else None
    return result

@ChiaFunction(resources={"sby": 1})
def apply_replacements(replacements: list) -> list:
    """Apply each {file, find, replace} edit, grouped so a file touched by
    several edits is only read/written once. An empty "find" creates the
    file (or fully overwrites it) with "replace".

    Rejects (without touching disk) any "file" that isn't a plain relative
    path within EDITABLE_FILES. An absolute path or one containing ".."
    would otherwise let a misread prompt (e.g. one that names an absolute
    local path for context - see TRANSLATE_PROMPT's history) make this
    write somewhere the code never intended, or just fail with a confusing
    FileNotFoundError against a directory tree that only exists on a
    different machine than the one this runs on. A file outside
    EDITABLE_FILES - most importantly the design itself - would let a
    misdiagnosed counterexample silently "fix" something this experiment
    guarantees it never touches (see DESIGN_BUG_PRIOR). Both are enforced
    here rather than left to prompt wording alone, since prompt
    instructions by themselves haven't proven reliable in this loop.

    Returns a per-edit report: how many times "find" occurred in that file
    *before* editing - 0 means it didn't match anything and was a no-op,
    which the caller should surface rather than silently trust; -1 means
    the "file" itself was rejected (see its "reason") and nothing in that
    group was applied. Always reported as applied (count 1) for an empty
    "find" (a full-file write).
    """
    by_file: dict = {}
    for r in replacements:
        by_file.setdefault(r["file"], []).append(r)

    report = []
    for filename, edits in by_file.items():
        reject_reason = None
        if os.path.isabs(filename) or ".." in Path(filename).parts:
            reject_reason = "not a relative path within the working directory"
        elif filename not in EDITABLE_FILES:
            reject_reason = f"not one of the editable files for this experiment ({', '.join(EDITABLE_FILES)})"
        if reject_reason:
            for r in edits:
                report.append({"file": filename, "find": r.get("find", ""), "count": -1, "reason": reject_reason})
            continue
        path = Path(filename)
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        for r in edits:
            find, replace = r.get("find", ""), r["replace"]
            if find:
                report.append({"file": filename, "find": find, "count": content.count(find)})
                content = content.replace(find, replace)
            else:
                report.append({"file": filename, "find": "", "count": 1})
                content = replace
        path.write_text(content, encoding="utf-8")
    return report


def classify_sby_result(response) -> str:
    """Distinguish sby's three actual outcomes from its own "DONE (...)"
    status line, rather than treating any nonzero returncode as "needs
    another fix attempt".

    sby itself already distinguishes "the setup is broken" from "the setup
    is fine and found a real violation": it prints DONE (PASS, rc=0),
    DONE (FAIL, rc=2), or DONE (ERROR, rc=16) (e.g. a Verilog syntax error,
    a missing source file, or an invalid .sby config). Collapsing FAIL and
    ERROR into one "not zero, keep looping" bucket meant a genuine
    counterexample - the engine running correctly and proving the property
    does NOT hold - got treated the same as a broken translation, spending
    the rest of the fix budget asking the LLM to "fix" a check that was
    never wrong.

    Returns "pass", "fail" (ran fine, found a real counterexample - this is
    a completed, meaningful result, not a failure to converge), or "error"
    (didn't get a clean run at all - the one case actually worth asking the
    LLM to fix). Falls back to "error" for a timeout or anything whose
    output doesn't contain one of sby's own DONE lines, since we can't
    otherwise tell those apart from a broken setup.
    """
    if "DONE (PASS" in response.stdout:
        return "pass"
    if "DONE (FAIL" in response.stdout:
        return "fail"
    return "error"


def snapshot_to_local_dir(filenames, local_working_dir):
    """Write each of `filenames`'s current content on the worker back to
    local_working_dir, so progress is visible on the real filesystem after
    every attempt regardless of how the loop eventually ends."""
    snapshot = get(read_files.chia_remote(filenames))
    for name, content in snapshot.items():
        if content is None:
            continue
        with open(os.path.join(local_working_dir, name), "w", encoding="utf-8") as f:
            f.write(content)
    print(f"Wrote worker files back to {local_working_dir}")


RATE_LIMIT_RETRIES = 6
RATE_LIMIT_BACKOFF_BASE = 60
RATE_LIMIT_BACKOFF_MAX = 600

# Vertex AI gemini-2.5-pro pricing (ported from 3-Fix-RTL/fix-rtl.py,
# confirmed there against Google's own pricing page,
# https://ai.google.dev/gemini-api/docs/pricing, September 2026):
# $1.25/1M input tokens and $10.00/1M output tokens for prompts <=200K
# tokens - doubles to $2.50/$15.00 above that threshold. Fine for this
# experiment's file sizes; update if a much larger design pushes a prompt
# past 200K tokens, or if pricing itself changes.
USD_PER_1M_INPUT_TOKENS: Optional[float] = 1.25
USD_PER_1M_OUTPUT_TOKENS: Optional[float] = 10.00


@ChiaFunction(resources={"vertex_creds": 0.01})
def call_llm_with_usage(llm: VertexGeminiLLM, user_message: str):
    """Returns (QueryResult, usage_dict). VertexGeminiLLM.prompt records
    usage on self._last_metadata, but only on the copy of the LLM object
    inside whatever process actually ran the call - a plain
    llm.prompt.chia_remote(llm, ...) dispatch returns only the QueryResult
    and discards it. Calling the undecorated llm.prompt(...) from inside
    this already-remote function reads the metadata in the same process,
    right after the call, instead of losing it (ported from
    3-Fix-RTL/fix-rtl.py, which tracks cost the same way).

    The resources= tag matches VertexGeminiLLM.prompt's own decorator
    exactly - confirmed necessary on that sibling project from a real
    failure: a bare @ChiaFunction() let Ray schedule this on a node
    without the right environment for it, crashing with a GLIBC version
    mismatch in cryptography's Rust extension on the very first call.

    Retries RateLimitError with exponential backoff (up to
    RATE_LIMIT_RETRIES times) instead of burning a whole setup-fix/
    counterexample-fix attempt on a transient rate limit. Converts
    MaxOutputTokensError into a no-op QueryResult, same as before this
    function existed."""
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            result = llm.prompt(user_message)
            usage = dict(getattr(llm, "_last_metadata", {}) or {})
            return result, usage
        except MaxOutputTokensError as e:
            print(f"WARNING: LLM response truncated at max_output_tokens ({e}); "
                  "treating this attempt as a no-op")
            return (QueryResult(result="", returncode=-1, stderr=str(e),
                                 stream_result="", success=False), {})
        except RateLimitError as e:
            if attempt == RATE_LIMIT_RETRIES - 1:
                print(f"WARNING: still rate-limited after {RATE_LIMIT_RETRIES} "
                      f"attempts ({e}); treating this attempt as a no-op")
                return (QueryResult(result="", returncode=-1, stderr=str(e),
                                     stream_result="", success=False), {})
            backoff = min(RATE_LIMIT_BACKOFF_BASE * 2 ** attempt, RATE_LIMIT_BACKOFF_MAX)
            print(f"Rate-limited by Vertex (attempt {attempt + 1}/{RATE_LIMIT_RETRIES}); "
                  f"backing off {backoff}s before retrying the same prompt")
            time.sleep(backoff)


RUN_USAGE_TOTALS = {"input_tokens": 0, "output_tokens": 0, "num_calls": 0}


def format_usage(usage_totals: dict) -> str:
    """Formats either RUN_USAGE_TOTALS (has "num_calls") or a single call's
    per-attempt usage dict (just input_tokens/output_tokens, straight from
    VertexGeminiLLM._last_metadata) - .get(..., 0) makes this robust to
    both shapes, and to the empty dict AntigravityLLM/OpenCodeLLM calls
    report (no usage visibility there), which just renders as zeros."""
    input_tokens = usage_totals.get("input_tokens", 0)
    output_tokens = usage_totals.get("output_tokens", 0)
    line = f"input_tokens={input_tokens}, output_tokens={output_tokens}"
    if "num_calls" in usage_totals:
        line = f"llm_calls={usage_totals['num_calls']}, " + line
    if USD_PER_1M_INPUT_TOKENS is not None and USD_PER_1M_OUTPUT_TOKENS is not None:
        cost = (input_tokens / 1e6 * USD_PER_1M_INPUT_TOKENS +
                 output_tokens / 1e6 * USD_PER_1M_OUTPUT_TOKENS)
        line += f", estimated_cost=${cost:.4f}"
    else:
        line += " (set USD_PER_1M_INPUT_TOKENS/USD_PER_1M_OUTPUT_TOKENS for a $ estimate)"
    return line


def call_llm(llm, prompt_text: str):
    """Dispatch a prompt to whichever backend `llm` is. Returns
    (QueryResult, usage_dict) - usage_dict is only ever non-empty for
    VertexGeminiLLM (see call_llm_with_usage's docstring for why cost
    tracking needs its own remote hop rather than a plain
    llm.prompt.chia_remote(...) dispatch); AntigravityLLM/OpenCodeLLM
    calls report an empty dict, which format_usage renders as zeros.

    AntigravityLLM gets the streaming wrapper plus its content-filter retry
    (see prompt_with_retry). OpenCodeLLM has none of that machinery (no
    known content-filter failure mode, and its own .prompt() already
    streams internally) - both classes share the same
    prompt(user_message, tools=None) -> QueryResult ChiaFunction shape, so
    calling it directly is enough.

    VertexGeminiLLM goes through call_llm_with_usage, which already handles
    rate-limit backoff/retry and max-output-token truncation. The try/
    except wrapped around that dispatch here is a second, broader safety
    net: VertexGeminiLLM.prompt() can also raise ContentBlockedError (or,
    in principle, something call_llm_with_usage doesn't anticipate), and
    any of those left uncaught would propagate through get(...) as a
    RayTaskError and kill the whole multi-hour job, throwing away every
    attempt made so far - a much worse failure mode than a response that
    just fails to parse (which the caller already tolerates as a no-op
    attempt, see extract_json_array's callers).
    """
    if isinstance(llm, AntigravityLLM):
        return prompt_with_retry(llm, prompt_text), {}
    if isinstance(llm, VertexGeminiLLM):
        try:
            resp, usage = get(call_llm_with_usage.chia_remote(llm, prompt_text))
        except Exception as exc:
            print(f"WARNING: LLM call failed ({exc!r}); treating this attempt as a no-op")
            return QueryResult(result="", returncode=-1, stderr=str(exc), stream_result="", success=False), {}
        RUN_USAGE_TOTALS["input_tokens"] += usage.get("input_tokens", 0)
        RUN_USAGE_TOTALS["output_tokens"] += usage.get("output_tokens", 0)
        RUN_USAGE_TOTALS["num_calls"] += 1
        return resp, usage
    try:
        return get(llm.prompt.chia_remote(llm, prompt_text)), {}
    except Exception as exc:
        print(f"WARNING: LLM call failed ({exc!r}); treating this attempt as a no-op")
        return QueryResult(result="", returncode=-1, stderr=str(exc), stream_result="", success=False), {}

def build_llm(backend: str, vertex_project: str):
    if backend == "opencode":
        return OpenCodeLLM(model="opencode/big-pickle", timeout_seconds=3600)
    return VertexGeminiLLM(model="gemini-2.5-pro", project=vertex_project, location="global",
                           timeout_seconds=3600, max_tokens=65536)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", choices=["opencode", "vertex"], default="vertex",
                         help="Which LLM backend to use (default: vertex)")
    parser.add_argument("--working-dir", required=True,
                         help="Absolute path to THIS machine's real checkout of this project "
                              "(e.g. the same directory you ran `chia job submit --working-dir .` "
                              "from). Deliberately has no default: the driver's own OS working "
                              "directory is Ray's ephemeral per-job snapshot, NOT this real path, "
                              "so there is no safe value to fall back to automatically.")
    parser.add_argument("--vertex-project", default=os.environ.get("VERTEX_PROJECT", DEFAULT_VERTEX_PROJECT),
                         help="GCP project for Vertex AI Gemini calls, used only when --llm=vertex "
                              "(default: $VERTEX_PROJECT if set, else the project this was "
                              "developed against).")
    args = parser.parse_args()
    llm = build_llm(args.llm, args.vertex_project)

    # Loop: run sby against SBY_FILE (which doesn't exist yet on attempt 1,
    # so the first run just fails with an ERROR and kicks off the loop), and
    # ask the LLM to instrument the sources / (re)write the .sby file when
    # it does, applying its proposed edits ourselves. Stops on:
    #   - PASS: the properties hold.
    #   - FAIL: sby ran cleanly and found a genuine counterexample - this is
    #     a completed, meaningful result (a real design issue), NOT a sign
    #     the translation is broken, so it must not be treated as "keep
    #     asking the LLM to fix it" the same way an ERROR is. Conflating the
    #     two used to burn the whole fix budget re-litigating a check that
    #     was never wrong (see classify_sby_result's docstring).
    #   - ERROR after MAX_FIX_ATTEMPTS: still broken, giving up.
    response = get(run_sby.chia_remote(SBY_FILE))
    print("Response from run_sby:")
    print(response)
    result = classify_sby_result(response)

    attempt = 0
    while result == "error" and attempt < MAX_FIX_ATTEMPTS:
        attempt += 1
        print(f"sby setup broken (attempt {attempt}/{MAX_FIX_ATTEMPTS}); asking the LLM to instrument/fix the files")

        current_files = get(read_files.chia_remote(SOURCE_FILES + REFERENCE_FILES + [SBY_FILE]))
        files_section = "\n\n".join(
            f"=== {name} ===\n{content if content is not None else '(does not exist yet)'}"
            for name, content in current_files.items()
        )

        prompt = (
            TRANSLATE_PROMPT
            + "\n\n" + files_section
            + "\n\nsby stdout:\n" + response.stdout
            + "\n\nsby stderr:\n" + response.stderr
        )

        resp, usage = call_llm(llm, prompt)
        print(f"LLM final response ({format_usage(usage)}):")
        print(resp.result)

        replacements = extract_json_array(resp.result)
        if not replacements:
            print("WARNING: could not parse a JSON replacement list from "
                  "the LLM's response; files left unchanged")
        else:
            report = get(apply_replacements.chia_remote(replacements))
            for r in report:
                if r["count"] == -1:
                    print(f"WARNING: rejected edit to {r['file']!r} - {r['reason']}")
                elif not r["find"]:
                    print(f"Created/overwrote {r['file']}")
                elif r["count"] == 0:
                    print(f"WARNING: find text not present in {r['file']}, no-op: {r['find']!r}")
                else:
                    print(f"Replaced {r['count']} occurrence(s) in {r['file']}")

        response = get(run_sby.chia_remote(SBY_FILE))
        print("Response from run_sby:")
        print(response)
        result = classify_sby_result(response)

        # Snapshot every touched file after each attempt, so progress is
        # visible even if we never converge within MAX_FIX_ATTEMPTS.
        snapshot_to_local_dir(SOURCE_FILES + [SBY_FILE], args.working_dir)

    if attempt == 0:
        # The initial run_sby (before the loop) already returned "pass" or
        # "fail" with no attempts needed (e.g. SBY_FILE already existed from
        # a previous run) - the loop body's snapshot above never ran.
        snapshot_to_local_dir(SOURCE_FILES + [SBY_FILE], args.working_dir)

    # Second, separate loop: sby ran cleanly and found a counterexample. For
    # a Property Set Conversion experiment like this one the design is
    # assumed correct (see DESIGN_BUG_PRIOR), so this is always a
    # translation bug, never a design defect - and apply_replacements
    # enforces that mechanically by rejecting any edit outside
    # EDITABLE_FILES, regardless of what the LLM proposes. Ask the LLM to
    # explain the root cause (printed as a reasoning report - useful on its
    # own even if no fix is found) and then propose a checker/config fix,
    # applying it the same deterministic way as the setup-fix loop. Bounded
    # by its own budget (MAX_COUNTEREXAMPLE_FIX_ATTEMPTS), separate from the
    # setup-fix one, and stops the moment the result stops being "fail"
    # (pass = fixed; error = the fix attempt broke the build, which this
    # loop doesn't try to repair itself - see the final report below).
    counterexample_attempt = 0
    while result == "fail" and counterexample_attempt < MAX_COUNTEREXAMPLE_FIX_ATTEMPTS:
        counterexample_attempt += 1
        print(f"Counterexample found (fix attempt {counterexample_attempt}/{MAX_COUNTEREXAMPLE_FIX_ATTEMPTS}); "
              "asking the LLM to explain the root cause and propose a checker/config fix")

        current_files = get(read_files.chia_remote(SOURCE_FILES + REFERENCE_FILES + [SBY_FILE]))
        files_section = "\n\n".join(
            f"=== {name} ===\n{content if content is not None else '(does not exist yet)'}"
            for name, content in current_files.items()
        )

        prompt = (
            EXPLAIN_AND_FIX_PROMPT
            + "\n\n" + files_section
            + "\n\nsby stdout:\n" + response.stdout
            + "\n\nsby stderr:\n" + response.stderr
        )

        resp, usage = call_llm(llm, prompt)

        replacements = extract_json_array(resp.result)
        # Best-effort split of the reasoning prose from the JSON tail, so
        # the report reads cleanly even though it's the same response text
        # extract_json_array already parses.
        json_start = resp.result.find("[")
        reasoning = resp.result[:json_start].strip() if json_start != -1 else resp.result.strip()
        reasoning = re.sub(r"^REASONING:\s*", "", reasoning)
        print(f"Reasoning report ({format_usage(usage)}):")
        print(reasoning or "(none provided)")

        if replacements is None:
            print("WARNING: could not parse a JSON fix list from the LLM's "
                  "response; stopping fix attempts")
            break
        if not replacements:
            print("LLM proposed no fix (empty JSON array) - treating this as "
                  "a fundamental limitation in the translation rather than "
                  "something to patch.")
            break

        report = get(apply_replacements.chia_remote(replacements))
        for r in report:
            if r["count"] == -1:
                print(f"WARNING: rejected edit to {r['file']!r} - {r['reason']}")
            elif not r["find"]:
                print(f"Created/overwrote {r['file']}")
            elif r["count"] == 0:
                print(f"WARNING: find text not present in {r['file']}, no-op: {r['find']!r}")
            else:
                print(f"Replaced {r['count']} occurrence(s) in {r['file']}")

        response = get(run_sby.chia_remote(SBY_FILE))
        print("Response from run_sby:")
        print(response)
        result = classify_sby_result(response)

        snapshot_to_local_dir(SOURCE_FILES + [SBY_FILE], args.working_dir)

    if result == "pass":
        if counterexample_attempt:
            print(f"Translation fix found and properties now hold, after {attempt} "
                  f"setup-fix attempt(s) and {counterexample_attempt} counterexample-fix attempt(s).")
        else:
            print(f"Properties hold after {attempt} LLM attempt(s).")
    elif result == "fail":
        print(f"sby ran successfully and found a counterexample after "
              f"{attempt} setup-fix attempt(s) and {counterexample_attempt} counterexample-fix "
              "attempt(s) that the LLM could not resolve - this is a still-imperfect "
              "translation, not a genuine RTL bug (RTL edits are out of scope for this "
              "experiment; see DESIGN_BUG_PRIOR). See the reasoning report(s) and last "
              "run_sby output above.")
    else:
        print(f"sby setup broken after {attempt} setup-fix attempt(s) and "
              f"{counterexample_attempt} counterexample-fix attempt(s); giving up.")

    print(f"\nTotal LLM usage this run: {format_usage(RUN_USAGE_TOTALS)}")


if __name__ == "__main__":
    main()
