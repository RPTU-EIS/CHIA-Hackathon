from chia.base.ChiaFunction import ChiaFunction, get
from pathlib import Path
from typing import Optional
import subprocess
import json
import re
import os
import sys
import threading
import time
import argparse
from chia.models.antigravity import *
from chia.models.opencode import (OpenCodeLLM,
                                   MaxOutputTokensError as OpenCodeMaxOutputTokensError,
                                   RateLimitError as OpenCodeRateLimitError)
from chia.models.vertex import VertexGeminiLLM, MaxOutputTokensError, RateLimitError

MAX_FIX_ATTEMPTS = 15 # Give up after this many LLM attempts fixing the sby setup
MAX_DESIGN_FIX_ATTEMPTS = 15 # Give up after this many LLM attempts fixing a real design bug

# Falls back to this literal project if VERTEX_PROJECT isn't set - the GCP
# project this was originally developed against. Anyone else running this
# (e.g. a hackathon judge with their own GCP project/quota) should set
# VERTEX_PROJECT rather than edit this file; --vertex-project overrides
# both for a single invocation.
DEFAULT_VERTEX_PROJECT = "project-be5ca9cc-e88a-41a1-81f"

# Vertex AI gemini-2.5-pro pricing (https://ai.google.dev/gemini-api/docs/pricing,
# September 2026): $1.25/1M input tokens and $10.00/1M output tokens for prompts
# <=200K tokens - doubles to $2.50/$15.00 above that threshold. The prompts here
# (full design sources + sby stdout) stay under 200K for case-study-sized
# designs; update if a much larger design pushes past it, or if pricing changes.
# Set either to None to report raw token counts with no $ estimate.
USD_PER_1M_INPUT_TOKENS: Optional[float] = 1.25
USD_PER_1M_OUTPUT_TOKENS: Optional[float] = 10.00

# Vertex returns HTTP 429 / RESOURCE_EXHAUSTED for per-minute request and
# token quotas, which refill continuously - it is NOT an out-of-credit
# signal. VertexGeminiLLM.prompt deliberately puts RateLimitError on its
# never-retry list (see vertex.py), and nothing in the framework above it
# handles the error either, so an unlucky 429 otherwise costs a whole fix
# attempt: the empty response fails JSON parsing and the loop burns an
# iteration having changed nothing. Observed on a real SHA-1 run, where it
# cost one setup-fix attempt AND the closing summary. The error's own
# reset_time is set to now+60s, so a 60s first backoff matches the window
# these quotas actually roll over on.
RATE_LIMIT_RETRIES = 6
RATE_LIMIT_BACKOFF_BASE = 60
RATE_LIMIT_BACKOFF_MAX = 600

# Default design sources (overridable via --source-files). The LLM may
# instrument/rewrite these. Unlike a ported property set, there is no
# existing .sva file - the LLM designs the property from scratch, either
# creating a new .sva file or writing the check directly into the miter.
DEFAULT_SOURCE_FILES = ["sha512.v", "sha512_miter.sv"]


def _get_discover_dit_prompt_template():
    return "" \
"We're checking whether the current design has DATA-" \
"INDEPENDENT TIMING (DIT) - i.e. whether it is \"constant-time\". " \
"The " \
"verification question: does the design's completion timing (when it " \
"signals that it has finished processing) depend only on its control " \
"inputs, or can it also vary depending on the DATA values it processes " \
"? A hardware block whose completion timing " \
"varies with data content does not have the data-independent-timing " \
"property that constant-time implementations are designed to guarantee - " \
"this kind of check is a standard formal-verification technique for " \
"confirming or disproving that guarantee.\n\n" \
"There is no pre-existing property file for this design - unlike a typical " \
"port, you need to design the UPEC/DIT check yourself, not translate one. " \
"Steps:\n\n" \
"1. Look at *_miter.sv: it already instantiates two copies of " \
"the design SHARED between them are " \
"the CONTROL inputs) but data inputs are given separately per " \
"instance via ...1/...2 ports (these are the DATA inputs - free to differ " \
"between C1 and C2). This two-instance-miter structure is the standard " \
"way to state a DIT property formally: if two runs share every control " \
"input but are allowed arbitrary, independently-chosen data inputs, a " \
"constant-time design must still produce identical timing behavior.\n" \
"2. Identify which of the designs's OWN output ports (see its entity) " \
" is the one that reflects timing/completion - the signal " \
"that changes when, and only when, the design finishes an operation.\n" \
"3. Design and write a property asserting that this signal must be equal " \
"between C1 and C2 on every cycle (once out of reset), given they only " \
"ever receive the same control inputs. If sby finds a counterexample, the " \
"design's completion timing depends on the data values, not just the " \
"control inputs - i.e. it does not have the data-independent-timing " \
"property; if it proves the property, the design's timing is independent " \
"of the data inputs you let differ.\n\n" \
"This exact class of port (same underlying toolchain, same commercial-" \
"tool-to-sby migration) has been done by hand already for a related " \
"design, and hit several sharp edges specific to this toolchain (GHDL + " \
"open-source yosys + smtbmc, no `-verific` plugin). Apply these rules " \
"directly rather than rediscovering them by trial and error:\n\n" \
"1. `ghdl` is a yosys PLUGIN, not a built-in command - the .sby [script] " \
"must start with `plugin -i ghdl` before any `ghdl ...` line, or it fails " \
"with \"No such command: ghdl\".\n" \
"2. If the VHDL uses `IEEE.STD_LOGIC_ARITH`/`STD_LOGIC_UNSIGNED`/" \
"`STD_LOGIC_SIGNED` (legacy Synopsys packages), the ghdl command needs " \
"`-fsynopsys`, e.g. `ghdl --std=08 -fsynopsys <files> -e <top>`.\n" \
"3. GHDL fixes VHDL generics to concrete values AT ELABORATION TIME (the " \
"default, or an explicit `-g<GENERIC>=<value>` on the ghdl command line). " \
"The resulting Verilog-visible module has NO overridable parameter - do " \
"not write `SomeEntity #(.GENERIC(x)) inst (...)` against it; instantiate " \
"it with no parameter override at all.\n" \
"4. Every VHDL signal needs exactly one driver. If two processes assign " \
"the same signal under equivalent conditions (even with identical logic), " \
"GHDL's synth rejects it as \"multiple assignments\" - remove the " \
"redundant driver, keeping only one.\n" \
"5. Open-source yosys's native SVA support has NO concurrent-SVA " \
"operators at all: no `##`, `|->`, `|=>`, no `property`/`endproperty`, no " \
"`sequence`. Only a plain immediate `assert(expr)`/`assume(expr)`/" \
"`cover(expr)` inside an ordinary `always @(posedge clk)` block is " \
"understood. Write the property directly in this style - do not draft it " \
"as concurrent SVA and then translate it, since there is nothing to " \
"translate from here anyway.\n" \
"6. MOST IMPORTANT, and the one thing that will silently produce a " \
"MEANINGLESS result if skipped: DO NOT USE `bind` AT ALL, for anything, " \
"in this toolchain. Two separate, confirmed failure modes, both silent " \
"(no error, no warning - the check just never actually runs and sby " \
"reports a trivial PASS with zero real assertions checked, verified by " \
"grepping the compiled .smt2 model for \"assert\" and finding none):\n" \
"   a. A hierarchical dot-reference from Verilog into a GHDL-elaborated " \
"VHDL instance's INTERNAL (non-port) signal - e.g. `some_instance." \
"internal_signal` - silently reads a disconnected/meaningless value, " \
"whether written inline, via `` `include``, or via `bind`.\n" \
"   b. Separately and independently of (a): `bind`-ing a module across " \
"a SEPARATE `read_verilog` call from the one that read its target module " \
"- even when the bound module only references clean, explicit ports, no " \
"internal signals at all - also silently fails to attach; the bound " \
"module gets dropped as \"unused\" before binding happens.\n" \
"So: never reference an instance's internal signal via dot-notation from " \
"outside its own VHDL file (promote it to a real OUTPUT PORT on the VHDL " \
"entity instead if it isn't one already, and wire that port to an " \
"explicit top-level signal in the Verilog miter, e.g. `.ready(ready1)`, " \
"never leaving it as `.ready()`), AND never use `bind` at all - put the " \
"check directly inside the SAME module as the instantiations " \
" itself, or `` `include`` a separate file's content " \
"into that same module, read in the same `read_verilog` invocation as " \
"everything else).\n" \
"7. Any auxiliary register you add purely for verification (a shift " \
"register, a \"trigger fired\" flag, a delay register) needs an explicit " \
"initial value (`initial x = 0;` in the Verilog miter, or `signal x : " \
"type := '0';` in VHDL). Without one, BMC/k-induction is free to pick an " \
"arbitrary power-up value and can use that freedom to fabricate a " \
"violation that never involved any real triggering event.\n" \
"8. To restrict the property to reachable (from-reset) executions, force " \
"a genuine reset at the start of every trace with an explicit `assume`, " \
"not merely a gate on \"having observed rst=1 at some point\": " \
"`reg init; initial init = 1; always @(posedge clk) begin if (init) " \
"assume (rst); ... init <= 0; end`. Without the `assume`, BMC can " \
"explore a trace where rst is simply never asserted, starting every " \
"register at an arbitrary uninitialized value, and \"violate\" a " \
"property from a state real hardware could never physically be in.\n" \
"9. If the design is plain SystemVerilog (no VHDL/ghdl involved) and any " \
"of its files use `package ... ; ... endpackage` together with " \
"`import some_pkg::*;` (very common in real RTL, e.g. sharing a types " \
"package across files), DO NOT use `read_verilog -sv`: yosys's built-in " \
"Verilog frontend does not implement the `import` statement AT ALL - it " \
"fails with \"syntax error, unexpected TOK_IMPORT\" regardless of whether " \
"the import sits inside a package or inside a module, and separately " \
"rejects any package-qualified reference like `pkg::NAME` used outside a " \
"module body (\"implicitly declared outside of a module\"). This is not " \
"fixable by editing the source - adding or removing the `import` line " \
"only toggles between those two errors forever. The fix is to use the " \
"real SystemVerilog frontend instead: `plugin -i slang` then " \
"`read_slang file1.sv file2.sv ... --top <top_module>` in place of " \
"`read_verilog -sv ...; prep -top <top_module>` (read_slang does its own " \
"elaboration, so a separate `prep -top` is harmless but redundant - keep " \
"it if you like, it's a no-op once read_slang has already picked the top).\n" \
"10. Before committing to `mode bmc` with a large explicit `depth`, " \
"consider `mode prove` (BMC base case + k-induction) instead - especially " \
"for a design whose real completion latency could span many cycles " \
"(anything iterative, round-based, or driven by an internal counter/FSM). " \
"Plain BMC has to unroll the whole design that many cycles just to reach " \
"the interesting behavior, and the SMT solver's cost grows sharply with " \
"depth - for a wide or complex datapath this can make BMC too slow to " \
"converge in any practical time budget even when the property is true. " \
"`mode prove`'s k-induction instead tries to prove the asserted properties " \
"are themselves an inductive invariant (a short base-case check from " \
"reset, plus an induction step asking whether the property holding for a " \
"short window of arbitrary consecutive cycles implies it still holds one " \
"cycle later); when it succeeds, the cost is roughly independent of how " \
"many cycles the design might actually need. Try `mode prove` first for " \
"any design whose completion signal depends on an internal counter, " \
"round, or multi-cycle step sequence; fall back to explicit-depth " \
"`mode bmc` only if the property genuinely isn't provable this way.\n" \
"11. If `mode prove`'s BASE CASE passes but the INDUCTION step fails, " \
"that is usually NOT a real counterexample and NOT a broken setup - it " \
"means the property you asserted, though true, isn't by itself a strong " \
"enough inductive invariant for the solver to close without help. The " \
"general fix is to STRENGTHEN the induction hypothesis: look for other " \
"internal state elements of the design (registers that already exist " \
"internally, whether or not currently exposed as ports) whose equality " \
"between the two miter instances (a) necessarily holds whenever your " \
"target completion-timing property holds, and (b) is itself something " \
"the proof can carry forward cycle-to-cycle. Expose them as additional " \
"output ports if needed, and assert their equality alongside your " \
"primary property in the same induction step - this does not weaken or " \
"change the property you actually care about, it only gives the " \
"inductive proof enough intermediate structure to succeed. If you can't " \
"identify any such auxiliary invariant, the property may only be " \
"provable via explicit-depth BMC (rule 10's fallback).\n" \
"12. The `[engines]` line MUST be exactly `smtbmc`, with no solver name " \
"and no other engine. Do not write `abc pdr`, `aiger`, `btor`, " \
"`smtbmc z3`, `smtbmc boolector` or anything else: this loop deliberately " \
"starts every proof on plain smtbmc and escalates to a stronger engine " \
"ITSELF if k-induction turns out to be inconclusive, so choosing a " \
"different engine here does not help and only makes runs inconsistent " \
"with each other. Any other engine you write will be rewritten to " \
"`smtbmc` before the file is used.\n" \
"Plain `smtbmc` with no solver name lets sby use its own default solver, " \
"which is usually the fastest choice for the wide, arithmetic-heavy " \
"bit-vector formulas typical of hardware designs. A different, " \
"explicitly-named solver can be dramatically slower on this class of " \
"problem - even appearing to hang on a single BMC/induction query for a " \
"very long time with no result - while the exact same files solve in " \
"seconds under the default. If a run seems to be taking unreasonably " \
"long, check `[engines]` first and drop back to plain `smtbmc` before " \
"concluding the property itself is intractable, too weak, or wrong.\n\n" \
"Your job, across three steps:\n" \
"1. Fix it per rules 3/4/6 above (remove the parameter " \
"overrides, wire out the timing signal(s) you identified in step 2 " \
"above as real ports, fix any plain syntax issues you notice), and add " \
"the DIT check you've designed directly in that module.\n" \
"   Structure the check with these exact marker comments, which later " \
"iterations rely on:\n" \
f"     {DIT_CHECK_BEGIN}\n" \
"       assert(<the completion-timing signals are equal>);\n" \
f"     {DIT_CHECK_END}\n" \
f"     {AUX_BEGIN}\n" \
f"     {AUX_END}\n" \
"   The goal region holds ONLY the property you actually want to prove, " \
"and is immutable from then on. The aux region starts empty and is where " \
"any later supporting invariants (one assertion per line) are added or " \
"removed. Keep the goal region minimal - extra equalities belong in the " \
"aux region, never in the goal, because anything in the goal changes what " \
"a PASS actually means.\n" \
"2. Create {sby_file}, a valid SymbiYosys job file, that reads and " \
"elaborates the design (via `ghdl` for VHDL sources, or `read_slang` per " \
"rule 9 for plain SystemVerilog sources - use whichever matches the " \
"actual source files shown below) and checks " \
"the property (pick whichever mode - bmc/prove/cover - fits).\n" \
"3. We will run sby against it and give you the output on the next " \
"iteration if it fails, so you can keep fixing it.\n\n" \
"Respond with ONLY a JSON array of {{\"file\": ..., \"find\": ..., " \
"\"replace\": ...}} objects - no prose, no markdown, nothing else. Each " \
"entry edits one file: \"find\" must be an exact, unique, literal snippet " \
"copied verbatim from that file's current content (shown below), or an " \
"empty string to create the file (or fully replace its content) with " \
"\"replace\". Everything is applied as a literal string operation, so it " \
"must match exactly."


