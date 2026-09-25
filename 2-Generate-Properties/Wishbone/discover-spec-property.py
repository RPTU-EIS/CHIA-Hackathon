from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.llm_call import QueryResult
from pathlib import Path
from typing import Optional
import subprocess
import json
import re
import os
import argparse
from chia.models.vertex import VertexGeminiLLM

MAX_FIX_ATTEMPTS = 6 # Give up after this many LLM attempts fixing the sby setup
MAX_VIOLATION_ATTEMPTS = 6 # Give up after this many LLM attempts triaging/fixing a sby-reported violation

# Falls back to this literal project if VERTEX_PROJECT isn't set - the GCP
# project this was originally developed against. Anyone else running this
# (e.g. a hackathon judge with their own GCP project/quota) should set
# VERTEX_PROJECT rather than edit this file; --vertex-project overrides
# both for a single invocation.
DEFAULT_VERTEX_PROJECT = "project-be5ca9cc-e88a-41a1-81f"

# Design sources the LLM may instrument/rewrite. Unlike the DIT loops (see
# chia-sha512/discover-dit-property.py), there is no two-instance miter here
# at all - this checks ONE instance of a Wishbone slave against a written
# protocol spec, not timing equivalence between two copies. CHECKER_FILE
# doesn't exist yet at the start; the LLM creates it from scratch.
DUT_FILE = "wishbone_ram.sv"
CHECKER_FILE = "wishbone_ram_checker.sv"
SOURCE_FILES = [DUT_FILE, CHECKER_FILE]
SBY_FILE = "wishbone_ram.sby"
DUT_TOP = "wishbone_ram"
CHECKER_TOP = "wishbone_ram_checker"

# The Wishbone B4 spec, read once by the driver and attached natively to
# every spec-grounded LLM call (see VertexGeminiLLM.prompt's pdf_bytes) -
# Gemini reads the PDF directly, including its timing diagrams and tables,
# rather than going through a lossy separate text-extraction step. Read as
# bytes here (not via a remote ChiaFunction) since the driver already has
# direct filesystem access to --working-dir (see main()'s argparse).
SPEC_PDF = "wbspec_b4.pdf"


