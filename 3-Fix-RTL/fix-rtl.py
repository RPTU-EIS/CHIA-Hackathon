"""Generic fix-rtl loop: one script for any project shaped like the
proc/HMAC case studies under Case Studies/ - a GOLDEN, already-validated
formal property suite (bound into the design via SystemVerilog `bind`, or
wired in however the source files already do it) checks a design, and
whenever a property fails, an LLM diagnoses the counterexample and patches
the RTL - never the properties - then the loop re-runs. Repeats until every
requested engine is clean, or the round budget runs out.

Takes the design's source files, which one is writable, and which engines
to run as command-line arguments, instead of hardcoding them per project
(mirroring 4-Security-Verification/upec-dit-check.py's --source-files
pattern) - see --help. Usage, run from THIS directory (3-Fix-RTL/) so
Case Studies/ ships as part of the job's working_dir:

    chia job submit --working-dir . -- python fix-rtl.py \\
        --source-files "Case Studies/01-Processor/proc-package.sv" \\
                       "Case Studies/01-Processor/proc-seq-mutated.sv" \\
                       "Case Studies/01-Processor/proc_checker.sv" \\
        --writable "Case Studies/01-Processor/proc-seq-mutated.sv" \\
        --top proc --engines bmc cover

ENGINES: --engines takes one or more of bmc, cover, prove and runs them
CONCURRENTLY (a real non-blocking wait-for-any via chia_wait/TrackedRef,
not sequential blocking calls), letting them inform/cross-check each
other:
  - cover finds a trigger reachable only at depth D' beyond bmc's current
    depth => bmc's next run targets at least D' (a depth that's too
    shallow doesn't just slow bug-hunting down, it can hide bugs in
    states it never unrolls far enough to reach - confirmed directly on
    the HMAC project's K20-area states).
  - prove returns PASS while bmc/cover are still running => done: an
    unbounded proof subsumes any bounded result, so stop everything and
    report success immediately. prove timing out (not a genuine sby
    verdict - prove has no natural stopping point short of PASS/FAIL) is
    retried a bounded number of times, then left alone until the next fix
    gives it a fresh attempt - PDR does not converge quickly on designs
    this size, confirmed empirically, so this is expected, not an error.
  - any engine's genuine FAIL cancels the other running engines first
    (their result would be checking RTL that's about to change anyway),
    then diagnoses and fixes via the REASONING/FIX protocol below, then
    restarts every requested engine fresh against the patched RTL.
Passing just "--engines bmc" runs the simple single-engine loop (no
concurrency, no cover/prove machinery engaged at all).

GROUNDING: the prompt requires the LLM to quote the exact current line(s)
it's diagnosing, verbatim from the file content shown to it, before
proposing a fix - added after a real round where the reasoning confidently
described a line that did not exist anywhere in the file, reasoned about
it at length, and proposed a fix for a bug that was never there.

FILESYSTEM: read_files/apply_replacements are plain local functions (not
remote ChiaFunctions) operating on --working-dir - REQUIRED, no default,
and must be the real absolute path to this machine's actual checkout (the
directory you ran `chia job submit --working-dir .` from). This is not
just a convenience requirement: confirmed directly that the driver
process's own OS working directory is Ray's ephemeral per-job snapshot
(deleted once the job ends), never the real submission directory - a file
written to the resolved "." default was gone the moment the job exited,
even though the driver otherwise has real, direct NFS access to the
actual project path when given explicitly. run_sby, meanwhile, executes
on a "sby"-resourced WORKER, which does NOT share that filesystem at all
either (confirmed from a real crash: a worker turned out to run under a
completely separate environment - /home/ray/anaconda3/..., no NFS mount
whatsoever - even on the same physical node the driver itself runs on).
So run_sby takes an explicit
file_contents={filename: content} argument, built by the driver right
before each launch, and writes those files into its own node-local
working directory before invoking sby. This passes live file state
through Ray's own object store - the one mechanism Ray actually
guarantees moves data between driver and worker correctly - instead of
assuming any shared filesystem.

COST: LLM token usage (and, if you fill in current Vertex pricing below,
an estimated $ cost) is tracked and reported per round and cumulatively.
VertexGeminiLLM.prompt records this on self._last_metadata, but only on
the copy of the LLM object inside whatever process actually ran the call;
a plain llm.prompt.chia_remote(llm, ...) dispatch returns only the
QueryResult and discards it. call_llm_with_usage below calls the
undecorated llm.prompt(...) from inside its own already-remote function so
the metadata is read in the same process, right after the call, and
returned alongside the response instead of being lost.
"""
import argparse
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from chia.base.ChiaFunction import ChiaFunction, get, chia_cancel
from chia.base.chia_wait import TrackedRef, chia_wait
from chia.base.llm_call import QueryResult
from chia.models.vertex import VertexGeminiLLM, MaxOutputTokensError, RateLimitError

# Falls back to this literal project if VERTEX_PROJECT isn't set - the GCP
# project this was originally developed against. Anyone else running this
# (e.g. a hackathon judge with their own GCP project/quota) should set
# VERTEX_PROJECT rather than edit this file; --vertex-project overrides
# both for a single invocation.
DEFAULT_VERTEX_PROJECT = "project-be5ca9cc-e88a-41a1-81f"