def build_discover_dit_prompt(sby_file, miter_file):
    template = _get_discover_dit_prompt_template()
    return template.format(sby_file=sby_file)


def _get_explain_and_fix_prompt_template():
    return "" \
"sby ran the current setup successfully but did not confirm the property " \
"holds. Two distinct situations lead here - check the sby output below " \
"to see which one you're in:\n\n" \
"- A GENUINE COUNTEREXAMPLE (`DONE (FAIL...)`, a failing BMC base-case " \
"step): the property definitely does NOT hold, for the SPECIFIC data " \
"values in the counterexample trace - the design's completion timing " \
"really does depend on the data. This is not a translation bug; the " \
"check is working correctly and found a real issue.\n" \
"- AN INCONCLUSIVE K-INDUCTION PROOF (`DONE (UNKNOWN...)`, base case " \
"passed but the induction step failed): sby found NO actual violation, " \
"but its induction step also couldn't PROVE the property holds. This is " \
"NOT evidence of a real design bug. Only conclude there is a genuine " \
"design bug if you have positive evidence of one (a concrete data-" \
"dependent branch or loop bound you can point to) - do not rewrite the " \
"design's datapath just because induction didn't close.\n\n" \
"  There are TWO distinct reasons induction fails, and they need OPPOSITE " \
"fixes. Read the named failing assertion(s) below before deciding which " \
"one you are in:\n" \
"  (a) THE INVARIANT SET IS TOO WEAK: the goal property is true but needs " \
"more supporting state equalities to carry across a cycle. Fix by ADDING " \
"an auxiliary invariant.\n" \
"  (b) AN AUXILIARY INVARIANT YOU ALREADY ADDED IS ITSELF THE PROBLEM - " \
"because it is not inductive, or because it is not even TRUE. Fix by " \
"REMOVING that assertion. Adding more on top of it can never help: one " \
"unprovable assertion keeps the whole induction step open forever.\n" \
"  Case (b) is at least as common as (a), and is the one this loop has " \
"historically handled badly - burning every remaining attempt adding " \
"invariants on top of a single bad one that sby was naming the whole " \
"time. If an assertion is named below as failing induction and it is an " \
"auxiliary invariant rather than the goal, REMOVING it is very likely the " \
"correct move. Two concrete red flags that an auxiliary invariant is " \
"false and must be removed rather than kept:\n" \
"    - It equates values on the DATA path (a hash result, a product, a " \
"ciphertext word, a datapath output). Those MUST differ between the two " \
"instances when the data differs - asserting them equal is asserting the " \
"design does no work. Only CONTROL state (counters, FSM state, round " \
"indices, busy/valid flags) is legitimately equal between instances.\n" \
"    - It equates a register that is only meaningful in part of the " \
"cycle, without guarding it by the condition that makes it meaningful.\n" \
"  Never re-propose an assertion that was already tried and named as " \
"failing - check the history below before proposing an edit.\n\n" \
"IMPORTANT POLICY for a genuine counterexample: do NOT try to change the " \
"design's actual computation to eliminate the timing dependency. Editing " \
"real hardware logic to force a property to pass risks silently breaking " \
"or degrading the design's real function - this loop has been burned by " \
"exactly that before (a \"fix\" that hardcoded away a data-dependent " \
"shortcut also corrupted the actual computed result). Instead:\n" \
"1. Identify the SPECIFIC data condition that triggers the timing " \
"divergence, as narrowly as you can (e.g. \"the denominator is exactly " \
"zero\", \"this opcode takes an early-exit path\", not a vague category).\n" \
"2. EXCLUDE just that condition from the miter's DATA inputs with a " \
"targeted `assume()` - e.g. `assume(i_denominator1 != 0); "\
"assume(i_denominator2 != 0);`. This narrows the property to \"data-" \
"independent timing EXCEPT for this specific, now-documented condition\" " \
"- itself a meaningful, honest formal-verification result (it " \
"characterizes exactly which inputs violate DIT) that never touches the " \
"actual hardware.\n" \
"3. Only ever edit {miter_file} for this - never the design-under-test " \
"source file(s). Any edit to a design-under-test file in response to a " \
"genuine counterexample will be rejected before it is even applied.\n" \
"4. The exclusion must be NARROW and target only the specific value(s) " \
"you identified from THIS counterexample. Never broadly force the two " \
"instances' data inputs to be equal or related to each other (e.g. " \
"`assume(op_a_i1 == op_a_i2)`) - that would trivially satisfy any " \
"property without proving anything real, and is not a valid finding. " \
"Exclude a VALUE (e.g. \"!= 0\"), never a RELATIONSHIP between the two " \
"instances' independent data inputs.\n" \
"5. sby will be re-run after your exclusion is applied. If it then finds " \
"a DIFFERENT counterexample, you'll be asked again - identify and " \
"exclude that new condition too, on top of the ones already excluded.\n\n" \
"Do two things, in this order:\n\n" \
"1. Write a short reasoning report in plain English (a few sentences): " \
"which of the two situations above is this, and why? For a genuine " \
"counterexample, walk through the root cause using the sby output (the " \
"failing assertion name, the step it failed at, the counterexample trace " \
"if shown), explain which data-dependent control-flow decision (a branch " \
"on the data, a data-dependent loop bound) causes the completion timing " \
"to vary, and state exactly what condition you are about to exclude and " \
"why it's narrow enough not to mask other real violations. For an " \
"inconclusive induction, identify what auxiliary invariant would " \
"strengthen the proof.\n" \
"2. Then propose a fix as a JSON array of {{\"file\": ..., \"find\": ..., " \
"\"replace\": ...}} edits - same format and rules as before (\"find\" " \
"must be an exact, unique, literal snippet copied verbatim from that " \
"file's current content shown below, or empty to create/overwrite a " \
f"file with \"replace\"). For a genuine counterexample this MUST be an " \
"`assume()`-based exclusion added to {miter_file} only (per the policy " \
"above); for an inconclusive induction this should be a fix to the MITER/" \
"PROPERTY (adding OR removing an auxiliary invariant, per (a)/(b) above), " \
"not the design's computation logic. The ONE exception: for an " \
"inconclusive induction you MAY add a pure OBSERVATION PORT to the " \
"design-under-test to expose an internal control register you need for an " \
"invariant (`output [6:0] round_o;` plus `assign round_o = round;`). That " \
"is allowed because it adds no logic and cannot change behavior. Never " \
"change an existing assignment, expression, or reset value while doing " \
"so - the design's computation must be bit-identical afterwards. If the " \
"invariant you need is over control state that is not yet a port, this is " \
"the correct fix, and it usually has to expose a GROUP of related control " \
"registers at once (a counter, the FSM state and the busy flag typically " \
"only close the induction together). If no narrow, honest exclusion or " \
"invariant exists (e.g. the timing dependency is pervasive rather than " \
"tied to a specific condition), say so in the reasoning report instead " \
"and return an empty JSON array [].\n\n" \
"STRUCTURE OF THE MITER - this is a hard contract, edits that violate it " \
"are rejected before they are applied:\n" \
f"- The region between `{DIT_CHECK_BEGIN}` and `{DIT_CHECK_END}` holds the DIT " \
"property actually being verified. It is IMMUTABLE. Never edit, weaken, " \
"guard, or delete anything inside it - that would change what is being " \
"proven and make a PASS meaningless.\n" \
f"- The region between `{AUX_BEGIN}` and `{AUX_END}` holds auxiliary " \
"invariants that exist only to help the induction close. This is the " \
"region you add to and remove from. Put every new helper assertion here, " \
"one per line, and remove a bad helper by deleting its line from here.\n" \
"- If these marker comments are not present in the miter yet, add them as " \
"part of your edit: put the completion-timing assertion inside the goal " \
"region and every supporting equality inside the aux region.\n\n" \
"Format your response exactly as:\n" \
"REASONING:\n<your reasoning report>\n\nFIX:\n<the JSON array, and " \
"nothing else after it>"


def build_explain_and_fix_prompt(sby_file, miter_file):
    template = _get_explain_and_fix_prompt_template()
    return template.format(sby_file=sby_file, miter_file=miter_file)