DISCOVER_SPEC_PROPERTIES_PROMPT = "" \
"We're checking whether the attached RTL design correctly implements the " \
"bus protocol defined in the attached specification document (a PDF - " \
"read it directly, including its timing diagrams and signal tables, not " \
"just any text you can infer from context). This design under test " \
"(the DUT) is a WISHBONE SLAVE - a single-cycle, non-pipelined 'CLASSIC' " \
"Wishbone interface (no burst/pipelined-feedback signals). Steps:\n\n" \
"1. Map the DUT's actual port names to the specification's canonical " \
f"signal names ({DUT_FILE} shown below) - e.g. its clock/reset ports " \
"(note any polarity difference from the spec's convention, such as an " \
"active-low reset named differently than the spec's canonical name - " \
"this is a legitimate implementation choice, not a violation, as long as " \
"it's handled correctly in the checker), and its ADR/DAT/WE/SEL/STB/ACK/" \
"CYC-equivalent ports.\n" \
"2. From the specification, derive a SET of formal properties this " \
"slave must satisfy - not just one. Ground every property in a specific " \
"part of the spec you can point to (e.g. \"the spec states in its " \
"description of the ACK_O signal that...\") - do not invent a property " \
"that sounds reasonable but isn't actually stated or implied by the " \
"spec. For a classic Wishbone slave, relevant categories typically " \
"include (this list is a starting point, not exhaustive - read the spec " \
"for what actually applies to this interface):\n" \
"   - The slave must not assert its acknowledgement output unless the " \
"master actually initiated a cycle (the cycle and strobe inputs were " \
"asserted).\n" \
"   - Once a cycle is initiated and not yet acknowledged, the slave " \
"must eventually respond (a bounded-response / liveness property) - " \
"exact bound depends on whether this is provable by BMC/induction at " \
"a reasonable depth; if not directly provable, a `cover()` demonstrating " \
"the response CAN happen is a reasonable fallback and should be noted " \
"as such in your reasoning.\n" \
"   - Byte-select semantics: when a write is qualified by a subset of " \
"select lines, only the selected byte lane(s) should be modified; " \
"unselected lanes must be unaffected.\n" \
"   - Read semantics: a read cycle must not modify the addressed " \
"storage, and must return the data actually stored at that address.\n" \
"   - Any other explicit requirement the spec states for a slave " \
"interface that this DUT's ports make checkable (e.g. behavior during " \
"reset, behavior when the cycle is aborted mid-transfer).\n" \
"3. Design and write these properties into a NEW checker file, " \
f"{CHECKER_FILE}, that instantiates {DUT_TOP} directly (a single " \
"instance - this is not a two-instance timing-equivalence check, just a " \
"direct correctness check against the spec) and drives its inputs as " \
"free/unconstrained signals for the solver to explore, with the " \
"properties written as plain immediate `assert(expr)`/`assume(expr)`/" \
"`cover(expr)` statements referencing the instance's ports directly.\n\n" \
"This is the same underlying open-source toolchain (yosys + smtbmc, no " \
"`-verific` plugin) used for prior formal work in this project family, " \
"which hit several sharp edges. Apply these rules directly rather than " \
"rediscovering them by trial and error:\n\n" \
"1. Open-source yosys's native SVA support has NO concurrent-SVA " \
"operators at all: no `##`, `|->`, `|=>`, no `property`/`endproperty`, " \
"no `sequence`. Only a plain immediate `assert(expr)`/`assume(expr)`/" \
"`cover(expr)` inside an ordinary `always @(posedge clk)` block is " \
"understood. Write every property directly in this style.\n" \
"2. MOST IMPORTANT, and the one thing that will silently produce a " \
"MEANINGLESS result if skipped: DO NOT USE `bind` AT ALL. Put the " \
f"checker's instantiation of {DUT_TOP} and all the assert/assume/cover " \
"statements directly in the same file, read in the same `read_verilog`/" \
"`read_slang` invocation as the DUT - never bind a separate module " \
"across a different read, and never reference the DUT instance's " \
"INTERNAL (non-port) signals via dot-notation; only reference its real " \
"ports.\n" \
"3. Any auxiliary register you add purely for verification (e.g. a " \
"\"cycle started N cycles ago\" tracker for a bounded-response check) " \
"needs an explicit initial value (`initial x = 0;`). Without one, BMC/" \
"k-induction is free to pick an arbitrary power-up value and can use " \
"that freedom to fabricate a violation that never involved any real " \
"triggering event. NEVER give a WIDE MEMORY ARRAY (e.g. a `reg [W-1:0] " \
"mem[N-1:0]` with large N) a loop-based initializer to satisfy this, " \
"even if you find one already present but commented out - a for-loop " \
"that touches every entry of a large array gets fully unrolled during " \
"elaboration and can turn a sub-second run into a multi-minute hang or " \
"an out-of-memory crash, for a benefit no current property actually " \
"needs (a property that cares about a specific memory location's " \
"contents should track a small, explicit golden-model register for just " \
"that location instead, as already done elsewhere in this checker, not " \
"rely on the whole array starting at a known value).\n" \
"4. To restrict properties to reachable (from-reset) executions, force " \
"a genuine reset at the start of every trace with an explicit `assume`, " \
"not merely a gate on \"having observed reset at some point\": " \
"`reg init; initial init = 1; always @(posedge clk) begin if (init) " \
"assume (<reset condition>); ... init <= 0; end`. Without this, BMC can " \
"explore a trace where reset is simply never asserted, starting every " \
"register at an arbitrary uninitialized value.\n" \
"5. Before committing to `mode bmc` with a large explicit `depth`, " \
"consider `mode prove` (BMC base case + k-induction) instead - " \
"especially since this slave's behavior is driven by internal state " \
"(a memory array, a registered ack). Plain BMC has to unroll the whole " \
"design just to reach interesting behavior, and the SMT solver's cost " \
"grows sharply with depth. `mode prove`'s k-induction proves the " \
"asserted properties are themselves an inductive invariant, at a cost " \
"roughly independent of how many actual cycles a real trace might need. " \
"Try `mode prove` first; fall back to explicit-depth `mode bmc` only if " \
"a property genuinely isn't provable this way.\n" \
"6. If `mode prove`'s BASE CASE passes but the INDUCTION step fails, " \
"that is usually NOT a real counterexample and NOT a broken setup - it " \
"means the property, though true, isn't by itself a strong enough " \
"inductive invariant. The fix is to STRENGTHEN the induction hypothesis: " \
"add an auxiliary invariant (often referencing more of the DUT's " \
"internal state, promoted to a checker-visible signal if needed) " \
"alongside the original assertion, not in place of it.\n" \
"7. Do not name an explicit solver on the `[engines]` line (e.g. " \
"`smtbmc z3`) unless you have a specific reason to - plain `smtbmc` " \
"uses sby's default solver, which is usually the fastest choice for " \
"this class of problem; an explicitly-named alternative can be " \
"dramatically slower or effectively hang on the same files that solve " \
"in seconds under the default.\n\n" \
"Your job, across two steps:\n" \
f"1. Write {CHECKER_FILE} with the property set you designed.\n" \
f"2. Create {SBY_FILE}, a valid SymbiYosys job file, that reads both " \
f"{DUT_FILE} and {CHECKER_FILE} together and checks the properties " \
"(pick whichever mode/engine fits, per rule 5 above).\n" \
"3. We will run sby against it and give you the output on the next " \
"iteration if it fails, so you can keep fixing it.\n\n" \
"Respond with ONLY a JSON array of {\"file\": ..., \"find\": ..., " \
"\"replace\": ...} objects - no prose, no markdown, nothing else. Each " \
"entry edits one file: \"find\" must be an exact, unique, literal " \
"snippet copied verbatim from that file's current content (shown " \
"below), or an empty string to create the file (or fully replace its " \
"content) with \"replace\"."