RATE_LIMIT_RETRIES = 6
RATE_LIMIT_BACKOFF_BASE = 60
RATE_LIMIT_BACKOFF_MAX = 600
POLL_INTERVAL_SECONDS = 30   # How often the orchestrator wakes to print
                              # progress / check for a finished engine.
                              # Doesn't affect how long any sby run takes.
PROVE_MAX_RETRIES = 2   # After a prove attempt times out (our own kill
                        # timer, not a genuine sby verdict - see module
                        # docstring), retry this many times before leaving
                        # it be until the next fix gives it a fresh design.

# Vertex AI gemini-2.5-pro pricing (confirmed against Google's own pricing
# page, https://ai.google.dev/gemini-api/docs/pricing, September 2026):
# $1.25/1M input tokens and $10.00/1M output tokens for prompts <=200K
# tokens - doubles to $2.50/$15.00 above that threshold. Fine for
# case-study-sized golden files; update if a much larger project pushes a
# round's prompt past 200K tokens, or if pricing itself changes.
USD_PER_1M_INPUT_TOKENS: Optional[float] = 1.25
USD_PER_1M_OUTPUT_TOKENS: Optional[float] = 10.00


FIX_PROMPT_TEMPLATE = "" \
"You are debugging a hardware design's RTL ({writable}) against a GOLDEN, " \
"ALREADY-VALIDATED formal property suite (bound into module `{top}` via " \
"the other source files shown below). The property suite is authoritative " \
"ground truth: if sby reports a property failing - whether from a bounded " \
"BMC run, a cover-mode run, or an UNBOUNDED prove/induction run, all shown " \
"below as \"a genuine counterexample\" - the RTL is wrong, NEVER the " \
"property. Do not propose any edit to any file other than {writable} " \
"under any circumstances - every other file is fixed context, not " \
"something a fix can touch, and any such edit will be silently discarded " \
"before it reaches disk anyway.\n\n" \
"sby found a genuine counterexample: shown below is the full RTL, the " \
"full property suite (for context - to understand exactly what a failing " \
"assertion expects), sby's own summary of which assertion(s) failed and " \
"at which step, and a testbench reproduction of the counterexample's " \
"primary-input stimulus (the design's actual top-level inputs, cycle by " \
"cycle; internal signals are not dumped, so trace through the RTL's own " \
"logic by hand, cycle by cycle, using those inputs, to see what the " \
"design actually computes versus what the failing property expects). sby " \
"may report MORE THAN ONE failing assertion at once - if so, treat them " \
"together: they may share one root cause, or be independent bugs each " \
"needing their own fix.\n\n" \
"Diagnose the ROOT CAUSE(S) - the actual logic error(s) in {writable} - " \
"and make MINIMAL, GENERAL fixes. Fix the underlying bug, not the " \
"specific counterexample: a fix that only avoids this specific " \
"counterexample without correcting the general logic will simply " \
"resurface as a different failure next round, wasting a round without " \
"real progress. If sby instead reports the setup itself broke (a syntax " \
"error from a previous round's edit, not a counterexample - you will see " \
"'ERROR' and a compiler message instead of 'failed assertion'), fix that " \
"syntax issue directly, in the same file.\n\n" \
"GROUNDING REQUIREMENT: for every bug you claim exists, first locate and " \
"quote the EXACT current line(s) from {writable}'s content shown below - " \
"copied verbatim, not paraphrased and not reconstructed from general " \
"knowledge of how this kind of design usually looks. If you cannot find " \
"the literal text of the line you are about to call buggy anywhere in " \
"the shown file, that is a sign your diagnosis is wrong - go back and " \
"re-read the actual file rather than reasoning about code that only " \
"resembles what's really there. A reasoning report that describes a line " \
"the file doesn't actually contain is exactly the failure mode this " \
"instruction exists to prevent - confirmed from a real round where this " \
"happened, on a different project: the reasoning confidently described a " \
"line that does not exist anywhere in the file, reasoned about it at " \
"length, and proposed a fix for a bug that was never there.\n\n" \
"Do this in one response, in two parts:\n" \
"1. In plain English, a few sentences per failing property: quote the " \
"exact current line(s) you're diagnosing (per the grounding requirement " \
"above), then explain what the counterexample actually shows (walk " \
"through the relevant cycles and what the RTL computes at each one), " \
"which property it violates and why, what the root-cause bug is, and " \
"what fix you're about to make and why it's the general fix rather than " \
"a special case.\n" \
"2. The fix itself, as a JSON array (it may contain more than one edit if " \
"more than one property failed).\n\n" \
"Format your response EXACTLY as:\n" \
"REASONING:\n<your explanation from part 1>\n\n" \
"FIX:\n<the JSON array from part 2, and nothing else after it>\n\n" \
"The JSON array is a list of {{\"file\": ..., \"find\": ..., \"replace\": " \
"...}} objects (only \"{writable}\" is ever a valid \"file\"): \"find\" " \
"must be an exact, unique, literal snippet copied verbatim from that " \
"file's CURRENT content shown below - copy it exactly, whitespace " \
"included, or the edit will silently match nothing and do nothing. " \
"Prefer the smallest snippet that uniquely identifies the buggy line(s) " \
"over reproducing a large block verbatim."