SUMMARY_PROMPT = "" \
"The automated data-independent-timing (DIT) verification run for this " \
"design has finished. Below is the step-by-step history of what was " \
"tried (setup-fix attempts, then design-fix attempts if a genuine " \
"counterexample was found), followed by the final sby status.\n\n" \
"Write a concise final summary in plain English - a few short " \
"paragraphs, no JSON, no markdown formatting - covering:\n" \
"1. What property was being checked and why.\n" \
"2. What happened at each meaningful step: what was tried, what worked " \
"or didn't, and why (in your own words, not just restating the log).\n" \
"3. The final outcome and what it means for this design - including any " \
"documented exclusions listed below. If exclusions exist and the final " \
"result is PASS, you MUST state plainly that this is a CONDITIONAL " \
"result (data-independent timing for every input EXCEPT the excluded " \
"condition(s)), never an unconditional \"this design is constant-time\" " \
"claim - and name what was excluded and why, in plain terms someone " \
"could act on (e.g. \"the divider is constant-time except when the " \
"denominator is exactly zero, which the caller must handle separately\")." \
"\n\n" \
"Write this summary NO MATTER THE RESULT - whether the run ended in " \
"PASS, FAIL, ERROR, or TIMEOUT. It should honestly reflect what actually " \
"happened, including if the loop never converged, if a fix attempt made " \
"things worse, or if the setup never got past basic errors. This is a " \
"closing report for someone who did not watch the run happen live, not " \
"a new fix attempt - do not propose further edits or JSON here."


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


@ChiaFunction(resources={"antigravity_creds": 0.01})
def prompt_streaming(llm: AntigravityLLM, user_message: str) -> QueryResult:
    """Like llm.prompt(user_message) with no tools, but streams agy's output
    live instead of staying silent until the whole run finishes.

    Unlike OpenCodeLLM, agy --print has no structured/streaming event format
    to parse (per chia.models.antigravity's own docstring) - it's a single
    plain-text blob on stdout, so there's no per-step JSON progress to
    surface here. Still worth it for two reasons: llm.prompt() runs agy via
    subprocess.run(capture_output=True), which buffers everything silently
    until the process exits, so streaming whatever agy does flush
    incrementally beats total silence; and more importantly,
    _run_streamed's watchdog timer gives an actually-enforced kill-after-
    timeout, rather than relying only on agy's own --print-timeout.

    Reuses AntigravityLLM's own command-building (_build_cmd) so the actual
    invocation matches llm.prompt(). Note: this merges stdout+stderr into
    one stream (unlike _run_antigravity, which keeps them separate) - a
    known simplification, fine here since we only ever read resp.result as
    plain text (fed to extract_json_array, which tolerates stray text).
    """
    cmd = llm._build_cmd(user_message)

    def on_line(line):
        line = line.rstrip("\n")
        if line:
            print(f"[antigravity] {line}")

    returncode, output, timed_out = _run_streamed(cmd, llm.timeout_seconds, on_line,
                                                    env=os.environ.copy())
    if timed_out:
        return QueryResult(result="", returncode=-1, stderr="antigravity run timed out",
                            stream_result=output, success=False)

    return QueryResult(result=output.strip(), returncode=returncode, stderr="",
                        stream_result=output, success=(returncode == 0))


MAX_FILTER_RETRIES = 3 # Retries specifically for a content-filter block, see prompt_with_retry

def prompt_with_retry(llm, prompt_text: str) -> QueryResult:
    """Call prompt_streaming, retrying if agy's content-safety filter blocks
    the request ("This request was blocked by Gemini's filters...").

    Confirmed by direct testing (feeding the exact same prompt text and file
    contents to a fresh agy call) that this block is transient, not caused
    by specific wording or file content - identical content that was
    blocked in one run passed cleanly in another. It's also not something
    any existing retry logic catches: chia.models.antigravity's own error
    classifier doesn't recognize "blocked by" as a failure signature at all
    (checked _HARD_FAILURE_SIGNATURES/_ERROR_PATTERNS), and agy exits 0 even
    when blocked, so this reads as a normal successful response both to
    AntigravityLLM.prompt()'s own retry loop and to prompt_streaming - it
    would otherwise just fail extract_json_array and burn a whole fix
    attempt on nothing. Retrying the identical prompt here is the practical
    fix until antigravity.py's classifier learns this signature upstream.
    """
    resp = None
    for attempt in range(1, MAX_FILTER_RETRIES + 1):
        resp = get(prompt_streaming.chia_remote(llm, prompt_text))
        if "blocked by" not in resp.result.lower():
            return resp
        print(f"LLM response was blocked by a content filter (attempt {attempt}/{MAX_FILTER_RETRIES}); retrying the same prompt")
    return resp


@ChiaFunction(resources={"vertex_creds": 0.01})
def call_llm_with_usage(llm: VertexGeminiLLM, user_message: str):
    """Returns (QueryResult, usage_dict).

    VertexGeminiLLM.prompt records token usage on self._last_metadata, but
    only on the copy of the LLM object inside whatever process actually ran
    the call; a plain llm.prompt.chia_remote(llm, ...) dispatch returns only
    the QueryResult and discards it. Calling the undecorated llm.prompt(...)
    from inside this already-remote function reads the metadata in the same
    process, right after the call, and returns it alongside the response
    instead of letting it be lost.

    The resources= tag matches VertexGeminiLLM.prompt's own decorator
    exactly - a bare @ChiaFunction() would let Ray schedule this on a node
    without the right environment/credentials for it.

    A 429 is retried here with exponential backoff (see RATE_LIMIT_RETRIES):
    it is a per-minute quota that refills, not a permanent failure, and
    VertexGeminiLLM.prompt re-raises it without retrying. Retrying inside
    this already-remote function means the wait happens on the worker,
    holding its vertex_creds slot, rather than blocking the driver.

    Any OTHER exception is left to propagate: call_llm converts it into an
    unsuccessful QueryResult, which keeps the loop's existing failure
    behaviour for everything except the two cases handled here.
    """
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            result = llm.prompt(user_message)
            return result, dict(getattr(llm, "_last_metadata", {}) or {})
        except MaxOutputTokensError as e:
            # Retrying cannot help: the same prompt will overrun the same
            # limit again. Hand back an empty result so this counts as a
            # no-op attempt rather than killing the job.
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


@ChiaFunction(resources={"opencode_creds": 0.01})
def call_opencode_with_usage(llm: OpenCodeLLM, user_message: str):
    """call_llm_with_usage's counterpart for the OpenCode backend, which
    records input_tokens/output_tokens on _last_metadata the same way and
    whose own prompt carries resources={"opencode_creds": 0.01}.

    Note OpenCode defines its OWN RateLimitError/MaxOutputTokensError - they
    are distinct classes from the Vertex ones, so the aliased imports above
    are what make this handler actually catch anything.
    """
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            result = llm.prompt(user_message)
            return result, dict(getattr(llm, "_last_metadata", {}) or {})
        except OpenCodeMaxOutputTokensError as e:
            print(f"WARNING: LLM response truncated at max_output_tokens ({e}); "
                  "treating this round as if no edits were returned")
            return (QueryResult(result="", returncode=-1, stderr=str(e),
                                 stream_result="", success=False), {})
        except OpenCodeRateLimitError as e:
            if attempt == RATE_LIMIT_RETRIES - 1:
                print(f"WARNING: still rate-limited after {RATE_LIMIT_RETRIES} "
                      f"attempts ({e}); treating this round as if no edits "
                      "were returned")
                return (QueryResult(result="", returncode=-1, stderr=str(e),
                                     stream_result="", success=False), {})
            backoff = min(RATE_LIMIT_BACKOFF_BASE * 2 ** attempt, RATE_LIMIT_BACKOFF_MAX)
            print(f"Rate-limited by OpenCode (attempt {attempt + 1}/{RATE_LIMIT_RETRIES}); "
                  f"backing off {backoff}s before retrying the same prompt")
            time.sleep(backoff)


# Cumulative LLM usage for the whole run, folded in by call_llm and reported
# once at the end next to the final verdict. num_untracked_calls counts calls
# through a backend that reports no token metadata (AntigravityLLM shells out
# to `agy --print` and gets plain text back) plus calls that raised, so the
# closing report can say the totals are a lower bound rather than implying
# those calls were free.
RUN_USAGE_TOTALS = {"input_tokens": 0, "output_tokens": 0,
                    "num_calls": 0, "num_untracked_calls": 0}


def format_usage(usage_totals: dict) -> str:
    """Formats either RUN_USAGE_TOTALS (has num_calls) or a single call's
    usage dict (only input_tokens/output_tokens/num_turns, straight from
    _last_metadata - no num_calls key) - .get(..., 0)/the "in" checks make
    this robust to both shapes. Note _last_metadata drops falsy entries, so
    a zero-token field can be absent entirely."""
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
    if usage_totals.get("num_untracked_calls"):
        line += (f" [+{usage_totals['num_untracked_calls']} call(s) with no usage "
                  "metadata - totals are a lower bound]")
    return line


def record_usage(usage: dict, tracked: bool = True) -> None:
    """Fold one call's usage into RUN_USAGE_TOTALS."""
    RUN_USAGE_TOTALS["num_calls"] += 1
    if tracked:
        RUN_USAGE_TOTALS["input_tokens"] += usage.get("input_tokens", 0) or 0
        RUN_USAGE_TOTALS["output_tokens"] += usage.get("output_tokens", 0) or 0
    else:
        RUN_USAGE_TOTALS["num_untracked_calls"] += 1


def call_llm(llm, prompt_text: str):
    """Dispatch a prompt to whichever backend `llm` is.

    AntigravityLLM gets the streaming wrapper plus its content-filter retry
    (see prompt_with_retry). OpenCodeLLM has none of that machinery (no
    known content-filter failure mode, and its own .prompt() already
    streams internally) - both classes share the same
    prompt(user_message, tools=None) -> QueryResult ChiaFunction shape, so
    calling it directly is enough.

    VertexGeminiLLM.prompt() raises a typed exception (MaxOutputTokensError,
    ContentBlockedError, RateLimitError, ...) instead of returning a failed
    QueryResult on unrecoverable errors - e.g. a large design's full-file
    JSON rewrite blowing past max_output_tokens even after its own one
    internal retry. Left uncaught, that exception propagates through
    get(...) as a RayTaskError and kills the whole multi-hour job, throwing
    away every attempt made so far - a much worse failure mode than a
    response that just fails to parse (which the caller already tolerates
    as a no-op attempt, see extract_json_array's callers). Converting it to
    an empty, unsuccessful QueryResult here lets this attempt be treated the
    same way: skipped with a warning, loop continues.

    Returns (QueryResult, usage_dict) - the usage dict is this call's token
    metadata, empty for a backend that reports none or for a failed call.
    Every call is also folded into RUN_USAGE_TOTALS for the closing report.
    """
    if isinstance(llm, AntigravityLLM):
        resp = prompt_with_retry(llm, prompt_text)
        # agy --print returns a plain-text blob with no usage metadata, so
        # this call is counted but contributes no tokens.
        record_usage({}, tracked=False)
        return resp, {}
    try:
        # Route through the usage-aware wrapper so the token counts survive
        # the remote hop (see call_llm_with_usage's docstring).
        if isinstance(llm, OpenCodeLLM):
            resp, usage = get(call_opencode_with_usage.chia_remote(llm, prompt_text))
        else:
            resp, usage = get(call_llm_with_usage.chia_remote(llm, prompt_text))
        record_usage(usage)
        return resp, usage
    except Exception as exc:
        print(f"WARNING: LLM call failed ({exc!r}); treating this attempt as a no-op")
        # Still counted: a failed call may have consumed input tokens, and
        # dropping it silently would understate the run's cost.
        record_usage({}, tracked=False)
        return (QueryResult(result="", returncode=-1, stderr=str(exc),
                             stream_result="", success=False), {})


SBY_TIMEOUT_SECONDS = 2400 # Kill a hung/intractable sby run after this long
SBY_TIMEOUT_MAX_SECONDS = 4800 # Never auto-raise the per-run budget past this