EXPLAIN_AND_FIX_PROMPT = "" \
"sby ran the current setup successfully but did not confirm the " \
"property set holds. Two distinct situations lead here - check the sby " \
"output below to see which one you're in:\n\n" \
"- A GENUINE COUNTEREXAMPLE (`DONE (FAIL...)`, a failing BMC base-case " \
"step): sby found a concrete trace violating one of the asserted " \
"properties. Its EXACT source text and the CONCRETE counterexample " \
"testbench (the actual cycle-by-cycle stimulus that triggers it) are " \
"provided below, separately from the general file listing - use them, " \
"not a reconstruction from memory of what the property was meant to " \
"check.\n" \
"- AN INCONCLUSIVE K-INDUCTION PROOF (`DONE (UNKNOWN...)`, base case " \
"passed but the induction step failed): sby found NO actual violation, " \
"but its induction step also couldn't PROVE the property holds - see " \
"rule 6 above (strengthen the induction hypothesis with an auxiliary " \
"invariant); this is NOT evidence of a real spec violation.\n\n" \
"For a genuine counterexample, use the SPECIFICATION to decide WHICH of " \
"the following is true - this triage step, and only this step, needs " \
"the spec's prose; read it directly, do not rely on memory of what " \
"Wishbone \"usually\" requires:\n\n" \
"(a) THE IMPLEMENTATION GENUINELY VIOLATES THE SPEC: the property is a " \
"correct formalization of a real requirement, and the DUT's behavior " \
"actually breaks it. Cite the specific requirement it breaks.\n" \
"(b) THE PROPERTY IS WRONG, NOT THE IMPLEMENTATION: the property " \
"misreads the spec passage it was meant to encode (wrong polarity, " \
"missing a legitimate exception the spec allows, wrong signal, an " \
"assumption not actually required). Cite the passage that shows the " \
"property is wrong.\n\n" \
"Once triage is settled, the ACTUAL FIX must be grounded in the " \
"PROPERTY'S EXACT WORDING and the CONCRETE TRACE below, NOT in a fresh " \
"re-derivation from the spec's prose - the spec's job was only to settle " \
"(a) vs (b) above; the property text is already the agreed, unambiguous " \
"formal target, and re-deriving \"what it should do\" from prose each " \
"attempt is exactly what has caused fix attempts to drift and fail " \
"before. Work mechanically: read the counterexample testbench's " \
"stimulus cycle by cycle, evaluate the failing property's exact boolean " \
"expression against those concrete signal values, and identify the " \
"precise cycle and signal(s) where it evaluates false. Then:\n" \
f"(a) propose a fix to the DUT ({DUT_FILE}) that changes its behavior so " \
"the exact property expression evaluates true on this exact stimulus " \
"(and, by construction, on the class of inputs it represents) - " \
"changing as little else as possible, and never by disabling or " \
"narrowing functionality just to quiet the checker; or\n" \
f"(b) fix the property in {CHECKER_FILE} so its expression correctly " \
"matches what you cited from the spec, and confirm it still evaluates " \
"false on the same trace for the RIGHT reason (i.e. it isn't now " \
"trivially true regardless of the DUT's behavior).\n\n" \
"If earlier attempts are listed below, read them: do not propose an " \
"edit equivalent to one already tried and shown to still fail - if your " \
"first instinct matches a prior attempt, that specific idea is already " \
"known not to work; find what's different this time.\n\n" \
"Do two things, in this order:\n\n" \
"1. Write a short reasoning report in plain English (a few sentences): " \
"which of the two top-level situations is this (genuine counterexample " \
"vs. inconclusive induction), and if a genuine counterexample, which of " \
"(a)/(b) it is and why (cite the specific spec passage that settles " \
"it), then a precise trace-grounded description of the fix (which " \
"cycle/signal in the concrete trace your fix targets).\n" \
"2. Then propose a fix as a JSON array of {\"file\": ..., \"find\": ..., " \
"\"replace\": ...} edits - same format and rules as before (\"find\" " \
"must be an exact, unique, literal snippet copied verbatim from that " \
"file's current content shown below, or empty to create/overwrite a " \
"file with \"replace\"). If you cannot determine a fix with confidence " \
"(the spec is ambiguous, or no clean fix exists), say so in the " \
"reasoning report instead and return an empty JSON array [].\n\n" \
"Format your response exactly as:\n" \
"REASONING:\n<your reasoning report>\n\nFIX:\n<the JSON array, and " \
"nothing else after it>"