DEPTH_PROMPT_TEMPLATE = "" \
"Before debugging begins, propose a SMALL starting BMC depth (number of " \
"clock cycles to unroll) for a fast, cheap first debugging pass of " \
"{writable} against its property suite. This does NOT need to be deep " \
"enough to reach every reachable state - a separate cover-mode run at a " \
"larger depth may be running concurrently and will tell the loop exactly " \
"how deep bmc needs to go once it finishes (or the loop will escalate " \
"depth itself as rounds pass); this starting depth only needs to make " \
"the FIRST few debugging rounds run quickly. Prefer a SMALLER number when " \
"in doubt. Read the RTL/property suite below and use it to judge a " \
"reasonable starting point; it's fine (and expected) if it's too shallow " \
"to reach every state.\n\n" \
"Reply with a brief justification, then end your response with a single " \
"final line in EXACTLY this form:\nDEPTH: <a single positive integer>"


def extract_json_array(text: str) -> Optional[list]:
    """Pull a JSON array of edits out of *text* - tolerates a fenced code
    block or stray prose around it. strict=False: "find" must be an exact
    literal copy of the source (whitespace included), and tab-indented RTL
    means a literal tab can land unescaped inside a JSON string, which
    strict JSON rejects - confirmed as a real, otherwise-silent failure
    mode on an earlier project."""
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


def _run_streamed(cmd, timeout_seconds, on_line, env=None, cwd=None):
    """Run *cmd*, streaming its merged stdout+stderr line by line to
    on_line, killed by a watchdog timer if it hasn't finished in time.
    Returns (returncode, combined_output, timed_out)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1, env=env, cwd=cwd)
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


@ChiaFunction(resources={"vertex_creds": 0.01})
def call_llm_with_usage(llm: VertexGeminiLLM, user_message: str):
    """Returns (QueryResult, usage_dict). See the module docstring's COST
    section for why this calls the undecorated llm.prompt(...) inside its
    own already-remote function rather than a second .chia_remote hop. The
    resources= tag matches VertexGeminiLLM.prompt's own decorator exactly
    - confirmed necessary from a real failure on an earlier project: a
    bare @ChiaFunction() let Ray schedule this on a node without the right
    environment for it, crashing with a GLIBC version mismatch in
    cryptography's Rust extension on the very first call."""
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            result = llm.prompt(user_message)
            usage = dict(getattr(llm, "_last_metadata", {}) or {})
            return result, usage
        except MaxOutputTokensError as e:
            print(f"WARNING: LLM response truncated at max_output_tokens ({e}); "
                  "treating this round as if no edits were returned")
            return (QueryResult(result="", returncode=-1, stderr=str(e),
                                 stream_result="", success=False), {})
        except RateLimitError as e:
            if attempt == RATE_LIMIT_RETRIES - 1:
                print(f"WARNING: still rate-limited after {RATE_LIMIT_RETRIES} "
                      f"attempts ({e}); treating this round as if no edits "
                      "were returned")
                return (QueryResult(result="", returncode=-1, stderr=str(e),
                                     stream_result="", success=False), {})
            backoff = min(RATE_LIMIT_BACKOFF_BASE * 2 ** attempt, RATE_LIMIT_BACKOFF_MAX)
            print(f"Rate-limited by Vertex (attempt {attempt + 1}/{RATE_LIMIT_RETRIES}); "
                  f"backing off {backoff}s before retrying the same prompt")
            time.sleep(backoff)


RUN_USAGE_TOTALS = {"input_tokens": 0, "output_tokens": 0, "num_calls": 0}


def call_llm(llm, user_message: str):
    """Driver-side wrapper: dispatches call_llm_with_usage remotely, blocks
    for the result, and folds the token usage into RUN_USAGE_TOTALS.
    Returns (QueryResult, usage_dict)."""
    resp, usage = get(call_llm_with_usage.chia_remote(llm, user_message))
    RUN_USAGE_TOTALS["input_tokens"] += usage.get("input_tokens", 0)
    RUN_USAGE_TOTALS["output_tokens"] += usage.get("output_tokens", 0)
    RUN_USAGE_TOTALS["num_calls"] += 1
    return resp, usage


def format_usage(usage_totals: dict) -> str:
    """Formats either RUN_USAGE_TOTALS (has num_calls) or a single call's
    per-round usage dict (only input_tokens/output_tokens/num_turns,
    straight from VertexGeminiLLM._last_metadata - no num_calls key) -
    .get(..., 0)/the "in" check make this robust to both shapes."""
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