@ChiaFunction(resources={"sby": 1})
def run_sby(sby_file: str, timeout_seconds: int = SBY_TIMEOUT_SECONDS):
    """Run sby, printing its own progress output live instead of buffering
    it silently until the process exits. BMC/prove against an
    arithmetic-heavy design like RSA modular multiplication can legitimately
    run for a long time (or never converge), and capture_output=True gave no
    way to tell "still working" from "stuck" - same issue prompt_streaming
    fixes for the LLM call. Killed after `timeout_seconds` if it hasn't
    finished by then, rather than blocking the job forever. Takes the budget
    as a parameter (default SBY_TIMEOUT_SECONDS) rather than reading the
    global directly, so main()'s timeout-retry loop can raise it per call
    without touching this function."""
    cmd = ["/opt/oss-cad-suite/bin/sby", "-f", sby_file]
    returncode, output, timed_out = _run_streamed(cmd, timeout_seconds, lambda line: print(line, end=""))
    if timed_out:
        note = f"[TIMEOUT] sby did not finish within {timeout_seconds}s, killed\n"
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
    """Distinguish sby's three actual outcomes from its own "DONE (...)"
    status line, rather than treating any nonzero returncode as "needs
    another fix attempt".

    sby itself already distinguishes "the setup is broken" from "the setup
    is fine and found a real violation": it prints DONE (PASS, rc=0),
    DONE (FAIL, rc=2), or DONE (ERROR, rc=16) (e.g. a syntax error, a
    missing plugin, a file GHDL can't import). Collapsing FAIL and ERROR
    into one "not zero, keep looping" bucket would mean a genuine
    counterexample - the engine running correctly and proving the property
    does NOT hold - gets treated the same as a broken translation, spending
    the whole fix budget re-litigating a check that was never wrong.

    Returns "pass", "fail" (ran fine, found a real counterexample - this is
    a completed, meaningful result, not a failure to converge), "timeout"
    (sby was still running, making progress or not, when we killed it - NOT
    evidence of a setup or RTL bug, just BMC needing more depth/wall-clock
    than budgeted), or "error" (didn't get a clean run at all - a genuine
    syntax/elaboration failure, the one case actually worth asking the LLM
    to fix).

    Distinguishing "timeout" from "error" matters: a timeout was originally
    folded into "error" and handed to the LLM with the same "fix the setup"
    prompt as a real syntax error. With nothing actually broken to find, it
    fabricated an unrelated "fix" - observed rewriting SHA-512's compression
    round update (`A <= next_A + H0` -> `A <= A + H0`, etc., silently
    breaking the hash function) in response to a plain timeout. A timeout
    has no basis for an LLM-authored fix at all; see main()'s handling.

    Also returns "unknown" for `mode prove`'s inconclusive outcome (`DONE
    (UNKNOWN, rc=4)`: the BMC base case passed but the k-induction step
    couldn't close the proof - see rule 11 in the prompts above). This was
    originally also folded into "error" and, same failure mode as the
    timeout case, handed to the setup-fix loop's "something is broken, fix
    the setup" prompt - even though sby ran completely cleanly and nothing
    is actually broken. Observed consequence on a real run (a different
    design, same bug): the LLM "fixed" a plain inconclusive-induction
    result by rewriting the miter, introducing a genuine syntax error;
    main() now routes "unknown" to the design-fix loop instead (rule 11's
    territory: strengthen the induction invariant), not the setup-fix loop.
    """
    if "DONE (PASS" in response.stdout:
        return "pass"
    if "DONE (FAIL" in response.stdout:
        return "fail"
    if "DONE (UNKNOWN" in response.stdout:
        return "unknown"
    if "[TIMEOUT]" in response.stdout:
        return "timeout"
    return "error"


# --------------------------------------------------------------------------
# (1) Induction-failure feedback
#
# sby names the exact assertion that broke the induction step, with file and
# line number, e.g.
#   engine_0.induction: ##  0:00:00  Assert failed in sha512_miter:
#       sha512_miter.sv:76.7-76.29 (_witness_.check_assert_sha512_miter_sv_76_29)
# Before this, that line was handed to the LLM only as part of the raw
# multi-thousand-line sby stdout, buried among "Checking assertions in step N.."
# noise - so in practice the model never used it. Observed consequence on the
# SHA-512 run that motivated this: sby pointed at the SAME assertion
# (`Kt_o1 == Kt_o2`) in all six design-fix attempts, and the loop kept adding
# *more* invariants instead of removing the one being named.
# --------------------------------------------------------------------------

_ASSERT_FAILED_RE = re.compile(
    r"Assert failed in (?P<module>\w+): (?P<file>[\w./\-]+):(?P<line>\d+)\.\d+"
)


def parse_induction_failures(stdout: str) -> list:
    """Extract the assertions sby reported as failing the INDUCTION step.

    Returns a de-duplicated list of {"module", "file", "line"} dicts, in
    first-seen order. Only lines belonging to the induction engine are
    considered: a base-case "Assert failed" is a genuine counterexample (a
    different situation entirely, handled by the exclusion policy), whereas an
    induction-only failure means the invariant set isn't inductive.

    sby's stdout reaches us with real newlines when streamed, but the archived
    logs embed it as an escaped "\\n" blob inside a CompletedProcess repr, so
    normalize both before matching. Defensive by construction: any parsing
    miss degrades to an empty list and the caller falls back to the old
    raw-stdout behavior rather than crashing a multi-hour job.
    """
    if not stdout:
        return []
    text = stdout.replace("\\n", "\n")
    lines = text.splitlines()

    # Only the MOST RECENT sby invocation's failures are meaningful. Line
    # numbers are relative to the miter as it was during that run, and the
    # miter is rewritten between attempts - so a failure reported against an
    # earlier revision would resolve to a completely unrelated line in the
    # current file. (Observed directly on the SHA-512 log: stale hits landed
    # on `reg busy_o1_dly;` and on a comment.) Anchor to the last "Removing
    # directory"/"Copy" banner that starts a run, when one is present.
    last_start = 0
    for i, line in enumerate(lines):
        if "Removing directory" in line or "engine_0: " in line:
            last_start = i
    lines = lines[last_start:]

    seen, failures = set(), []
    for line in lines:
        if "induction" not in line or "Assert failed" not in line:
            continue
        m = _ASSERT_FAILED_RE.search(line)
        if not m:
            continue
        key = (m.group("file"), int(m.group("line")))
        if key in seen:
            continue
        seen.add(key)
        failures.append({"module": m.group("module"), "file": m.group("file"),
                          "line": int(m.group("line"))})
    return failures


def describe_induction_failures(stdout: str, file_contents: dict) -> str:
    """Render parse_induction_failures as a focused digest naming each failing
    assertion together with its actual source line, so the LLM sees
    "assert(Kt_o1 == Kt_o2); // <- this one" rather than having to find a
    line number in a wall of solver progress output.
    """
    failures = parse_induction_failures(stdout)
    if not failures:
        return ""
    out = []
    for f in failures:
        content = file_contents.get(f["file"])
        src = "(source unavailable)"
        if content:
            lines = content.splitlines()
            if 0 < f["line"] <= len(lines):
                candidate = lines[f["line"] - 1].strip()
                # Only quote the line if it really is the assertion sby meant.
                # If the miter was rewritten since that run, the number can
                # point at unrelated text, and quoting it would actively
                # mislead the model into "fixing" the wrong line.
                src = (candidate if "assert" in candidate
                        else f"(line {f['line']} is no longer an assertion in the "
                             f"current file - it now reads {candidate!r}; the "
                             f"miter changed since that run)")
        out.append(f"  - {f['file']}:{f['line']}  ->  {src}")
    return (
        "The k-induction step failed on the following assertion(s) - sby named "
        "these specifically, they are not a guess:\n" + "\n".join(out) + "\n\n"
        "Each assertion listed above is one the induction step could NOT carry "
        "from an arbitrary pre-state to the next cycle. An assertion appearing "
        "here is either (a) not inductive on its own, or (b) not actually true. "
        "It is very often an auxiliary invariant that was ADDED to help the "
        "proof and is itself the thing now blocking it.\n"
    )


# --------------------------------------------------------------------------
# (2) Separating the DIT goal from loop-managed auxiliary invariants
#
# The miter is split by marker comments into an immutable GOAL region (the
# property we actually care about) and an AUX region the loop owns. This buys
# two things: the goal can be protected deterministically - the same way
# out-of-file edits are already rejected - and, because the aux region is
# machine-identifiable, a failing induction can be repaired by BISECTING the
# aux set with no LLM call at all.
# --------------------------------------------------------------------------

# The marker TEXT is part of the on-disk contract - miters already carry it,
# and changing it would orphan them - so the strings stay fixed even though
# the constants are referred to by both spellings in the prompts.
DIT_CHECK_BEGIN = "// === DIT GOAL (do not modify) ==="
DIT_CHECK_END = "// === END DIT GOAL ==="
GOAL_BEGIN = DIT_CHECK_BEGIN
GOAL_END = DIT_CHECK_END
AUX_BEGIN = "// === AUX INVARIANTS (loop-managed) ==="
AUX_END = "// === END AUX INVARIANTS ==="


def _region(content: str, begin: str, end: str) -> Optional[str]:
    """Return the text between the *begin* and *end* markers, or None if the
    region isn't present/well-formed."""
    if not content:
        return None
    i = content.find(begin)
    if i == -1:
        return None
    j = content.find(end, i + len(begin))
    if j == -1:
        return None
    return content[i + len(begin):j]


def extract_goal(content: str) -> Optional[str]:
    return _region(content, GOAL_BEGIN, GOAL_END)


def extract_aux_lines(content: str) -> list:
    """The individual assertion lines currently in the aux region."""
    region = _region(content, AUX_BEGIN, AUX_END)
    if region is None:
        return []
    return [l for l in region.splitlines() if "assert" in l]


def goal_was_modified(old_content: str, new_content: str) -> bool:
    """True if an edit changed the protected goal region.

    Only meaningful once the markers exist; if either side lacks a well-formed
    goal region we report False and let the normal flow continue, rather than
    blocking edits on a miter that hasn't been marked up yet (e.g. during the
    setup phase, before the LLM has written the structure at all).
    """
    old_goal, new_goal = extract_goal(old_content), extract_goal(new_content)
    if old_goal is None or new_goal is None:
        return False
    return old_goal.strip() != new_goal.strip()


def set_aux_region(content: str, aux_lines: list) -> Optional[str]:
    """Replace the aux region's contents with exactly *aux_lines*. Returns None
    if the region isn't present, so callers can skip bisection rather than
    corrupting an unmarked miter."""
    region = _region(content, AUX_BEGIN, AUX_END)
    if region is None:
        return None
    body = "\n" + "\n".join(aux_lines) + "\n      " if aux_lines else "\n      "
    start = content.find(AUX_BEGIN) + len(AUX_BEGIN)
    end = content.find(AUX_END, start)
    return content[:start] + body + content[end:]


# --------------------------------------------------------------------------
# (3) Validating auxiliary invariants before trusting them
#
# A helper assertion that is FALSE in reachable states poisons the proof
# permanently: induction can never close, and every subsequent attempt is
# wasted. This is not hypothetical - the SHA-512 run that motivated these
# changes spent all six design-fix attempts stuck behind two such helpers
# (`Kt_o1 == Kt_o2`, non-inductive; and `!busy_o1 || (text_o1 == text_o2)`,
# outright false - text_o is the hash result, which MUST differ when the data
# differs). Checking a candidate helper under plain BMC catches the "false"
# case in seconds, deterministically, with no LLM judgment involved.
# --------------------------------------------------------------------------

AUX_CHECK_TIMEOUT_SECONDS = 600 # A validation/bisection probe is cheap; don't let one hang the loop
AUX_BISECT_MAX_RUNS = 12        # Cap total probe runs so a large aux set can't blow the budget


# --------------------------------------------------------------------------
# (4) Engine escalation before spending an LLM attempt
#
# `mode prove`'s k-induction needs the asserted set to be inductive ON ITS
# OWN: every supporting equality must be stated explicitly. PDR/IC3 (`abc
# pdr`) instead DERIVES its own strengthening, so it routinely proves
# properties that k-induction leaves UNKNOWN with no property changes at all.
#
# This matters for exactly the failure this loop keeps hitting. On SHA-512 the
# control state is a mutually-dependent cluster (`cmd[4] <= busy`, `busy` set
# from `round`, `round` gated by `read_counter`), so k-induction needs all
# four equalities stated together - and the run that motivated this stalled
# because the LLM exposed only `round` as a port and the other two were never
# reachable. PDR has to be given none of them.
#
# Ordered cheapest/most-likely-first. Each entry rewrites only the engine and
# mode lines of the .sby for one probe run; nothing about the property changes,
# so a PASS here is exactly as trustworthy as a PASS from the committed setup.
# --------------------------------------------------------------------------