SUMMARY_PROMPT = "" \
"The automated Wishbone-slave spec-conformance verification run for this " \
"design has finished. Below is the step-by-step history of what was " \
"tried (setup-fix attempts deriving the property set, then violation-" \
"triage attempts if sby found something), followed by the final sby " \
"status.\n\n" \
"Write a concise final summary in plain English - a few short " \
"paragraphs, no JSON, no markdown formatting - covering:\n" \
"1. What properties were checked and why (briefly - which spec " \
"requirements they encode).\n" \
"2. What happened at each meaningful step: what was tried, what worked " \
"or didn't, and why (in your own words, not just restating the log) - " \
"in particular, for any counterexample encountered, whether it turned " \
"out to be a genuine implementation bug (cite the spec requirement it " \
"broke) or a wrongly-formulated property (cite what was corrected and " \
"why).\n" \
"3. The final outcome and what it means for this design - is it, as " \
"far as this run could establish, a spec-compliant Wishbone slave for " \
"the properties checked? Any implementation fixes applied should be " \
"named plainly (what was wrong, what changed).\n\n" \
"Write this summary NO MATTER THE RESULT - whether the run ended in " \
"PASS, FAIL, or ERROR. It should honestly reflect what actually " \
"happened, including if the loop never converged, if a fix attempt made " \
"things worse, or if the setup never got past basic errors. This is a " \
"closing report for someone who did not watch the run happen live, not " \
"a new fix attempt - do not propose further edits or JSON here."


def extract_json_array(text: str) -> Optional[list]:
    """Pull a JSON array of edits out of *text*. Tolerates a fenced code
    block or stray prose around the JSON, since the model doesn't always
    follow "only JSON" exactly. Returns None on parse failure, distinct
    from a valid empty [] (a deliberate "no fix" signal)."""
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


_FAILED_ASSERTION_RE = re.compile(r"failed assertion \S+ at ([\w./\\-]+):(\d+)\.")


def extract_failing_property_context(sby_stdout: str, current_files: dict, context_lines: int = 3) -> Optional[str]:
    """Pull the literal source text around the specific line sby reported
    as the failing assertion, from whichever file it's actually in.

    sby's own stdout only ever says `failed assertion NAME at file:line` -
    never the source text at that line, let alone its surrounding context.
    Grounding the fix step in the property's own exact wording (rather
    than a paraphrase reconstructed from memory of what the property was
    "supposed to" check) matters because that wording is the actual,
    already-agreed-upon formal target - re-deriving it from the
    specification prose each attempt is a source of drift between
    attempts and wasted effort once triage has already settled that the
    property itself is correct.
    """
    match = _FAILED_ASSERTION_RE.search(sby_stdout)
    if not match:
        return None
    filename, line_no = match.group(1), int(match.group(2))
    content = current_files.get(filename)
    if content is None:
        return None
    lines = content.splitlines()
    start = max(0, line_no - 1 - context_lines)
    end = min(len(lines), line_no + context_lines)
    snippet = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start, end))
    return f"=== {filename}, around line {line_no} (the EXACT failing property) ===\n{snippet}"