@ChiaFunction(resources={"sby": 1})
def run_sby(sby_file: str, timeout_seconds: int, file_contents: dict):
    """Run sby, streaming its own progress output live.

    file_contents is {filename: content} for every file this run needs
    (the generated .sby file plus every RTL/checker source) - written into
    this task's own node-local working directory before sby runs. See the
    module docstring's FILESYSTEM section for why this can't be a shared
    path instead.

    On ERROR (broken setup), appends the tail of <workdir>/model/design*.log
    from the first actual compiler error onward - sby's own console output
    only ever says "see full log for details". On FAIL, appends
    <workdir>/engine_0/trace_tb.v (or, for prove-mode abc-pdr
    counterexamples that don't always land at that exact path, whatever
    trace file exists in engine_0) - the counterexample's primary-input
    stimulus, so the diagnosing LLM has the actual input sequence to trace
    through by hand. On any result, appends the workdir's PASS/FAIL marker
    file: sby's own console summary truncates long reached/unreached lists
    with "and N further ...", but that marker file has the same lines
    without truncation."""
    for name, content in file_contents.items():
        if content is not None:
            path = Path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    cmd = ["/opt/oss-cad-suite/bin/sby", "-f", sby_file]
    returncode, output, timed_out = _run_streamed(cmd, timeout_seconds, lambda line: print(line, end=""))
    if timed_out:
        note = f"[TIMEOUT] sby did not finish within {timeout_seconds}s, killed\n"
        print(note)
        output += note
        returncode = -1

    workdir = Path(sby_file).stem

    if "ERROR" in output:
        for log_name in ("design.log", "design_prep.log", "design_smt2.log"):
            log_path = Path(workdir) / "model" / log_name
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                start = next((i for i, l in enumerate(lines) if re.search(r"\b(error|Error)\b", l)), 0)
                lines = lines[start:]
                if len(lines) > 150:
                    lines = lines[:150] + ["...(truncated)..."]
                output += f"\n\n--- {workdir}/model/{log_name} (from first error) ---\n" + "\n".join(lines)

    if "DONE (FAIL" in output:
        tb_path = Path(workdir) / "engine_0" / "trace_tb.v"
        if tb_path.exists():
            tb_text = tb_path.read_text(encoding="utf-8", errors="replace")
            output += f"\n\n--- {workdir}/engine_0/trace_tb.v (counterexample stimulus) ---\n{tb_text}"
        else:
            engine_dir = Path(workdir) / "engine_0"
            if engine_dir.exists():
                for cand in sorted(engine_dir.glob("*tb*.v")) + sorted(engine_dir.glob("*.vcd")):
                    if cand.suffix == ".v":
                        output += f"\n\n--- {cand} (counterexample stimulus) ---\n" + \
                                  cand.read_text(encoding="utf-8", errors="replace")
                        break

    for marker_name in ("PASS", "FAIL"):
        marker_path = Path(workdir) / marker_name
        if marker_path.exists():
            marker_text = marker_path.read_text(encoding="utf-8", errors="replace")
            output += f"\n\n--- {workdir}/{marker_name} (full untruncated summary) ---\n{marker_text}"

    return subprocess.CompletedProcess(cmd, returncode, output, "")


def read_files(base_dir: Path, filenames: list) -> dict:
    """Plain local function, NOT a remote ChiaFunction: the driver process
    already has direct, working access to base_dir (the NFS-shared project
    directory), so there's no scheduling question to resolve here."""
    result = {}
    for name in filenames:
        path = base_dir / name
        result[name] = path.read_text(encoding="utf-8") if path.exists() else None
    return result


def apply_replacements(base_dir: Path, replacements: list) -> list:
    """Plain local function - see read_files' docstring for why. Writes
    directly to base_dir, the one real copy every sby invocation (via its
    file_contents argument, read from here right before each launch)
    reads from too."""
    by_file: dict = {}
    for r in replacements:
        by_file.setdefault(r["file"], []).append(r)

    report = []
    for filename, edits in by_file.items():
        path = base_dir / filename
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
    if "DONE (PASS" in response.stdout:
        return "pass"
    if "DONE (FAIL" in response.stdout:
        return "fail"
    return "error"


def extract_failing_assertions(stdout: str) -> list:
    return sorted(set(re.findall(r"failed assertion \S+\\(\S+) at", stdout)))


def extract_cover_reached_steps(stdout: str):
    """Parse "reached cover statement X at file:pos step N" lines to find,
    per cover point, the deepest step at which it was confirmed reached -
    exactly "how far bmc needs to unroll to exercise this property's
    trigger". Returns (max_step_overall, reached: {name: step},
    unreached: set(name))."""
    reached = {}
    for m in re.finditer(r"[Rr]eached cover statement \S+\\(\S+) at \S+ step (\d+)", stdout):
        name, step = m.group(1), int(m.group(2))
        reached[name] = max(reached.get(name, 0), step)
    all_names = set(re.findall(r"\S+\\(\S+) at \S+:\d+\.\d+-\d+\.\d+$", stdout, re.MULTILINE))
    unreached = all_names - set(reached)
    max_step = max(reached.values()) if reached else 0
    return max_step, reached, unreached