ENGINE_LADDER = [
    # (label, mode, engine, timeout)
    ("PDR (abc pdr) - derives its own inductive strengthening",
     "prove", "abc pdr", 900),
    ("k-induction with a deeper window (depth 40)",
     "prove", None, 900),
]

ENGINE_LADDER_DEPTH = {1: 40} # ladder index -> explicit depth to set


def rewrite_sby(sby_content: str, mode: Optional[str] = None,
                 engine: Optional[str] = None, depth: Optional[int] = None) -> str:
    """Return *sby_content* with its mode/engine/depth lines rewritten.

    Handles both plain (`mode prove`) and task-prefixed (`dit_check: mode
    prove`) option styles, since the LLM writes either. The task prefix is
    preserved so the rewritten file stays valid for a [tasks]-based job.
    """
    out = sby_content
    if mode:
        out = re.sub(r"(?m)^(\s*(?:\w+:\s*)?mode\s+)\w+\s*$",
                      lambda m: m.group(1) + mode, out)
    if engine:
        # Replace the engine spec while keeping any "task:" prefix.
        out = re.sub(r"(?m)^(\s*(?:\w+:\s*)?)(smtbmc|abc|aiger|btor)\b.*$",
                      lambda m: m.group(1) + engine, out)
    if depth is not None:
        if re.search(r"(?m)^\s*(?:\w+:\s*)?depth\s+\d+\s*$", out):
            out = re.sub(r"(?m)^(\s*(?:\w+:\s*)?depth\s+)\d+\s*$",
                          lambda m: m.group(1) + str(depth), out)
        else:
            # Insert a depth line next to the mode line, copying its prefix so
            # a task-scoped file stays task-scoped.
            m = re.search(r"(?m)^(\s*)((?:\w+:\s*)?)mode\s+\w+\s*$", out)
            if m:
                out = out[:m.end()] + f"\n{m.group(1)}{m.group(2)}depth {depth}" + out[m.end():]
    return out


INITIAL_ENGINE = "smtbmc" # What every proof starts on; see pin_initial_engine


def pin_initial_engine(sby_content: str) -> Optional[str]:
    """Force the committed .sby onto INITIAL_ENGINE, or None if already there.

    The engine for the FIRST proof attempt is pinned deterministically rather
    than left to whatever the LLM happens to write, so that every run starts
    from the same solver configuration and the engine ladder below is the
    only thing that ever changes it. Rule 12 of the discover-DIT prompt asks
    for `smtbmc`; this enforces it, because a prompt is not a guarantee -
    observed directly on SHA-1, where two clean-slate runs of the identical
    prompt produced `smtbmc` while an earlier configuration used `abc pdr`,
    making the two runs incomparable.

    smtbmc (k-induction under `mode prove`) is the right starting point
    precisely BECAUSE it can return UNKNOWN: that verdict is what routes a
    run into the mechanical remedies, where PDR is then tried on an
    untouched property. Starting on `abc pdr` would mask that path entirely.

    A file with no engine line at all is left alone: sby's own default is
    already smtbmc, and synthesising an `[engines]` section risks breaking a
    task-scoped file.
    """
    pinned = rewrite_sby(sby_content, engine=INITIAL_ENGINE)
    return None if pinned == sby_content else pinned


def try_engine_ladder(sby_file, miter_file, miter_content, sby_content):
    """Try progressively different engines/modes on the UNCHANGED property.

    Returns (label, rewritten_sby) for the first configuration that PASSes, or
    (None, None) if none does. The property is never touched here - only how
    the solver is asked to discharge it - so this is always safe to try before
    any edit, and costs no LLM call.
    """
    for i, (label, mode, engine, timeout) in enumerate(ENGINE_LADDER):
        depth = ENGINE_LADDER_DEPTH.get(i)
        probe_sby = rewrite_sby(sby_content, mode=mode, engine=engine, depth=depth)
        if probe_sby == sby_content and engine is None and depth is None:
            continue # nothing actually changed; don't waste a run
        print(f"  [engine-ladder] trying {label}")
        resp = get(run_sby_variant.chia_remote(
            sby_file, miter_file, miter_content, timeout, None, probe_sby))
        verdict = classify_sby_result(resp)
        print(f"  [engine-ladder] -> {verdict}")
        if verdict == "pass":
            return label, probe_sby
        if verdict == "fail":
            # A real counterexample found by a stronger engine is a genuine
            # result, not a configuration question - stop laddering and let
            # the normal counterexample path handle it.
            print("  [engine-ladder] a stronger engine found a real "
                  "counterexample; stopping the ladder")
            return None, None
    return None, None


@ChiaFunction(resources={"sby": 1})
def run_sby_variant(sby_file: str, miter_file: str, miter_content: str,
                     timeout_seconds: int = AUX_CHECK_TIMEOUT_SECONDS,
                     mode_override: Optional[str] = None,
                     sby_override: Optional[str] = None):
    """Run sby against a temporary variant of the miter, then restore the
    original file.

    Used for the aux-invariant probes: validating a candidate helper under BMC
    and bisecting a failing aux set. Writes the variant, runs, and restores in
    a finally block so a crashed or timed-out probe can never leave the
    worker's miter in a modified state - the main loop's own edits and
    snapshots must see exactly the file they wrote.

    `mode_override` rewrites the `.sby` mode line for the probe only (BMC for
    truth-checking a helper); `sby_override` replaces the whole .sby for the
    probe (used by the engine ladder). Either way the committed .sby is
    restored afterwards.
    """
    miter_path, sby_path = Path(miter_file), Path(sby_file)
    original_miter = miter_path.read_text(encoding="utf-8") if miter_path.exists() else None
    original_sby = sby_path.read_text(encoding="utf-8") if sby_path.exists() else None
    try:
        miter_path.write_text(miter_content, encoding="utf-8")
        if sby_override:
            sby_path.write_text(sby_override, encoding="utf-8")
        elif mode_override and original_sby:
            sby_path.write_text(rewrite_sby(original_sby, mode=mode_override),
                                 encoding="utf-8")
        cmd = ["/opt/oss-cad-suite/bin/sby", "-f", sby_file]
        returncode, output, timed_out = _run_streamed(
            cmd, timeout_seconds, lambda line: print(line, end=""))
        if timed_out:
            note = f"[TIMEOUT] sby did not finish within {timeout_seconds}s, killed\n"
            output += note
            returncode = -1
        return subprocess.CompletedProcess(cmd, returncode, output, "")
    finally:
        if original_miter is not None:
            miter_path.write_text(original_miter, encoding="utf-8")
        if original_sby is not None:
            sby_path.write_text(original_sby, encoding="utf-8")


def validate_aux_invariants(sby_file, miter_file, miter_content, aux_lines):
    """Check each aux invariant for plain TRUTH (not inductiveness) under BMC.

    Each candidate is checked alone - goal region intact, all other aux lines
    removed - under `mode bmc`. A helper that BMC refutes is false in a
    genuinely reachable state, so it can never be part of any valid proof and
    is rejected outright. A helper that survives may still be non-inductive;
    that is bisection's job, not this function's.

    Returns (false_lines, unproven_lines): the first is the set to reject, the
    second is those whose probe didn't produce a clean verdict (timeout or
    setup error) and which are therefore left alone rather than discarded on
    inconclusive evidence.
    """
    false_lines, unproven = [], []
    for line in aux_lines[:AUX_BISECT_MAX_RUNS]:
        variant = set_aux_region(miter_content, [line])
        if variant is None:
            return [], list(aux_lines)
        resp = get(run_sby_variant.chia_remote(sby_file, miter_file, variant,
                                                AUX_CHECK_TIMEOUT_SECONDS, "bmc"))
        verdict = classify_sby_result(resp)
        if verdict == "fail":
            # BMC found a reachable state violating this helper: it is simply
            # untrue, regardless of how the induction step behaves.
            false_lines.append(line)
            print(f"  [aux-validate] REJECTED (false under BMC): {line.strip()}")
        elif verdict in ("pass", "unknown"):
            print(f"  [aux-validate] ok (no BMC counterexample): {line.strip()}")
        else:
            unproven.append(line)
            print(f"  [aux-validate] inconclusive ({verdict}), keeping: {line.strip()}")
    return false_lines, unproven


def bisect_aux_invariants(sby_file, miter_file, miter_content, aux_lines):
    """Find a subset of the aux invariants under which the proof closes.

    Greedy leave-one-out: drop each aux line in turn and re-run the real proof.
    If some drop yields PASS, that line was the blocker and we return the
    surviving set. Cheap when a proof takes seconds (the SHA-512 case closed in
    ~1s), and it needs no LLM call - which is the point: the loop previously
    burned a full LLM attempt per iteration to do worse than this, never
    removing anything.

    Returns (passing_aux_lines, culprit_line) or (None, None) if no single
    removal closes the proof.
    """
    for candidate in aux_lines[:AUX_BISECT_MAX_RUNS]:
        trial = [l for l in aux_lines if l != candidate]
        variant = set_aux_region(miter_content, trial)
        if variant is None:
            return None, None
        print(f"  [aux-bisect] trying without: {candidate.strip()}")
        resp = get(run_sby_variant.chia_remote(sby_file, miter_file, variant,
                                                AUX_CHECK_TIMEOUT_SECONDS))
        if classify_sby_result(resp) == "pass":
            print(f"  [aux-bisect] PROOF CLOSES without: {candidate.strip()}")
            return trial, candidate
    return None, None


# --------------------------------------------------------------------------
# Additive invariant search - the counterpart to bisection
#
# Bisection only ever REMOVES, so it is vacuous when the aux set is already
# small: with a single invariant the leave-one-out trial is the empty set, and
# "no single removal closes the proof" is the only possible answer. That is
# exactly what happened on the second SHA-512 run - the aux set was CLEAN but
# too WEAK (`round` alone; `read_counter` and `busy` were missing), and every
# mechanism in the loop was subtractive.
#
# So: propose additions mechanically too. Candidates are the DUT's own state
# registers, restricted to CONTROL state - a data register (the hash result,
# an accumulator, a message-schedule word) must NOT be asserted equal between
# the two instances, since those legitimately differ whenever the data differs.
# Asserting one equal is how the loop previously poisoned its own proof.
# --------------------------------------------------------------------------

# Registers whose equality is never a legitimate DIT invariant, because they
# hold DATA. Matched case-insensitively against the declared register name.
_DATA_NAME_HINTS = (
    "text", "data", "msg", "message", "digest", "hash", "result", "out",
    "key", "cipher", "plain", "product", "quotient", "remainder", "acc",
)

# Names that clearly denote CONTROL state - counters, FSM state, handshake
# flags. These are the registers whose equality a DIT proof legitimately needs.
_CONTROL_NAME_HINTS = (
    "round", "count", "cnt", "state", "busy", "valid", "ready", "done",
    "stage", "phase", "step", "idx", "index", "ptr", "fsm", "start", "run",
)

_REG_DECL_RE = re.compile(
    r"(?m)^\s*(?:reg|logic)\s*(?:\[(?P<msb>[^\]]+):(?P<lsb>[^\]]+)\])?\s*"
    r"(?P<names>[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*;"
)


def classify_register(name: str) -> str:
    """Classify a DUT register as "control", "data" or "unknown" by name.

    Deliberately a NAME heuristic, not a dataflow analysis: it only ever
    proposes candidates that are then checked by an actual sby run, so a
    misclassification costs one probe, never a wrong result. The asymmetry
    matters though - a data register wrongly proposed as control would be
    caught by BMC (it is false, so the probe refutes it), whereas the reverse
    just means a useful invariant is never tried. So we bias toward control
    only on an explicit control-ish name, and treat data hints as a hard veto.
    """
    low = name.lower()
    if any(h in low for h in _DATA_NAME_HINTS):
        return "data"
    if any(h in low for h in _CONTROL_NAME_HINTS):
        return "control"
    return "unknown"