def _run_streamed(cmd, on_line, env=None):
    """Run *cmd* to completion (no timeout - sby runs for as long as it
    needs), calling on_line(line) for each line of its live merged
    stdout+stderr as it arrives instead of buffering silently until exit.

    Returns (returncode, combined_output).
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1, env=env)
    lines = []
    for line in proc.stdout:
        lines.append(line)
        on_line(line)
    proc.wait()
    return proc.returncode, "".join(lines)


def call_llm(llm, prompt_text: str, pdf_bytes: Optional[bytes] = None) -> QueryResult:
    """Dispatch a spec-grounded prompt to VertexGeminiLLM, attaching the
    spec PDF natively when given (see VertexGeminiLLM.prompt's pdf_bytes).

    Wraps the call in a try/except because VertexGeminiLLM.prompt() raises
    a typed exception (MaxOutputTokensError, ContentBlockedError,
    RateLimitError, ...) instead of returning a failed QueryResult on
    unrecoverable errors. Left uncaught, that exception propagates through
    get(...) as a RayTaskError and kills the whole multi-hour job, throwing
    away every attempt made so far - a much worse failure mode than a
    response that just fails to parse (which the caller already tolerates
    as a no-op attempt). Converting it to an empty, unsuccessful
    QueryResult here lets this attempt be skipped with a warning instead.
    """
    try:
        return get(llm.prompt.chia_remote(llm, prompt_text, pdf_bytes=pdf_bytes))
    except Exception as exc:
        print(f"WARNING: LLM call failed ({exc!r}); treating this attempt as a no-op")
        return QueryResult(result="", returncode=-1, stderr=str(exc), stream_result="", success=False)


@ChiaFunction(resources={"sby": 1})
def run_sby(sby_file: str):
    """Run sby, printing its own progress output live instead of buffering
    it silently until the process exits. No timeout - runs to completion
    regardless of how long that takes."""
    cmd = ["/opt/oss-cad-suite/bin/sby", "-f", sby_file]
    returncode, output = _run_streamed(cmd, lambda line: print(line, end=""))
    return subprocess.CompletedProcess(cmd, returncode, output, "")

@ChiaFunction(resources={"sby": 1})
def read_counterexample_trace(sby_file: str) -> Optional[str]:
    """Read back sby's generated counterexample testbench - the actual,
    concrete cycle-by-cycle signal trace that triggered a FAIL, as a plain
    Verilog testbench (`--dump-vlogtb`, on by default for BMC/prove
    engines).

    Without this, the LLM never sees what actually happened - sby's own
    stdout only ever says `failed assertion NAME at file:line`, with no
    signal values at all. Every fix attempt then has to reason about the
    violation from memory of the property's intent and the specification,
    not from the concrete scenario that broke it - a major reason fix
    attempts can flail even when the diagnosis (which property, roughly
    why) is already correct: they're each guessing at a different
    plausible-sounding scenario instead of looking at the one that
    actually occurred.

    sby names the task directory after the .sby file's stem (e.g.
    "wishbone_ram.sby" -> "wishbone_ram/"). Tries the BMC/basecase
    testbench first (`engine_0/trace_tb.v`, a "fail" result), then the
    induction one (`engine_0/trace_induct_tb.v`, an "unknown" result) -
    returns None if sby hasn't produced either (e.g. on a pass).
    """
    task_dir = Path(sby_file).stem
    for name in ("trace_tb.v", "trace_induct_tb.v"):
        path = Path(task_dir) / "engine_0" / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None

@ChiaFunction(resources={"sby": 1})
def read_files(filenames: list) -> dict:
    """Read each of `filenames` from the worker's cwd; None for one that
    doesn't exist yet (e.g. the checker/.sby file, before the LLM creates it)."""
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

    Returns a per-edit report: how many times "find" occurred in that file
    *before* editing - 0 means it didn't match anything and was a no-op,
    which the caller should surface rather than silently trust. Always
    reported as applied for an empty "find" (a full-file write).
    """
    by_file: dict = {}
    for r in replacements:
        by_file.setdefault(r["file"], []).append(r)

    report = []
    for filename, edits in by_file.items():
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
    """Distinguish sby's actual outcomes from its own "DONE (...)" status
    line, rather than treating any nonzero returncode as "needs another
    fix attempt".

    Returns "pass", "fail" (ran fine, found a real counterexample - a
    completed, meaningful result), "unknown" (mode prove's inconclusive
    outcome, `DONE (UNKNOWN, rc=4)`: BMC base case passed but the
    k-induction step couldn't close the proof - not evidence of a broken
    setup or a real violation, sby ran completely cleanly), or "error"
    (didn't get a clean run at all - a genuine syntax/elaboration failure,
    the one case actually worth asking the LLM to fix the *setup* for).

    Folding "unknown" into "error" would hand the LLM the setup-fix loop's
    "something is broken, fix it" prompt for a run that actually completed
    cleanly - observed elsewhere in this project family to push the LLM
    into rewriting things and introducing real syntax errors trying to
    "fix" something that was never broken.
    """
    if "DONE (PASS" in response.stdout:
        return "pass"
    if "DONE (FAIL" in response.stdout:
        return "fail"
    if "DONE (UNKNOWN" in response.stdout:
        return "unknown"
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


def build_llm(vertex_project: str):
    return VertexGeminiLLM(model="gemini-2.5-pro", project=vertex_project, location="global",
                           timeout_seconds=3600, max_tokens=65536)


def main():
    parser = argparse.ArgumentParser(description="Spec-grounded property-discovery loop for the "
                                                   "Wishbone RAM slave (report-and-fix, unlike "
                                                   "discover-covering-properties.py in this same "
                                                   "directory).")
    parser.add_argument("--working-dir", required=True,
                         help="Absolute path to THIS machine's real checkout of this project "
                              "(e.g. the same directory you ran `chia job submit --working-dir .` "
                              "from). Deliberately has no default: the driver's own OS working "
                              "directory is Ray's ephemeral per-job snapshot, NOT this real path, "
                              "so there is no safe value to fall back to automatically.")
    parser.add_argument("--vertex-project", default=os.environ.get("VERTEX_PROJECT", DEFAULT_VERTEX_PROJECT),
                         help="GCP project for Vertex AI Gemini calls (default: $VERTEX_PROJECT if "
                              "set, else the project this was developed against).")
    args = parser.parse_args()

    llm = build_llm(args.vertex_project)
    spec_pdf_bytes = Path(os.path.join(args.working_dir, SPEC_PDF)).read_bytes()
    print(f"Loaded {SPEC_PDF} ({len(spec_pdf_bytes)} bytes) for native attachment to spec-grounded prompts")

    # Plain-English record of what happened at each step, regardless of
    # phase or outcome. Fed to the LLM at the very end (see SUMMARY_PROMPT)
    # to produce a human-readable closing report.
    history = []

    # Separately tracks which fixes touched the checker/property file vs.
    # the DUT itself, so the final report can honestly distinguish "we
    # corrected a wrong property" from "we found and fixed a real
    # implementation bug" rather than a bare, ambiguous PASS.
    property_fixes = []
    implementation_fixes = []

    # First loop: get sby to a clean run at all (PASS, FAIL, or UNKNOWN -
    # anything but a broken setup). CHECKER_FILE/SBY_FILE don't exist yet
    # on attempt 1, so the first run just errors and kicks this off.
    response = get(run_sby.chia_remote(SBY_FILE))
    print("Response from run_sby:")
    print(response)
    result = classify_sby_result(response)
    history.append(f"Initial sby run (before any fix attempts): result={result}.")

    attempt = 0
    while result == "error" and attempt < MAX_FIX_ATTEMPTS:
        attempt += 1

        print(f"sby setup broken (attempt {attempt}/{MAX_FIX_ATTEMPTS}); asking the LLM to derive/fix the property set and setup")

        current_files = get(read_files.chia_remote(SOURCE_FILES + [SBY_FILE]))
        files_section = "\n\n".join(
            f"=== {name} ===\n{content if content is not None else '(does not exist yet)'}"
            for name, content in current_files.items()
        )

        prompt = (
            DISCOVER_SPEC_PROPERTIES_PROMPT
            + "\n\n" + files_section
            + "\n\nsby stdout:\n" + response.stdout
            + "\n\nsby stderr:\n" + response.stderr
        )

        resp : QueryResult = call_llm(llm, prompt, pdf_bytes=spec_pdf_bytes)
        print("LLM final response:")
        print(resp.result)

        replacements = extract_json_array(resp.result)
        if not replacements:
            print("WARNING: could not parse a JSON replacement list from "
                  "the LLM's response; files left unchanged")
            fix_desc = "could not parse a fix from the LLM's response; files left unchanged"
        else:
            report = get(apply_replacements.chia_remote(replacements))
            for r in report:
                if not r["find"]:
                    print(f"Created/overwrote {r['file']}")
                elif r["count"] == 0:
                    print(f"WARNING: find text not present in {r['file']}, no-op: {r['find']!r}")
                else:
                    print(f"Replaced {r['count']} occurrence(s) in {r['file']}")
            fix_desc = f"applied {len(replacements)} edit(s)"

        response = get(run_sby.chia_remote(SBY_FILE))
        print("Response from run_sby:")
        print(response)
        result = classify_sby_result(response)
        history.append(f"Setup-fix attempt {attempt}: sby reported an error. "
                        f"LLM response (excerpt): {resp.result.strip()[:400]!r}. "
                        f"{fix_desc}. New result: {result}.")

        snapshot_to_local_dir(SOURCE_FILES + [SBY_FILE], args.working_dir)

    if attempt == 0:
        snapshot_to_local_dir(SOURCE_FILES + [SBY_FILE], args.working_dir)

    # Second, separate loop: sby ran cleanly but reported "fail" (genuine
    # counterexample) or "unknown" (inconclusive induction). Ask the LLM to
    # triage which it is and fix accordingly - a property fix (unlike the
    # DIT loops in this project family, this one IS allowed to patch the
    # DUT for a genuine spec violation, per this project's explicit policy)
    # or an implementation fix. last_good_files tracks the most recent
    # state sby could actually parse and run, so a fix attempt that breaks
    # the build gets reverted rather than left in place - the run must
    # never end with unparseable, broken files on disk just because the
    # last fix attempt happened to be bad.
    last_good_files = get(read_files.chia_remote(SOURCE_FILES + [SBY_FILE]))

    violation_attempt = 0
    while result in ("fail", "unknown") and violation_attempt < MAX_VIOLATION_ATTEMPTS:
        violation_attempt += 1
        reason = "genuine counterexample" if result == "fail" else "inconclusive k-induction proof"
        print(f"sby ran cleanly but did not confirm the property set ({reason}; "
              f"violation-triage attempt {violation_attempt}/{MAX_VIOLATION_ATTEMPTS}); "
              "asking the LLM to triage and propose a fix")

        current_files = get(read_files.chia_remote(SOURCE_FILES + [SBY_FILE]))
        files_section = "\n\n".join(
            f"=== {name} ===\n{content if content is not None else '(does not exist yet)'}"
            for name, content in current_files.items()
        )

        # Ground the fix in the property's exact wording and the concrete
        # counterexample, not a paraphrase reconstructed from memory - see
        # extract_failing_property_context's and read_counterexample_trace's
        # docstrings for why this matters (sby's own stdout gives neither).
        failing_property_context = extract_failing_property_context(response.stdout, current_files)
        trace_tb = get(read_counterexample_trace.chia_remote(SBY_FILE))
        trace_section = (
            f"=== Concrete counterexample testbench (the actual stimulus "
            f"that triggers this) ===\n{trace_tb}"
            if trace_tb else "(no counterexample testbench available for this result)"
        )

        prior_attempts = [h for h in history if h.startswith("Violation-triage attempt")]
        prior_section = (
            "\n".join(f"- {h}" for h in prior_attempts) if prior_attempts
            else "(this is the first violation-triage attempt)"
        )

        prompt = (
            EXPLAIN_AND_FIX_PROMPT
            + "\n\n" + (failing_property_context or "(could not locate the failing property's exact source line)")
            + "\n\n" + trace_section
            + "\n\nPrior violation-triage attempts (do not repeat one of these):\n" + prior_section
            + "\n\nAll current files:\n" + files_section
            + "\n\nsby stdout:\n" + response.stdout
            + "\n\nsby stderr:\n" + response.stderr
        )

        resp : QueryResult = call_llm(llm, prompt, pdf_bytes=spec_pdf_bytes)

        replacements = extract_json_array(resp.result)
        # Best-effort split of the reasoning prose from the JSON tail, so
        # the report reads cleanly even though it's the same response text
        # extract_json_array already parses.
        json_start = resp.result.find("[")
        reasoning = resp.result[:json_start].strip() if json_start != -1 else resp.result.strip()
        reasoning = re.sub(r"^REASONING:\s*", "", reasoning)
        print("Reasoning report:")
        print(reasoning or "(none provided)")

        if replacements is None:
            print("WARNING: could not parse a JSON fix list from the LLM's "
                  "response; stopping violation-triage attempts")
            history.append(f"Violation-triage attempt {violation_attempt}: reasoning "
                            f"(excerpt): {reasoning[:400]!r}. Could not parse a fix "
                            f"from the response; stopped violation-triage attempts.")
            break
        if not replacements:
            print("LLM proposed no fix (empty JSON array) - treating this as "
                  "an unresolved case (ambiguous spec, or no clean fix found).")
            history.append(f"Violation-triage attempt {violation_attempt}: reasoning "
                            f"(excerpt): {reasoning[:400]!r}. LLM proposed no fix.")
            break

        report = get(apply_replacements.chia_remote(replacements))
        for r in report:
            if not r["find"]:
                print(f"Created/overwrote {r['file']}")
            elif r["count"] == 0:
                print(f"WARNING: find text not present in {r['file']}, no-op: {r['find']!r}")
            else:
                print(f"Replaced {r['count']} occurrence(s) in {r['file']}")

        touched = sorted({r["file"] for r in replacements})
        response = get(run_sby.chia_remote(SBY_FILE))
        print("Response from run_sby:")
        print(response)
        result = classify_sby_result(response)

        if result == "error":
            # The patch broke the build - revert to the last state sby
            # could actually parse rather than leaving broken files
            # behind, then re-run sby to confirm the revert restores it.
            print(f"Violation-triage attempt {violation_attempt} broke the build "
                  f"(sby result=error); reverting to the last known-good state.")
            revert_ops = [{"file": name, "find": "", "replace": content}
                          for name, content in last_good_files.items()
                          if content is not None]
            get(apply_replacements.chia_remote(revert_ops))
            response = get(run_sby.chia_remote(SBY_FILE))
            result = classify_sby_result(response)
            history.append(f"Violation-triage attempt {violation_attempt}: reasoning "
                            f"(excerpt): {reasoning[:400]!r}. Applied edit(s) to "
                            f"{touched}, but that broke sby's setup (result=error) - "
                            f"reverted to the last known-good state (result after "
                            f"revert: {result}).")
        else:
            history.append(f"Violation-triage attempt {violation_attempt}: reasoning "
                            f"(excerpt): {reasoning[:400]!r}. Applied edit(s) to "
                            f"{touched}. New result: {result}.")
            if result in ("fail", "unknown"):
                last_good_files = get(read_files.chia_remote(SOURCE_FILES + [SBY_FILE]))
            if DUT_FILE in touched:
                implementation_fixes.append(f"Attempt {violation_attempt}: {reasoning[:200]!r}")
            if CHECKER_FILE in touched:
                property_fixes.append(f"Attempt {violation_attempt}: {reasoning[:200]!r}")

        snapshot_to_local_dir(SOURCE_FILES + [SBY_FILE], args.working_dir)

    if result == "pass":
        if implementation_fixes and property_fixes:
            print(f"Property set holds after {attempt} setup-fix attempt(s) and "
                  f"{violation_attempt} violation-triage attempt(s) - both "
                  f"implementation fix(es) and property correction(s) were needed:")
        elif implementation_fixes:
            print(f"Property set holds after {attempt} setup-fix attempt(s) and "
                  f"{violation_attempt} violation-triage attempt(s) - a genuine "
                  f"implementation bug was found and fixed:")
        elif property_fixes:
            print(f"Property set holds after {attempt} setup-fix attempt(s) and "
                  f"{violation_attempt} violation-triage attempt(s) - the "
                  f"implementation was fine, the property set needed correction:")
        elif violation_attempt:
            print(f"Property set holds after {attempt} setup-fix attempt(s) and "
                  f"{violation_attempt} violation-triage attempt(s).")
        else:
            print(f"Property set holds after {attempt} setup-fix attempt(s) - the "
                  "design appears to be spec-compliant for the properties checked.")
        for e in implementation_fixes:
            print(f"  [implementation fix] {e}")
        for e in property_fixes:
            print(f"  [property correction] {e}")
    elif result == "fail":
        print(f"sby ran successfully and found a standing counterexample after "
              f"{attempt} setup-fix attempt(s) and {violation_attempt} "
              "violation-triage attempt(s). See the reasoning report(s) and last "
              "run_sby output above.")
    elif result == "unknown":
        print(f"sby ran cleanly but k-induction stayed inconclusive after "
              f"{attempt} setup-fix attempt(s) and {violation_attempt} "
              "violation-triage attempt(s) (base case passed, induction step "
              "never closed); giving up. The property may still be true - it "
              "likely just needs a stronger auxiliary invariant than what was tried.")
    else:
        print(f"sby setup broken after {attempt} setup-fix attempt(s) and "
              f"{violation_attempt} violation-triage attempt(s); giving up.")

    # Unconditional closing report: ask the LLM to summarize the whole run
    # in its own words, regardless of how it ended.
    history_section = "\n".join(f"- {h}" for h in history) if history else "(no fix attempts were needed)"
    impl_section = "\n".join(f"- {e}" for e in implementation_fixes) if implementation_fixes else "(none)"
    prop_section = "\n".join(f"- {e}" for e in property_fixes) if property_fixes else "(none)"
    summary_prompt = (
        SUMMARY_PROMPT
        + "\n\nStep-by-step history:\n" + history_section
        + "\n\nImplementation (DUT) fixes applied:\n" + impl_section
        + "\n\nProperty/checker corrections applied:\n" + prop_section
        + f"\n\nFinal classification: {result}"
        + "\n\nLast sby stdout (tail):\n" + response.stdout[-3000:]
    )
    summary_resp = call_llm(llm, summary_prompt)
    print("\n" + "=" * 80)
    print("Final summary (LLM-authored):")
    print("=" * 80)
    print(summary_resp.result.strip() or "(LLM produced no summary)")


if __name__ == "__main__":
    main()