class FixRtlLoop:
    """Bundles CLI-derived config (source files, writable target, engine
    selection, depths/timeouts) with the running loop state, so run_sby's
    file_contents and the .sby files it needs can be built generically for
    whatever project this run targets."""

    def __init__(self, args):
        self.working_dir = Path(args.working_dir).resolve()
        self.source_files = args.source_files   # driver-relative paths, e.g.
                                                  # "Case Studies/01-Processor/x.sv"
        self.writable = args.writable
        if self.writable not in self.source_files:
            raise ValueError(f"--writable {self.writable!r} must be one of --source-files")
        self.golden_files = [f for f in self.source_files if f != self.writable]
        self.top = args.top
        self.engines = set(args.engines)
        self.case_dir = Path(self.writable).parent

        # WORKER-side names are bare basenames, never the driver-relative
        # path: run_sby writes file_contents into a flat per-task temp
        # directory, and a nested relative path like "Case Studies/01-
        # Processor/x.sv" would fail with FileNotFoundError (parent
        # doesn't exist there) - confirmed directly. self.basename_of maps
        # each source file to what sby actually sees; the generated .sby's
        # own [files]/read_slang sections list basenames too, matching.
        self.basename_of = {f: Path(f).name for f in self.source_files}
        if len(set(self.basename_of.values())) != len(self.source_files):
            raise ValueError("--source-files basenames must be unique (sby "
                              "sees them in a flat per-task directory)")
        self.writable_basename = self.basename_of[self.writable]
        self.bmc_sby_name = f"{args.name}_bmc.sby"
        self.cover_sby_name = f"{args.name}_cover.sby"
        self.prove_sby_name = f"{args.name}_prove.sby"
        self.final_cover_sby_name = f"{args.name}_final_cover.sby"

        self.bmc_start_depth = args.bmc_start_depth
        self.bmc_max_depth = args.bmc_max_depth
        self.cover_start_depth = args.cover_start_depth
        self.cover_max_depth = args.cover_max_depth
        self.bmc_timeout = args.bmc_timeout
        self.cover_timeout = args.cover_timeout
        self.prove_timeout = args.prove_timeout
        self.max_rounds = args.max_rounds

        self.fix_prompt = FIX_PROMPT_TEMPLATE.format(writable=self.writable, top=self.top)
        self.depth_prompt = DEPTH_PROMPT_TEMPLATE.format(writable=self.writable)

    # -- file helpers, bound to this run's working_dir --

    def read(self, filenames: list) -> dict:
        return read_files(self.working_dir, filenames)

    def apply(self, replacements: list) -> list:
        return apply_replacements(self.working_dir, replacements)

    def files_section_for(self, filenames) -> str:
        current_files = self.read(filenames)
        return "\n\n".join(
            f"=== {name} ===\n{content if content is not None else '(does not exist yet)'}"
            for name, content in current_files.items()
        )

    def apply_edits_guarded(self, replacements) -> bool:
        """Rejects (loudly, never silently) any edit outside self.writable
        before it reaches disk - the technical enforcement half of "the
        properties are golden": even if the LLM ignores the prompt and
        proposes an edit to a golden file, it never actually lands on
        disk."""
        if not replacements:
            return False
        allowed, rejected = [], []
        for r in replacements:
            if r.get("file") == self.writable:
                allowed.append(r)
            else:
                rejected.append(r)
        for r in rejected:
            print(f"REJECTED edit to {r.get('file')!r} - only {self.writable!r} "
                  "is writable (everything else is golden, never editable)")
        if not allowed:
            return False
        report = self.apply(allowed)
        for r in report:
            if not r["find"]:
                print(f"Created/overwrote {r['file']}")
            elif r["count"] == 0:
                print(f"WARNING: find text not present in {r['file']}, no-op: {r['find']!r}")
            else:
                print(f"Replaced {r['count']} occurrence(s) in {r['file']}")
        return True

    # -- .sby generation --

    def build_sby(self, mode: str, depth: Optional[int], engine_line: str) -> str:
        """Constructs a complete .sby file from scratch (no read-modify-
        write of a pre-existing one needed - just a template filled in
        from this run's own config), matching the read_slang/prep recipe
        established for the golden checkers this loop targets. Uses
        basenames throughout (see basename_of's docstring) - this content
        is what actually ships to the worker, which sees a flat directory."""
        basenames = [self.basename_of[f] for f in self.source_files]
        files_block = " \\\n    ".join(basenames)
        depth_line = f"depth {depth}\n" if depth is not None else ""
        return (
            f"[options]\n"
            f"mode {mode}\n"
            f"{depth_line}"
            f"\n[engines]\n"
            f"{engine_line}\n"
            f"\n[script]\n"
            f"plugin -i slang\n"
            f"read_slang --top {self.top} \\\n"
            f"    {files_block}\n"
            f"\nprep -top {self.top}\n"
            f"\n[files]\n"
            + "\n".join(basenames) + "\n"
        )

    def sby_input_files(self, sby_name: str, sby_content: str) -> dict:
        """{basename: content} for every file a given sby run needs - the
        generated .sby content plus the current source files, KEYED BY
        BASENAME to match what the .sby content itself references - handed
        to run_sby as an explicit argument (see module docstring's
        FILESYSTEM section)."""
        contents = self.read(self.source_files)
        by_basename = {self.basename_of[f]: content for f, content in contents.items()}
        by_basename[sby_name] = sby_content
        return by_basename

    def persist_sby(self, sby_name: str, sby_content: str):
        """Writes a copy of the generated .sby into the case study's own
        driver-visible directory too, purely so a human can look at
        exactly what ran - run_sby never reads this copy, only the
        file_contents argument built by sby_input_files above."""
        self.apply([{"file": str(self.case_dir / sby_name), "find": "", "replace": sby_content}])

    # -- engine launches --

    def launch_bmc(self, depth: int) -> TrackedRef:
        sby_content = self.build_sby("bmc", depth, "smtbmc --keep-going bitwuzla")
        self.persist_sby(self.bmc_sby_name, sby_content)
        file_contents = self.sby_input_files(self.bmc_sby_name, sby_content)
        return TrackedRef(
            ref=run_sby.chia_remote(self.bmc_sby_name, self.bmc_timeout, file_contents),
            submit_fn=lambda: run_sby.chia_remote(self.bmc_sby_name, self.bmc_timeout, file_contents),
            label=f"bmc@depth={depth}")

    def launch_cover(self, depth: int) -> TrackedRef:
        sby_content = self.build_sby("cover", depth, "smtbmc --keep-going bitwuzla")
        self.persist_sby(self.cover_sby_name, sby_content)
        file_contents = self.sby_input_files(self.cover_sby_name, sby_content)
        return TrackedRef(
            ref=run_sby.chia_remote(self.cover_sby_name, self.cover_timeout, file_contents),
            submit_fn=lambda: run_sby.chia_remote(self.cover_sby_name, self.cover_timeout, file_contents),
            label=f"cover@depth={depth}")

    def launch_prove(self) -> TrackedRef:
        sby_content = self.build_sby("prove", None, "abc pdr")
        self.persist_sby(self.prove_sby_name, sby_content)
        file_contents = self.sby_input_files(self.prove_sby_name, sby_content)
        return TrackedRef(
            ref=run_sby.chia_remote(self.prove_sby_name, self.prove_timeout, file_contents),
            submit_fn=lambda: run_sby.chia_remote(self.prove_sby_name, self.prove_timeout, file_contents),
            label="prove")

    # -- LLM interaction --

    def propose_start_depth(self, llm) -> int:
        files_section = self.files_section_for(self.source_files)
        resp, usage = call_llm(llm, self.depth_prompt + "\n\n" + files_section)
        match = re.search(r"DEPTH:\s*(\d+)", resp.result)
        if match:
            depth = int(match.group(1))
            print(f"LLM-proposed starting depth: {depth} ({format_usage(usage)})")
        else:
            depth = self.bmc_start_depth
            print(f"WARNING: could not parse a DEPTH: line from the LLM's response; "
                  f"falling back to default starting depth {depth}")
            print("LLM raw response:")
            print(resp.result)
        return max(1, min(depth, self.bmc_max_depth))

    def diagnose_and_fix(self, llm, response, round_num, max_rounds):
        files_section = self.files_section_for(self.source_files)
        prompt = self.fix_prompt + "\n\n" + files_section + "\n\nsby output:\n" + response.stdout
        resp, usage = call_llm(llm, prompt)

        # Split on the "FIX:" marker, never the first "[" - RTL/property
        # source is full of bit-range syntax (`[15:12]`) that can appear
        # in the reasoning prose before the JSON array actually starts.
        fix_marker = resp.result.find("FIX:")
        if fix_marker != -1:
            reasoning, json_text = resp.result[:fix_marker].strip(), resp.result[fix_marker:]
        else:
            reasoning, json_text = resp.result.strip(), resp.result
            print("WARNING: no \"FIX:\" marker found in the response - it may "
                  "have been cut off before proposing a fix at all")
        reasoning = re.sub(r"^REASONING:\s*", "", reasoning)
        print(f"\nRound {round_num}/{max_rounds} reasoning report ({format_usage(usage)}):")
        print(reasoning or "(none provided)")

        replacements = extract_json_array(json_text)
        if replacements is None:
            print("WARNING: could not parse a JSON replacement list from the LLM's "
                  "response; files left unchanged")
            print("LLM raw response:")
            print(resp.result)
            return False
        return self.apply_edits_guarded(replacements)