def find_control_registers(dut_source: str, max_width: int = 16) -> list:
    """Candidate CONTROL registers in *dut_source*, widest-first exclusion of
    obvious datapath state.

    Width is a second, independent filter: a 64-bit register in a hash core is
    essentially always datapath (message schedule, working variables), while
    control counters and FSM state are narrow. Combined with the name veto this
    keeps `H0..H7`, `W0..W14`, `A..H`, `Wt`, `Kt` out of the candidate set on
    SHA-512, while keeping `round`, `read_counter` and `busy`.
    """
    candidates = []
    for m in _REG_DECL_RE.finditer(dut_source):
        width = 1
        msb, lsb = m.group("msb"), m.group("lsb")
        if msb is not None:
            try:
                width = abs(int(msb.strip()) - int(lsb.strip())) + 1
            except ValueError:
                width = max_width + 1 # unparsable (parameterized) - assume wide
        for raw in m.group("names").split(","):
            name = raw.strip()
            if not name:
                continue
            kind = classify_register(name)
            if kind == "data" or width > max_width:
                continue
            if kind == "control" or width <= 8:
                candidates.append({"name": name, "width": width, "kind": kind})
    # Control-named first, then narrowest - cheapest and most likely to help.
    candidates.sort(key=lambda c: (c["kind"] != "control", c["width"]))
    return candidates


def suggest_additional_invariants(miter_content: str, dut_source: str) -> list:
    """Control registers that are NOT yet asserted equal in the miter.

    Returns dicts describing each candidate. Whether a candidate is already
    reachable (exposed as a port and wired into the miter) decides if the loop
    can test it itself or has to ask the LLM to wire it out first.
    """
    aux_text = " ".join(extract_aux_lines(miter_content))
    goal_text = extract_goal(miter_content) or ""
    asserted = aux_text + " " + goal_text
    out = []
    for cand in find_control_registers(dut_source):
        name = cand["name"]
        if re.search(rf"\b{re.escape(name)}(_o)?[12]\b", asserted):
            continue # already covered by an existing assertion
        # Is it reachable from the miter - i.e. wired to per-instance signals?
        wired = bool(re.search(rf"\b{re.escape(name)}_o1\b", miter_content))
        out.append({**cand, "wired": wired})
    return out


def try_additional_invariants(sby_file, miter_file, miter_content, candidates):
    """Greedily ADD reachable candidate invariants until the proof closes.

    Only candidates already wired out as ports can be tried here - anything
    else needs a source edit to expose it, which is the LLM's job. Adds
    cumulatively: control invariants usually need each other (on SHA-512,
    `round`, `read_counter` and `busy` only close the induction together), so
    testing them one at a time in isolation would find nothing.

    Returns (aux_lines, added) on success, or (None, []) if no combination of
    the reachable candidates closes the proof.
    """
    reachable = [c for c in candidates if c["wired"]]
    if not reachable:
        return None, []
    aux = list(extract_aux_lines(miter_content))
    added = []
    for cand in reachable[:AUX_BISECT_MAX_RUNS]:
        line = f"      assert({cand['name']}_o1 == {cand['name']}_o2);"
        trial = aux + [line]
        variant = set_aux_region(miter_content, trial)
        if variant is None:
            return None, []
        print(f"  [aux-add] trying with: {line.strip()}")
        resp = get(run_sby_variant.chia_remote(sby_file, miter_file, variant,
                                                AUX_CHECK_TIMEOUT_SECONDS))
        verdict = classify_sby_result(resp)
        if verdict == "fail":
            # This equality is refuted: the register is data-dependent after
            # all, so the name heuristic misjudged it. Drop it and continue.
            print(f"  [aux-add] refuted (data-dependent), skipping: {cand['name']}")
            continue
        if verdict not in ("pass", "unknown"):
            # A setup error or timeout says nothing about this candidate; keep
            # the aux set as it was rather than accreting an untested line.
            print(f"  [aux-add] inconclusive ({verdict}), skipping: {cand['name']}")
            continue
        # Keep it and keep going: "unknown" here means the addition was at
        # least not refuted, and control invariants typically only close the
        # induction as a group.
        aux = trial
        added.append(line)
        if verdict == "pass":
            print(f"  [aux-add] PROOF CLOSES after adding {len(added)} invariant(s)")
            return aux, added
    return None, added


def describe_invariant_candidates(candidates) -> str:
    """Render unwired candidates as an instruction for the LLM.

    This is the part the loop cannot do for itself: reaching an internal
    register requires adding an output port to the DUT and wiring it in the
    miter. Naming the exact registers removes the guesswork that left the
    second SHA-512 run stuck with only `round` exposed.
    """
    unwired = [c for c in candidates if not c["wired"]]
    if not unwired:
        return ""
    listed = "\n".join(
        f"  - {c['name']} ({c['width']}-bit, looks like {c['kind']} state)"
        for c in unwired)
    return (
        "MECHANICALLY IDENTIFIED CANDIDATE INVARIANTS.\n"
        "These registers exist in the design-under-test, look like CONTROL "
        "state (not datapath), and are NOT yet asserted equal between the two "
        "instances. They are not currently reachable from the miter because "
        "they are internal registers rather than output ports:\n"
        + listed + "\n\n"
        "The induction is failing because the invariant set is too WEAK, and "
        "these are the most likely missing pieces. To use one: add an output "
        "port to the design-under-test that exposes the register (a pure "
        "observation port - it must not change any existing logic), wire it in "
        "the miter to per-instance signals named `<reg>_o1`/`<reg>_o2`, and "
        "assert their equality in the AUX region. Adding observation ports to "
        "the design file IS allowed and expected for this purpose - it is the "
        "one kind of design edit this situation calls for, because it adds no "
        "logic and cannot change behavior. Control state usually has to be "
        "exposed as a GROUP: these registers constrain each other, so adding "
        "just one often still will not close the induction.\n"
    )


@ChiaFunction(resources={"sby": 1})
def snapshot_files(filenames: list) -> dict:
    """Read each of `filenames` from the worker and return their content as
    a dictionary. The caller (driver) is responsible for writing these to disk.
    This allows the driver to handle file I/O in its own environment."""
    result = {}
    for name in filenames:
        path = Path(name)
        result[name] = path.read_text(encoding="utf-8") if path.exists() else None
    return result


def write_snapshot_to_disk(filenames, output_dir, sby_file=None, miter_file=None, iteration=None):
    """Call snapshot_files on the worker and write returned files to output_dir.
    .sby and miter files are also saved to a timestamped 'generated_source' subfolder
    to trace the iterative refinement process."""
    snapshot = get(snapshot_files.chia_remote(filenames))
    generated_dir = os.path.join(output_dir, "generated_source")
    os.makedirs(generated_dir, exist_ok=True)

    import time
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    for name, content in snapshot.items():
        if content is None:
            continue
        # Write to main output_dir
        filepath = os.path.join(output_dir, name)
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        # Save .sby files to generated_source with timestamp
        if name.endswith(".sby"):
            sby_name = f"{name[:-4]}_{timestamp}.sby"
            sby_path = os.path.join(generated_dir, sby_name)
            with open(sby_path, "w", encoding="utf-8") as f:
                f.write(content)

        # Save miter files to generated_source with timestamp and iteration
        if miter_file and name == miter_file:
            if iteration is not None:
                miter_name = f"{name[:-3]}_{iteration}_{timestamp}.sv"
            else:
                miter_name = f"{name[:-3]}_{timestamp}.sv"
            miter_path = os.path.join(generated_dir, miter_name)
            with open(miter_path, "w", encoding="utf-8") as f:
                f.write(content)

    print(f"Wrote worker files to {output_dir}")
    print(f"Wrote generated .sby and miter files to {generated_dir}")


_NUMBERED_EQ_RE = re.compile(r"\b([A-Za-z_]\w*)1\b\s*==\s*\b([A-Za-z_]\w*)2\b")


def find_broad_data_exclusion(new_lines):
    """Best-effort check that a genuine-counterexample "exclusion" actually
    excludes a VALUE, not a RELATIONSHIP between the two miter instances'
    data inputs.

    A narrow, honest exclusion looks like `assume(i_denominator1 != 0)`. A
    degenerate one that would trivially satisfy ANY property without
    proving anything real looks like `assume(op_a_i1 == op_a_i2)` - forcing
    the two instances' otherwise-independent data inputs to match removes
    the whole point of the miter. Flags the first `assume` line among
    `new_lines` (the lines added by the latest edit) that equates two
    identifiers differing only by a trailing "1"/"2" - e.g. `op_a_i1` and
    `op_a_i2`. Heuristic, not a hard block: matches on text pattern only,
    so it can both miss a differently-spelled version of the same trick and
    (rarely) flag a legitimate narrow comparison that happens to fit the
    shape - always surfaced for human review, never auto-rejected.
    """
    for line in new_lines:
        if "assume" not in line:
            continue
        for m in _NUMBERED_EQ_RE.finditer(line):
            if m.group(1) == m.group(2):
                return line.strip()
    return None


def build_llm(backend: str, vertex_project: str):
    if backend == "opencode":
        return OpenCodeLLM(model="opencode/big-pickle", timeout_seconds=3600)
    return VertexGeminiLLM(model="gemini-2.5-pro", project=vertex_project, location="global",
                           timeout_seconds=3600, max_tokens=65536)


def main():
    # Write output to /tmp/ so files persist and are easily accessible
    output_dir = "/tmp/chia-dit-output"
    os.makedirs(output_dir, exist_ok=True)

    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", choices=["opencode", "vertex"], default="vertex",
                         help="Which LLM backend to use (default: vertex)")
    parser.add_argument("--source-files", nargs="+", default=DEFAULT_SOURCE_FILES,
                         help="Design source files to verify (default: sha512.v sha512_miter.sv)")
    parser.add_argument("--vertex-project", default=os.environ.get("VERTEX_PROJECT", DEFAULT_VERTEX_PROJECT),
                         help="GCP project for Vertex AI Gemini calls, used only when --llm=vertex "
                              "(default: $VERTEX_PROJECT if set, else the project this was "
                              "developed against).")
    args = parser.parse_args()
    source_files = args.source_files
    llm = build_llm(args.llm, args.vertex_project)

    # Derive miter file and sby filename from source files
    # Find the miter file (ends with _miter.sv) and use its base name for SBY_FILE
    miter_files = [f for f in source_files if f.endswith("_miter.sv")]
    if not miter_files:
        print(f"ERROR: No miter file (*_miter.sv) found in source files: {source_files}")
        sys.exit(1)
    miter_file = miter_files[0]
    base_name = miter_file.replace("_miter.sv", "")
    sby_file = f"{base_name}.sby"

    # Plain-English record of what happened at each step, regardless of
    # phase (setup-fix vs design-fix) or outcome. Fed to the LLM at the very
    # end (see SUMMARY_PROMPT) to produce a human-readable closing report -
    # unlike the print()s scattered through the loop, this is what actually
    # survives to summarize the run for someone who wasn't watching it live.
    history = []

    # Documented exclusions accumulated across design-fix attempts (each a
    # newly-added `assume()` line narrowing the property away from a
    # specific counterexample condition, e.g. "denominator != 0") - kept
    # separate from `history` so the final deterministic report and
    # SUMMARY_PROMPT can call out precisely what was excluded, rather than
    # letting a final PASS silently imply unconditional DIT.
    exclusions = []

    # Loop: run sby against SBY_FILE (which doesn't exist yet on attempt 1,
    # so the first run just fails with an ERROR and kicks off the loop), and
    # ask the LLM to design the DIT property and (re)write the .sby file
    # when it does, applying its proposed edits ourselves. Stops on:
    #   - PASS: the property holds (the design is data-independent-timing
    #     with respect to the inputs let differ).
    #   - FAIL: sby ran cleanly and found a genuine counterexample - this is
    #     a completed, meaningful result (a real timing side-channel), NOT a
    #     sign the translation is broken, so it must not be treated as
    #     "keep asking the LLM to fix it" the same way an ERROR is.
    #   - ERROR after MAX_FIX_ATTEMPTS: still broken, giving up.
    sby_timeout = SBY_TIMEOUT_SECONDS

    # A .sby left behind by a previous run would otherwise dictate the engine
    # for this one - the case that made two SHA-1 runs incomparable, one
    # inheriting `abc pdr` and proving on the first call, the other authoring
    # `smtbmc` and going through the ladder. Pin it before the first run so
    # every run starts identically, whether or not the file already existed.
    existing_sby = get(read_files.chia_remote([sby_file]))[sby_file]
    if existing_sby:
        pinned = pin_initial_engine(existing_sby)
        if pinned is not None:
            get(apply_replacements.chia_remote(
                [{"file": sby_file, "find": "", "replace": pinned}]))
            print(f"Pinned the pre-existing {sby_file} to '{INITIAL_ENGINE}' "
                  "so this run starts from the same configuration as any other")

    response = get(run_sby.chia_remote(sby_file, sby_timeout))
    print("Response from run_sby:")
    print(response)
    result = classify_sby_result(response)
    history.append(f"Initial sby run (before any fix attempts): result={result}.")

    attempt = 0
    while result in ("error", "timeout") and attempt < MAX_FIX_ATTEMPTS:
        attempt += 1

        if result == "timeout":
            # Nothing for the LLM to diagnose here - sby was still running,
            # not stuck on a bad file. Just give it more wall-clock and
            # retry the exact same setup, no prompt/edit involved.
            sby_timeout = min(sby_timeout * 2, SBY_TIMEOUT_MAX_SECONDS)
            print(f"sby timed out (attempt {attempt}/{MAX_FIX_ATTEMPTS}); "
                  f"retrying with timeout raised to {sby_timeout}s (no LLM call)")
            response = get(run_sby.chia_remote(sby_file, sby_timeout))
            print("Response from run_sby:")
            print(response)
            result = classify_sby_result(response)
            history.append(f"Setup-fix attempt {attempt}: sby timed out; no LLM "
                            f"call made, just retried with the timeout raised to "
                            f"{sby_timeout}s. New result: {result}.")
            write_snapshot_to_disk(source_files + [sby_file], output_dir, sby_file=sby_file, miter_file=miter_file, iteration=f"setup_{attempt}")
            continue

        print(f"sby setup broken (attempt {attempt}/{MAX_FIX_ATTEMPTS}); asking the LLM to design/fix the property and setup")

        current_files = get(read_files.chia_remote(source_files + [sby_file]))
        files_section = "\n\n".join(
            f"=== {name} ===\n{content if content is not None else '(does not exist yet)'}"
            for name, content in current_files.items()
        )

        prompt = (
            build_discover_dit_prompt(sby_file, miter_file)
            + "\n\n" + files_section
            + "\n\nsby stdout:\n" + response.stdout
            + "\n\nsby stderr:\n" + response.stderr
        )

        resp, usage = call_llm(llm, prompt)
        print(f"LLM final response ({format_usage(usage) if usage else 'no usage reported'}):")
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

        # Pin the engine before running: the first proof attempt always
        # starts on INITIAL_ENGINE regardless of what the LLM wrote, so the
        # engine ladder stays the only thing that ever changes it.
        committed_sby = get(read_files.chia_remote([sby_file]))[sby_file]
        if committed_sby:
            pinned = pin_initial_engine(committed_sby)
            if pinned is not None:
                get(apply_replacements.chia_remote(
                    [{"file": sby_file, "find": "", "replace": pinned}]))
                print(f"Pinned the initial solver configuration to "
                      f"'{INITIAL_ENGINE}' (the LLM had chosen a different engine)")
                fix_desc += f"; engine pinned to {INITIAL_ENGINE}"

        response = get(run_sby.chia_remote(sby_file, sby_timeout))
        print("Response from run_sby:")
        print(response)
        result = classify_sby_result(response)
        history.append(f"Setup-fix attempt {attempt}: sby reported an error. "
                        f"LLM response (excerpt): {resp.result.strip()[:400]!r}. "
                        f"{fix_desc}. New result: {result}.")

        # Snapshot every touched file after each attempt, so progress is
        # visible even if we never converge within MAX_FIX_ATTEMPTS. Note
        # SOURCE_FILES/[SBY_FILE] doesn't cover a new .sva file the LLM
        # might have created - read_files/snapshot only sees files it's
        # told about, so a self-created .sva won't be written back here
        # unless it also gets folded into rsacypher_miter.sv via `include`.
        write_snapshot_to_disk(source_files + [sby_file], output_dir, sby_file=sby_file, miter_file=miter_file, iteration=f"setup_{attempt}")

    if attempt == 0:
        # The initial run_sby (before the loop) already returned "pass" or
        # "fail" with no attempts needed (e.g. SBY_FILE already existed from
        # a previous run) - the loop body's snapshot above never ran.
        write_snapshot_to_disk(source_files + [sby_file], output_dir, sby_file=sby_file, miter_file=miter_file, iteration="initial")

    # Second, separate loop: a genuine counterexample means the setup is
    # fine and there's a real bug in the design itself. Ask the LLM to
    # explain the root cause (printed as a reasoning report - useful on its
    # own even if no fix is found) and then propose an RTL fix, applying it
    # the same deterministic way as the setup-fix loop. Bounded by its own
    # budget (MAX_DESIGN_FIX_ATTEMPTS), separate from the setup-fix one, and
    # stops the moment the result stops being "fail" (pass = fixed).
    #
    # last_good_files tracks the most recent state that sby could actually
    # parse and run ("fail" is a completed, meaningful result - the setup is
    # fine, it just found a real counterexample). If a design-fix edit turns
    # that into "error" - the patch itself is broken, not a real design
    # question - it gets reverted immediately below rather than left in
    # place: the run must never end with unparseable, broken files on disk
    # just because the last fix attempt happened to be bad.
    last_good_files = get(read_files.chia_remote(source_files + [sby_file]))

    # Auxiliary invariants that have been tried and shown to block the proof
    # (rejected as false under BMC, or identified by bisection as the reason
    # induction won't close). Fed back to the LLM so it cannot re-propose one
    # it has already been told is bad - the SHA-512 run that motivated this
    # oscillated between two variants of the same bad assertion for four
    # consecutive attempts.
    rejected_aux = []

    design_attempt = 0
    while result in ("fail", "unknown") and design_attempt < MAX_DESIGN_FIX_ATTEMPTS:
        design_attempt += 1
        # Captured now, before `result` gets reassigned below - decides
        # whether this attempt must stick to the miter-only exclusion
        # policy (a genuine counterexample) or may touch the design-under-
        # test to add an auxiliary invariant (inconclusive induction).
        was_exclusion_attempt = (result == "fail")
        reason = "genuine counterexample" if was_exclusion_attempt else "inconclusive k-induction proof"

        current_files = get(read_files.chia_remote(source_files + [sby_file]))
        pre_edit_miter = current_files.get(miter_file) or ""

        # -- Deterministic repair, before spending an LLM attempt -----------
        # An inconclusive induction is very often caused by an auxiliary
        # invariant that is itself false or non-inductive. Both of those are
        # findable mechanically: BMC refutes a false helper outright, and
        # leave-one-out bisection finds a non-inductive one by just removing
        # it and seeing the proof close. Doing this first means the common
        # case costs zero LLM calls and cannot hallucinate a "fix".
        aux_lines = extract_aux_lines(pre_edit_miter)
        invariant_candidates = [] # populated by the additive search below
        if result == "unknown":
            print(f"Induction inconclusive with {len(aux_lines)} auxiliary "
                  f"invariant(s); trying mechanical remedies before asking the LLM")

            # (4) Cheapest first: keep the property exactly as it is and ask a
            # stronger engine. PDR derives its own strengthening, so it often
            # closes a proof that k-induction leaves open with no edit at all.
            label, better_sby = try_engine_ladder(
                sby_file, miter_file, pre_edit_miter, current_files.get(sby_file) or "")
            if better_sby is not None:
                get(apply_replacements.chia_remote(
                    [{"file": sby_file, "find": "", "replace": better_sby}]))
                response = get(run_sby.chia_remote(sby_file, sby_timeout))
                result = classify_sby_result(response)
                history.append(
                    f"Design-fix attempt {design_attempt}: the property was "
                    f"left unchanged and the solver configuration escalated to "
                    f"{label}, which closed the proof. No LLM call, no property "
                    f"edit. New result: {result}.")
                write_snapshot_to_disk(source_files + [sby_file], output_dir,
                                        sby_file=sby_file, miter_file=miter_file,
                                        iteration=f"design_{design_attempt}_engine")
                continue

            false_lines, _ = validate_aux_invariants(
                sby_file, miter_file, pre_edit_miter, aux_lines) if aux_lines else ([], [])
            if false_lines:
                kept = [l for l in aux_lines if l not in false_lines]
                repaired = set_aux_region(pre_edit_miter, kept)
                if repaired is not None:
                    get(apply_replacements.chia_remote(
                        [{"file": miter_file, "find": "", "replace": repaired}]))
                    rejected_aux.extend(l.strip() for l in false_lines)
                    response = get(run_sby.chia_remote(sby_file, sby_timeout))
                    result = classify_sby_result(response)
                    history.append(
                        f"Design-fix attempt {design_attempt}: removed "
                        f"{len(false_lines)} auxiliary invariant(s) refuted by BMC "
                        f"(false in a reachable state, so they could never be part "
                        f"of a valid proof): {[l.strip() for l in false_lines]!r}. "
                        f"No LLM call. New result: {result}.")
                    write_snapshot_to_disk(source_files + [sby_file], output_dir,
                                            sby_file=sby_file, miter_file=miter_file,
                                            iteration=f"design_{design_attempt}_auxreject")
                    continue

            surviving, culprit = bisect_aux_invariants(
                sby_file, miter_file, pre_edit_miter, aux_lines) if len(aux_lines) > 1 \
                else (None, None)
            if surviving is not None:
                repaired = set_aux_region(pre_edit_miter, surviving)
                get(apply_replacements.chia_remote(
                    [{"file": miter_file, "find": "", "replace": repaired}]))
                rejected_aux.append(culprit.strip())
                response = get(run_sby.chia_remote(sby_file, sby_timeout))
                result = classify_sby_result(response)
                history.append(
                    f"Design-fix attempt {design_attempt}: bisection found that "
                    f"auxiliary invariant {culprit.strip()!r} was blocking the "
                    f"induction; removing it closed the proof. No LLM call. "
                    f"New result: {result}.")
                write_snapshot_to_disk(source_files + [sby_file], output_dir,
                                        sby_file=sby_file, miter_file=miter_file,
                                        iteration=f"design_{design_attempt}_auxbisect")
                continue

            # Nothing to remove helped. The set may instead be too WEAK, so
            # try ADDING control-state equalities. Candidates come from the
            # DUT's own register declarations, filtered to control-looking
            # state - a data register asserted equal would be false and would
            # poison the proof exactly the way earlier runs did.
            dut_sources = "\n".join(
                content for name, content in current_files.items()
                if name not in (miter_file, sby_file) and content)
            invariant_candidates = suggest_additional_invariants(
                pre_edit_miter, dut_sources)
            if invariant_candidates:
                print(f"  [aux-add] {len(invariant_candidates)} candidate control "
                      f"register(s) not yet asserted equal: "
                      f"{[c['name'] for c in invariant_candidates]}")
            grown, added = try_additional_invariants(
                sby_file, miter_file, pre_edit_miter, invariant_candidates)
            if grown is not None:
                repaired = set_aux_region(pre_edit_miter, grown)
                get(apply_replacements.chia_remote(
                    [{"file": miter_file, "find": "", "replace": repaired}]))
                response = get(run_sby.chia_remote(sby_file, sby_timeout))
                result = classify_sby_result(response)
                history.append(
                    f"Design-fix attempt {design_attempt}: the invariant set was "
                    f"too weak, not wrong; adding {len(added)} control-state "
                    f"equality/equalities {[l.strip() for l in added]!r} closed "
                    f"the proof. No LLM call. New result: {result}.")
                write_snapshot_to_disk(source_files + [sby_file], output_dir,
                                        sby_file=sby_file, miter_file=miter_file,
                                        iteration=f"design_{design_attempt}_auxadd")
                continue

            print("  [aux] no mechanical remedy closed the proof; asking the LLM")

        print(f"sby ran cleanly but did not confirm the property ({reason}; "
              f"design attempt {design_attempt}/{MAX_DESIGN_FIX_ATTEMPTS}); "
              "asking the LLM to explain the root cause and propose a fix")

        files_section = "\n\n".join(
            f"=== {name} ===\n{content if content is not None else '(does not exist yet)'}"
            for name, content in current_files.items()
        )

        # Surface the assertion sby actually named as failing induction, plus
        # the aux invariants already ruled out, instead of relying on the
        # model to dig either out of the raw log.
        failure_digest = describe_induction_failures(response.stdout, current_files)
        rejected_section = ""
        if rejected_aux:
            rejected_section = (
                "\n\nAuxiliary invariants ALREADY RULED OUT on this design - do "
                "not propose any of these again, in any form:\n"
                + "\n".join(f"  - {l}" for l in rejected_aux) + "\n")

        # Name the control registers the mechanical search identified but
        # could not reach on its own (they need an observation port added to
        # the DUT). Without this the model has to guess which internal state
        # matters, and the run that motivated this guessed only `round`.
        candidates_section = describe_invariant_candidates(invariant_candidates)

        prompt = (
            build_explain_and_fix_prompt(sby_file, miter_file)
            + ("\n\n" + failure_digest if failure_digest else "")
            + rejected_section
            + ("\n\n" + candidates_section if candidates_section else "")
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
        print(f"Reasoning report ({format_usage(usage) if usage else 'no usage reported'}):")
        print(reasoning or "(none provided)")

        if replacements is None:
            print("WARNING: could not parse a JSON fix list from the LLM's "
                  "response; stopping design-fix attempts")
            history.append(f"Design-fix attempt {design_attempt}: reasoning "
                            f"(excerpt): {reasoning[:400]!r}. Could not parse a "
                            f"fix from the response; stopped design-fix attempts.")
            break
        if not replacements:
            print("LLM proposed no fix (empty JSON array) - treating this as "
                  "a fundamental design limitation rather than a bug to patch.")
            history.append(f"Design-fix attempt {design_attempt}: reasoning "
                            f"(excerpt): {reasoning[:400]!r}. LLM proposed no "
                            f"fix - treated as a fundamental design limitation.")
            break

        if was_exclusion_attempt:
            # Policy: a genuine counterexample must be handled by excluding
            # the triggering condition in the miter, never by patching the
            # design-under-test (see EXPLAIN_AND_FIX_PROMPT). Enforce it
            # here rather than trusting the LLM to have followed it -
            # reject any edit to a different file before it's ever applied.
            disallowed = [r for r in replacements if r.get("file") != miter_file]
            if disallowed:
                bad_files = sorted({r.get("file") for r in disallowed})
                print(f"WARNING: rejecting edit(s) to {bad_files} - a genuine "
                      f"counterexample must be handled by excluding the "
                      f"triggering condition in {miter_file} only, never by "
                      f"patching the design-under-test.")
                replacements = [r for r in replacements if r.get("file") == miter_file]
            if not replacements:
                history.append(f"Design-fix attempt {design_attempt}: reasoning "
                                f"(excerpt): {reasoning[:400]!r}. Rejected - all "
                                f"proposed edit(s) touched disallowed file(s) "
                                f"{bad_files} instead of {miter_file}; nothing "
                                f"applied.")
                write_snapshot_to_disk(source_files + [sby_file], output_dir, sby_file=sby_file, miter_file=miter_file, iteration=f"design_{design_attempt}_rejected")
                continue

        report = get(apply_replacements.chia_remote(replacements))
        for r in report:
            if not r["find"]:
                print(f"Created/overwrote {r['file']}")
            elif r["count"] == 0:
                print(f"WARNING: find text not present in {r['file']}, no-op: {r['find']!r}")
            else:
                print(f"Replaced {r['count']} occurrence(s) in {r['file']}")

        # The DIT goal is immutable: weakening or guarding it would make a
        # subsequent PASS meaningless (the loop would be "proving" a property
        # nobody asked for). Checked on the applied text rather than on the
        # proposed edits, since a find/replace can reach into the goal region
        # without naming it; reverted in place if violated.
        post_edit_miter = get(read_files.chia_remote([miter_file]))[miter_file] or ""
        if goal_was_modified(pre_edit_miter, post_edit_miter):
            print(f"WARNING: rejecting this fix - it modified the protected DIT "
                  f"goal region of {miter_file}. The goal states what is being "
                  f"verified and must stay fixed; only the aux-invariant region "
                  f"may change. Reverting.")
            get(apply_replacements.chia_remote(
                [{"file": miter_file, "find": "", "replace": pre_edit_miter}]))
            history.append(f"Design-fix attempt {design_attempt}: reasoning "
                            f"(excerpt): {reasoning[:400]!r}. REJECTED - the edit "
                            f"modified the immutable DIT goal region; reverted "
                            f"without running sby.")
            write_snapshot_to_disk(source_files + [sby_file], output_dir,
                                    sby_file=sby_file, miter_file=miter_file,
                                    iteration=f"design_{design_attempt}_goalviolation")
            continue

        response = get(run_sby.chia_remote(sby_file, sby_timeout))
        print("Response from run_sby:")
        print(response)
        result = classify_sby_result(response)

        if result == "error":
            # The patch broke the build - not a real design question, just a
            # bad edit. Revert to the last state sby could actually parse
            # (still "fail" - a real, standing counterexample) rather than
            # leaving broken files behind, then re-run sby to confirm the
            # revert actually restores that state.
            print(f"Design-fix attempt {design_attempt} broke the build (sby "
                  f"result=error); reverting to the last known-good state.")
            revert_ops = [{"file": name, "find": "", "replace": content}
                          for name, content in last_good_files.items()
                          if content is not None]
            get(apply_replacements.chia_remote(revert_ops))
            response = get(run_sby.chia_remote(sby_file, sby_timeout))
            result = classify_sby_result(response)
            history.append(f"Design-fix attempt {design_attempt}: reasoning "
                            f"(excerpt): {reasoning[:400]!r}. Applied "
                            f"{len(replacements)} edit(s), but that broke sby's "
                            f"setup (result=error) - reverted to the last "
                            f"known-good state (result after revert: {result}).")
        else:
            history.append(f"Design-fix attempt {design_attempt}: reasoning "
                            f"(excerpt): {reasoning[:400]!r}. Applied "
                            f"{len(replacements)} edit(s). New result: {result}.")
            if result in ("fail", "unknown"):
                # Still sby-parseable - this is the new baseline to revert to
                # if a LATER attempt breaks the build.
                last_good_files = get(read_files.chia_remote(source_files + [sby_file]))

            if was_exclusion_attempt:
                # Record what got excluded, and flag (never auto-reject - a
                # human should look) anything that looks like it forces the
                # two instances' data inputs equal instead of excluding a
                # specific value - see find_broad_data_exclusion's docstring.
                # post_edit_miter was read for the goal-region check above and
                # is still current: a goal violation would have reverted and
                # skipped this branch entirely.
                pre_lines = set(pre_edit_miter.splitlines())
                new_lines = [l for l in post_edit_miter.splitlines() if l not in pre_lines]
                new_assumes = [l.strip() for l in new_lines if "assume" in l]
                exclusions.extend(new_assumes)
                suspicious = find_broad_data_exclusion(new_lines)
                if suspicious:
                    print(f"WARNING: this exclusion looks like it may force two "
                          f"data inputs to be equal rather than excluding a "
                          f"specific value: {suspicious!r}. That would trivially "
                          f"satisfy the property without proving anything real - "
                          f"please review the miter by hand.")
                    history.append(f"Design-fix attempt {design_attempt}: "
                                    f"WARNING - exclusion {suspicious!r} looks "
                                    f"overly broad (equates two data inputs); "
                                    f"needs manual review.")

        write_snapshot_to_disk(source_files + [sby_file], output_dir, sby_file=sby_file, miter_file=miter_file, iteration=f"design_{design_attempt}")

    if result == "pass":
        if exclusions:
            print(f"Property holds after {attempt} setup-fix attempt(s) and "
                  f"{design_attempt} design-fix attempt(s) - but ONLY under "
                  f"{len(exclusions)} documented exclusion(s), not "
                  "unconditionally. This is NOT a clean DIT result - the "
                  "design has data-independent timing for every input EXCEPT "
                  "the excluded condition(s) below:")
            for e in exclusions:
                print(f"  - {e}")
        elif design_attempt:
            print(f"Design fix found and the property now holds, after {attempt} "
                  f"setup-fix attempt(s) and {design_attempt} design-fix attempt(s).")
        else:
            print(f"Property holds after {attempt} LLM attempt(s) - the design "
                  "appears to be data-independent-timing for the inputs checked.")
    elif result == "fail":
        print(f"sby ran successfully and found a genuine counterexample after "
              f"{attempt} setup-fix attempt(s) and {design_attempt} design-fix "
              "attempt(s) - a real timing side-channel. See the reasoning "
              "report(s) and last run_sby output above.")
    elif result == "unknown":
        print(f"sby ran cleanly but k-induction stayed inconclusive after "
              f"{attempt} setup-fix attempt(s) and {design_attempt} design-fix "
              "attempt(s) (base case passed, induction step never closed); "
              "giving up. The property may still be true - it likely just "
              "needs a stronger auxiliary invariant than what was tried.")
    elif result == "timeout":
        print(f"sby never completed within the time budget (last try: {sby_timeout}s) "
              f"after {attempt} setup-fix attempt(s) and {design_attempt} design-fix "
              "attempt(s); giving up. This is not a setup or design bug - BMC just "
              "needs more depth/wall-clock than budgeted for this design, or a "
              "different engine/mode.")
    else:
        print(f"sby setup broken after {attempt} setup-fix attempt(s) and "
              f"{design_attempt} design-fix attempt(s); giving up.")

    # Unconditional closing report: ask the LLM to summarize the whole run
    # in its own words, regardless of how it ended. Separate from the
    # deterministic status prints above (which are precise but mechanical) -
    # this is meant to read like a human handoff note for someone who
    # wasn't watching the run live.
    history_section = "\n".join(f"- {h}" for h in history) if history else "(no fix attempts were needed)"
    exclusions_section = (
        "\n".join(f"- {e}" for e in exclusions) if exclusions
        else "(none - no exclusions were needed or applied)"
    )
    summary_prompt = (
        SUMMARY_PROMPT
        + "\n\nStep-by-step history:\n" + history_section
        + "\n\nDocumented exclusions (conditions assumed away to isolate a "
          "narrower DIT claim - if any exist and the final result is PASS, "
          "the summary MUST state the result is conditional on these, not "
          "an unconditional DIT guarantee):\n" + exclusions_section
        + f"\n\nFinal classification: {result}"
        + "\n\nLast sby stdout (tail):\n" + response.stdout[-3000:]
    )
    summary_resp, _ = call_llm(llm, summary_prompt)
    print("\n" + "=" * 80)
    print("Final summary (LLM-authored):")
    print("=" * 80)
    print(summary_resp.result.strip() or "(LLM produced no summary)")
    print("\n" + "=" * 80)
    print(f"Final result: {result}")
    print(f"Total LLM usage this run: {format_usage(RUN_USAGE_TOTALS)}")
    print("\n" + "=" * 80)
    print(f"Output files written to: {output_dir}")
    print(f"Generated .sby files: {os.path.join(output_dir, 'generated_source')}")
    print("=" * 80)


if __name__ == "__main__":
    main()