def main():
    parser = argparse.ArgumentParser(
        description="Generic RTL-debug loop: golden formal property suite "
                    "checks a design; an LLM diagnoses and fixes real "
                    "counterexamples in the writable RTL file until every "
                    "requested engine is clean.")
    parser.add_argument("--source-files", nargs="+", required=True,
                         help="Every file read_slang needs, IN ORDER (packages "
                              "before things that import them, etc.), as paths "
                              "relative to --working-dir.")
    parser.add_argument("--writable", required=True,
                         help="The ONE file (must be one of --source-files) the "
                              "LLM is allowed to edit. Everything else is golden.")
    parser.add_argument("--top", required=True, help="Top-level module name.")
    parser.add_argument("--name", default=None,
                         help="Basename for generated .sby files (default: "
                              "derived from --writable's stem).")
    parser.add_argument("--working-dir", required=True,
                         help="Absolute path to THIS machine's real checkout of "
                              "the project (e.g. the same directory you ran "
                              "`chia job submit --working-dir .` from) - the "
                              "driver reads/writes this path directly. Deliberately "
                              "has no default: the driver's own OS working "
                              "directory is Ray's ephemeral per-job snapshot, "
                              "NOT this real path (confirmed directly - a file "
                              "written there never appears back on disk once the "
                              "job ends), so there is no safe value to fall back "
                              "to automatically. Get this wrong and every fix "
                              "this loop finds is silently lost the moment the "
                              "job exits.")
    parser.add_argument("--vertex-project", default=os.environ.get("VERTEX_PROJECT", DEFAULT_VERTEX_PROJECT),
                         help="GCP project for Vertex AI Gemini calls (default: "
                              "$VERTEX_PROJECT if set, else the project this was "
                              "developed against).")
    parser.add_argument("--engines", nargs="+", default=["bmc", "cover"],
                         choices=["bmc", "cover", "prove"],
                         help="Which engines to run concurrently. 'bmc' alone "
                              "is the simple single-engine loop; add 'cover' "
                              "so it can tell bmc how deep to go; add 'prove' "
                              "for an unbounded-proof attempt (PDR does not "
                              "converge quickly on non-trivial designs, so "
                              "expect it to time out and retry rather than "
                              "resolve fast - see module docstring).")
    parser.add_argument("--bmc-start-depth", type=int, default=None,
                         help="Default: ask the LLM to propose one.")
    parser.add_argument("--bmc-max-depth", type=int, default=60)
    parser.add_argument("--cover-start-depth", type=int, default=40)
    parser.add_argument("--cover-max-depth", type=int, default=80)
    parser.add_argument("--bmc-timeout", type=int, default=300)
    parser.add_argument("--cover-timeout", type=int, default=600)
    parser.add_argument("--prove-timeout", type=int, default=1800)
    parser.add_argument("--max-rounds", type=int, default=12)
    args = parser.parse_args()
    if args.name is None:
        args.name = Path(args.writable).stem
    user_specified_bmc_depth = args.bmc_start_depth is not None
    if args.bmc_start_depth is None:
        args.bmc_start_depth = 10  # placeholder; overwritten below by
                                    # propose_start_depth() unless the user
                                    # explicitly asked for a specific depth

    loop = FixRtlLoop(args)
    engines = loop.engines
    prove_enabled = "prove" in engines
    cover_enabled = "cover" in engines

    llm = VertexGeminiLLM(model="gemini-2.5-pro", project=args.vertex_project,
                           location="global", timeout_seconds=3600, max_tokens=65536)

    if "bmc" in engines and not user_specified_bmc_depth:
        bmc_depth = loop.propose_start_depth(llm)
        args.bmc_start_depth = bmc_depth  # so restarts-after-fix reuse this,
                                           # not a fresh LLM call every round
    else:
        bmc_depth = loop.bmc_start_depth
    cover_depth = loop.cover_start_depth
    min_required_bmc_depth = 0
    prove_timeouts_this_round = 0
    round_num = 0
    bug_log = []   # (round, source, failing_before)

    runs = {}
    if "bmc" in engines:
        runs["bmc"] = loop.launch_bmc(bmc_depth)
    if cover_enabled:
        runs["cover"] = loop.launch_cover(cover_depth)
    if prove_enabled:
        runs["prove"] = loop.launch_prove()
    print(f"Launched: {', '.join(tr.label for tr in runs.values())} "
          f"(engines={sorted(engines)})")

    def cancel_siblings(keep_name):
        for name, tr in list(runs.items()):
            if name == keep_name:
                continue
            print(f"Cancelling {tr.label} (a sibling found a real failure; "
                  "its result would be against RTL we're about to change).")
            chia_cancel(tr.ref)
            del runs[name]

    def restart_all():
        nonlocal bmc_depth, cover_depth, min_required_bmc_depth, prove_timeouts_this_round
        bmc_depth, cover_depth = args.bmc_start_depth, loop.cover_start_depth
        min_required_bmc_depth = 0
        prove_timeouts_this_round = 0
        if "bmc" in engines:
            runs["bmc"] = loop.launch_bmc(bmc_depth)
        if cover_enabled:
            runs["cover"] = loop.launch_cover(cover_depth)
        if prove_enabled:
            runs["prove"] = loop.launch_prove()

    def handle_failure(name, response):
        """A genuine counterexample from *name*, or a broken setup (prove's
        own timeout-driven ERROR has a separate meaning - see its own
        branch below). Cancels the other engines, diagnoses and fixes; if
        applied, restarts every requested engine fresh; otherwise
        relaunches just *name*. Returns True iff a fix was applied."""
        nonlocal round_num
        cancel_siblings(keep_name=name)
        round_num += 1
        failing_before = extract_failing_assertions(response.stdout)
        fixed = loop.diagnose_and_fix(llm, response, round_num, args.max_rounds)
        bug_log.append((round_num, name, failing_before))
        if fixed:
            print(f"Restarting all requested engines against the fixed RTL. "
                  f"Cumulative usage so far: {format_usage(RUN_USAGE_TOTALS)}")
            restart_all()
        else:
            print(f"No edit applied this round; relaunching {name} unchanged "
                  "(the other engines stay down until a fix actually lands).")
            if name == "bmc":
                runs["bmc"] = loop.launch_bmc(bmc_depth)
            elif name == "cover":
                runs["cover"] = loop.launch_cover(cover_depth)
            else:
                runs["prove"] = loop.launch_prove()
        return fixed

    proven = False
    while round_num < args.max_rounds:
        if not runs:
            # Nothing left running without a new fix - success if prove is
            # disabled (bmc-at-max + cover-exhausted is this run's actual
            # success state, bounded not unbounded confidence) or if prove
            # was never re-enabled after exhausting its retries.
            proven = not prove_enabled
            print("\nNothing left running without a new RTL fix"
                  + ("" if prove_enabled else " (prove disabled/exhausted - "
                     "bmc clean + cover exhausted is this run's success "
                     "state, bounded confidence).")
                  + " Stopping.")
            break

        ready, pending = chia_wait(list(runs.values()), num_returns=1,
                                    timeout=POLL_INTERVAL_SECONDS)
        if not ready:
            print(f"... still running: {', '.join(tr.label for tr in pending)} "
                  f"[{format_usage(RUN_USAGE_TOTALS)}]")
            continue

        finished = ready[0]
        name = next(k for k, tr in runs.items() if tr is finished)
        response = get(finished.ref)
        result = classify_sby_result(response)
        print(f"\n=== {finished.label} finished: {result.upper()} ===")

        if name == "prove":
            if result == "pass":
                print("PROVE SUCCEEDED - unbounded proof complete. This "
                      "subsumes bmc/cover; stopping everything.")
                cancel_siblings(keep_name="prove")
                proven = True
                break
            elif result == "fail":
                print("PROVE found a genuine UNBOUNDED counterexample - "
                      "diagnosing and fixing...")
                handle_failure("prove", response)
            else:
                prove_timeouts_this_round += 1
                if prove_timeouts_this_round <= PROVE_MAX_RETRIES:
                    print(f"PROVE inconclusive (timed out or errored) - retry "
                          f"{prove_timeouts_this_round}/{PROVE_MAX_RETRIES}.")
                    runs["prove"] = loop.launch_prove()
                else:
                    print(f"PROVE still inconclusive after {PROVE_MAX_RETRIES} "
                          "retries - leaving it be until the next RTL fix.")
                    del runs["prove"]
                    prove_enabled = False  # bmc/cover-only success now suffices

        elif name == "cover":
            if result != "pass":
                print(f"COVER {result.upper()} - a property genuinely failed "
                      "during cover-mode exploration - diagnosing and fixing...")
                handle_failure("cover", response)
            else:
                max_step, reached, unreached = extract_cover_reached_steps(response.stdout)
                print(f"Cover: {len(reached)} reached (deepest trigger at step "
                      f"{max_step}), {len(unreached)} unreached.")
                if unreached:
                    print(f"UNREACHED at depth {cover_depth}: {sorted(unreached)}")
                if max_step > min_required_bmc_depth:
                    min_required_bmc_depth = max_step
                    print(f"New cover-derived floor for bmc's depth: {min_required_bmc_depth}")
                if unreached and cover_depth < loop.cover_max_depth:
                    cover_depth = min(cover_depth * 2, loop.cover_max_depth)
                    print(f"Some cover points still unreached; escalating cover "
                          f"itself to depth {cover_depth}.")
                    runs["cover"] = loop.launch_cover(cover_depth)
                else:
                    print("Cover has nothing further to explore for now; not "
                          "relaunching until the next RTL fix.")
                    del runs["cover"]

        else:  # bmc
            if result == "pass":
                next_depth = max(bmc_depth * 2, min_required_bmc_depth)
                if next_depth <= bmc_depth or bmc_depth >= loop.bmc_max_depth:
                    print(f"BMC clean at depth {bmc_depth} with no higher "
                          "floor from cover and at/near the depth ceiling; "
                          "not escalating further for now.")
                    del runs["bmc"]
                else:
                    bmc_depth = min(next_depth, loop.bmc_max_depth)
                    print(f"BMC clean; escalating to depth {bmc_depth} "
                          f"(cover-derived floor: {min_required_bmc_depth}).")
                    runs["bmc"] = loop.launch_bmc(bmc_depth)
            else:
                print(f"BMC {result.upper()} at depth {bmc_depth} - "
                      "diagnosing and fixing...")
                handle_failure("bmc", response)

    print("\n" + "=" * 70)
    print("FIX-RTL LOOP - FINAL REPORT")
    print("=" * 70)
    for rnd, source, before in bug_log:
        print(f"round {rnd} (triggered by {source}): failing={before}")
    print(f"\nTotal LLM usage this run: {format_usage(RUN_USAGE_TOTALS)}")

    if not proven:
        print(f"\nDid not reach a clean state within {args.max_rounds} fix "
              f"round(s). Last known bmc depth: {bmc_depth}"
              + (f" (cover-derived floor: {min_required_bmc_depth})" if cover_enabled else "") + ".")
        return

    if "prove" in args.engines and prove_enabled:
        print(f"\nUNBOUNDED PROOF COMPLETE after {round_num} fix round(s).")
    else:
        print(f"\nClean at full target depth after {round_num} fix round(s) "
              "(bounded, not unbounded, confidence).")

    if cover_enabled:
        print("Running a final cover-mode non-vacuity pass to confirm nothing "
              "is left unreachable...")
        sby_content = loop.build_sby("cover", loop.cover_max_depth, "smtbmc --keep-going bitwuzla")
        loop.persist_sby(loop.final_cover_sby_name, sby_content)
        file_contents = loop.sby_input_files(loop.final_cover_sby_name, sby_content)
        cover_response = get(run_sby.chia_remote(loop.final_cover_sby_name, loop.cover_timeout, file_contents))
        print(cover_response.stdout)
        max_step, reached, unreached = extract_cover_reached_steps(cover_response.stdout)
        print(f"Cover: {len(reached)} reached, {len(unreached)} unreached.")
        if unreached:
            print(f"UNREACHED (possible vacuous property): {sorted(unreached)}")


if __name__ == "__main__":
    main()
