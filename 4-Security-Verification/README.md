# CHIA Generalized UPEC DIT Verification Loop

This directory contains a **generic, automated Data-Independent Timing (DIT) verification loop** for cryptographic hardware modules. The loop uses CHIA to orchestrate formal verification tasks based on UPEC-DIT and LLM-driven design refinement.

## What is DIT?

**Data-Independent Timing (DIT)** exists if the execution time of a (cryptographic) operation depends **only on control inputs** (e.g., the chosen algorithm), not on the **data being processed** (secret keys, plaintexts, etc.). Verifying DIT is crucial for preventing **timing-based side-channel attacks** that can leak secret information by observing the execution time of operations.

## How It Works

The CHIA Loop performs **automated formal verification and design refinement**:

### Setup Phase (Setup-Fix Loop)
1. **Initial sby run**: Execute SymbiYosys (sby) to check the DIT property
2. **If error/timeout**: Task the LLM to fix the `.sby` configuration or property
3. **Apply edits**: Use deterministic JSON patch format to update miter/design
4. **Retry**: Re-run sby with the refined setup
5. **Repeat** until the property check runs cleanly

### Analysis Phase (Design-Fix Loop)
Once sby runs without errors, classify the result:

- **PASS**: Property proven ✅ → Report success
- **FAIL**: Real counterexample found 🔴 → Enter design-fix loop
- **UNKNOWN**: Inconclusive induction proof (k-induction failed) ⚠️ → Enter design-fix loop to strengthen invariants

### Design-Fix Loop

For genuine counterexamples or inconclusive proofs.

**FAIL (real counterexample)** goes straight to the LLM, restricted to the
**miter file only** — no design changes allowed. Edits to any other file are
rejected before they are applied; if a real bug exists we document and report
it rather than patching the RTL to make the property pass.

**UNKNOWN (inconclusive induction)** first tries a sequence of **mechanical
remedies that cost no LLM call at all**, in increasing order of intrusiveness.
Each either closes the proof or rules something out deterministically:

1. **Engine escalation** — the property is left *completely untouched* and a
   stronger solver configuration is tried (`abc pdr`, then k-induction at
   depth 40). PDR derives its own inductive strengthening, so it often proves
   what k-induction leaves open, with no property edit whatsoever.
2. **BMC validation of each auxiliary invariant** — every helper is checked
   alone under `mode bmc`. One that BMC refutes is *false in a reachable
   state*, can never be part of a valid proof, and is removed outright.
3. **Bisection** — leave-one-out over the auxiliary invariants. If dropping
   one closes the proof, that one was blocking the induction (it was true but
   not inductive). Skipped when there is ≤1 helper, since the trial would be
   vacuous.
4. **Additive search** — the opposite move, for when the invariant set is too
   *weak* rather than wrong. Candidate control registers are extracted from
   the DUT's own declarations and added cumulatively. Datapath registers are
   excluded by name and by width, because asserting those equal between the
   two instances is false by construction (they *must* differ when the data
   differs) and would poison the proof.

Only if all of these fail is the LLM asked — and it is then given the
assertion sby actually named as failing, the invariants already ruled out,
and the specific control registers the mechanical search identified but
could not reach on its own. Its JSON edits are applied and sby re-run, up to
15 design-fix iterations, until PASS or the budget is exhausted.

#### Why both directions matter

An inconclusive induction has two opposite causes, and fixing the wrong one
makes things worse:

| Cause | Symptom | Fix |
|---|---|---|
| Invariant set too **weak** | Proof needs more supporting state | **Add** a control-state equality |
| An added invariant is **wrong** | One helper is false or non-inductive | **Remove** that helper |

A single unprovable assertion keeps the induction step open forever, so
piling more invariants on top of a bad one can never help. Both directions
are now handled mechanically before the LLM is consulted.

### Protected Regions in the Miter

The miter is split by marker comments into two regions:

```verilog
// === DIT GOAL (do not modify) ===
  assert(cmd_o1 == cmd_o2);
// === END DIT GOAL ===
// === AUX INVARIANTS (loop-managed) ===
  assert(round_o1 == round_o2);
// === END AUX INVARIANTS ===
```

- The **goal region** states what is actually being verified. It is
  **immutable** — any edit reaching into it is detected and reverted, because
  weakening or guarding the goal would make a subsequent PASS meaningless.
- The **aux region** is owned by the loop: this is what validation,
  bisection and the additive search add to and remove from.

### Observation Ports

For an inconclusive induction — and *only* then — the LLM may add a **pure
observation port** to the design under test to expose an internal control
register:

```verilog
output [6:0] round_o;
assign round_o = round;
```

This is permitted because it adds no logic and cannot change behavior; no
existing assignment, expression or reset value may be touched. Control state
usually has to be exposed as a **group** (a counter, the FSM state and the
busy flag typically only close the induction together).

### File Preservation
All iterations are saved to `/tmp/chia-dit-output/generated_source/`:
- `.sby` files: `<design>_20260909_120000.sby` (timestamped with each update)
- Miter files: `<design>_miter_setup_1_20260909_120000.sv`, `<design>_miter_design_2_20260909_120000.sv`, etc.

This lets you **trace the exact refinement sequence** from initial broken setup through LLM-driven fixes.

## Setup

```bash
export VERTEX_PROJECT=your-gcp-project-id
```

Falls back to the project this was developed against if unset; override
per-invocation with `--vertex-project` instead if you'd rather not export
an env var. No `--working-dir` flag here - unlike the other experiments in
this hackathon submission, this loop never persists edits back to the
driver's own filesystem (see the File Preservation section above), so
there's no equivalent portability concern for it.

## Usage

### Basic Command

```bash
chia job submit --submission-id <job-name> --working-dir . -- \
  python upec-dit-check.py --source-files <design>.v <design>_miter.sv
```

### Example: Verify SHA-1

```bash
chia job submit --submission-id sha1_verify --working-dir . -- \
  python upec-dit-check.py --source-files sha1.v sha1_miter.sv
```

### Example: Verify SHA-256

```bash
chia job submit --submission-id sha256_verify --working-dir . -- \
  python upec-dit-check.py --source-files sha256.v sha256_miter.sv
```

### Example: Verify Custom RSA Design

```bash
chia job submit --submission-id rsacypher_verify --working-dir . -- \
  python upec-dit-check.py --source-files modmult.vhd rsacypher.vhd rsacypher_miter.sv
```

### Command-Line Options

- `--source-files <file1> <file2> ...`: Design files to verify
  - Must include exactly one `*_miter.sv` file (defines the DIT check structure)
  - Other files are the design under test (can be `.v`, `.vhd`, or mix)
  - The miter's basename determines the generated `.sby` filename: `sha1_miter.sv` → `sha1.sby`

- `--llm {vertex|opencode}`: LLM backend (default: `vertex` = Gemini)

## The Miter File

The **miter file** defines the DIT property formally. It must:

1. **Instantiate two copies** of the design with:
   - **Shared control inputs**: Both instances get identical control signals
   - **Independent data inputs**: Each instance has separate data (can differ arbitrarily)
   - Example: `cmd_i` (shared), `text_i1` and `text_i2` (independent)

2. **Assert timing equivalence**: Check that both instances produce identical timing signals
   - Example: `assert(cmd_o1 == cmd_o2)` where `cmd_o` encodes the busy/ready signal

3. **Use proper reset**: Force genuine reset via assumption, not just conditional checking

4. **Carry the region markers**: the goal assertion inside the `DIT GOAL`
   markers, supporting invariants inside the `AUX INVARIANTS` markers (see
   [Protected Regions](#protected-regions-in-the-miter)). The loop's
   mechanical remedies rely on these to tell the property apart from its
   helpers; without them it falls back to asking the LLM for everything.

   ```verilog
   reg init;
   initial init = 1;
   always @(posedge clk) begin
     if (init) begin
       assume(rst);
       init <= 0;
     end else if (!rst) begin
       // === DIT GOAL (do not modify) ===
       assert(cmd_o1 == cmd_o2);
       // === END DIT GOAL ===
       // === AUX INVARIANTS (loop-managed) ===
       // === END AUX INVARIANTS ===
     end
   end
   ```

### Example Miter Structure

```verilog
module sha1_miter (
  input           clk,
  input           rst,
  
  // Shared control inputs
  input   [2:0]   cmd_i,
  input           cmd_w_i,
  
  // Independent data inputs
  input   [31:0]  text_i1, text_i2,
  
  // Outputs
  output  [31:0]  text_o1, text_o2,
  output  [3:0]   cmd_o1, cmd_o2
);

  sha1 SHA1_1 (
    .clk_i(clk), .rst_i(rst),
    .cmd_i(cmd_i), .cmd_w_i(cmd_w_i),      // SHARED
    .text_i(text_i1), .text_o(text_o1),
    .cmd_o(cmd_o1)
  );

  sha1 SHA1_2 (
    .clk_i(clk), .rst_i(rst),
    .cmd_i(cmd_i), .cmd_w_i(cmd_w_i),      // SHARED
    .text_i(text_i2), .text_o(text_o2),
    .cmd_o(cmd_o2)
  );

  // DIT property
  reg init;
  initial init = 1;
  always @(posedge clk) begin
    if (init) begin
      assume(rst);
      init <= 0;
    end else if (!rst) begin
      // === DIT GOAL (do not modify) ===
      assert(cmd_o1 == cmd_o2);
      // === END DIT GOAL ===
      // === AUX INVARIANTS (loop-managed) ===
      // === END AUX INVARIANTS ===
    end
  end
endmodule
```

The aux region starts empty. The loop fills it in — mechanically where it
can, via the LLM where it cannot — and empties it again when a helper turns
out to be blocking the proof.

## How the Loop Generates the `.sby` File

The loop doesn't require a pre-existing `.sby` file. On the first run:

1. sby fails with `FileNotFoundError: 'sha1.sby'` (expected)
2. The loop asks the LLM: *"Design a SymbiYosys configuration that elaborates these source files and checks the DIT property"*
3. The LLM responds with JSON edits, including a create operation: `{"file": "sha1.sby", "find": "", "replace": "[options]\nmode prove\n..."}`
4. The loop applies the edit (creates `sha1.sby`)
5. sby runs again with the generated configuration

The LLM knows **12 toolchain-specific rules** (ghdl plugins, reset handling, no `bind`, etc.) that ensure the `.sby` file works correctly with open-source SymbiYosys.

## Output

After the job completes, check `/tmp/chia-dit-output/generated_source/` on the cluster worker for:

- **All `.sby` iterations**: Shows how the property and configuration evolved
- **All miter iterations**: Shows auxiliary invariants added to strengthen the proof
- **Final report**: In job logs, explains the verification outcome

Example output:
```
================================================================================
Final summary (LLM-authored):
================================================================================
This report summarizes the data-independent-timing (DIT) verification run for the RSA cipher design. The goal of this check was to ensure the hardware's execution time does not depend on the secret data it processes, such as the private key. This is a critical security property to prevent side-channel attacks where an adversary could deduce secret information simply by measuring how long a cryptographic operation takes.

The verification process first encountered setup errors, which prevented the check from running. Initial automated fixes to the VHDL source code were unsuccessful, but a second attempt correctly configured the verification tool to parse the design files, allowing the analysis to proceed. Once the setup was corrected, the tool immediately found a genuine timing vulnerability. The automated system then made three separate attempts to patch the design's logic. Each attempt correctly identified that the root cause was a data-dependent loop within the modular multiplication submodule, but none of the proposed code changes successfully fixed the timing leak.

The final result of the verification is FAIL. This means the RSA cipher design is not constant-time and contains a timing side-channel vulnerability. The time it takes to complete an operation changes based on the input data. This failure was confirmed even when specific edge cases, such as zero-value inputs or a modulus of one, were excluded from the check. The automated tool concluded that the timing leak is a fundamental part of the current algorithm and could not be patched automatically.

================================================================================
Output files written to: /tmp/chia-dit-output
Generated .sby files: /tmp/chia-dit-output/generated_source
================================================================================
```

## Troubleshooting

### "Hierarchical reference failed: Identifier implicitly declared"
The miter tried to reference an internal signal that's not an output port (e.g., `SHA1_1.read_counter`). The LLM should expose it as an output port first, then wire it in the miter.

### "DONE (UNKNOWN)"
The base case passed but k-induction failed — sby found **no** violation, it
just couldn't close the proof. This is *not* evidence of a design bug.

The loop handles it automatically: it first escalates the engine (`abc pdr`),
then checks each auxiliary invariant under BMC, then bisects, then tries
adding control-state equalities — all without an LLM call. Only if none of
that works does it ask the LLM, naming the failing assertion and the
candidate registers.

If a run still ends UNKNOWN, the two things worth checking by hand are
whether a needed control register is still unexposed (the loop can only
suggest the port, the LLM has to add it), and whether an auxiliary invariant
in the miter is one that cannot hold — anything equating **datapath** values
between the two instances is false by construction, since those must differ
when the data differs.

### A helper assertion keeps coming back
Auxiliary invariants ruled out during a run are fed back to the LLM as a
do-not-repeat list. If one still reappears, check that the miter carries the
`AUX INVARIANTS` markers — without them the loop cannot tell the property
apart from its helpers and skips the mechanical remedies entirely.

## References

- **SymbiYosys (sby)**: Formal verification tool (https://yosyshq.net/yosys/)
- **CHIA**: Cluster Hardware Integrated Architecture for distributed task execution
- **DIT verification**: Standard technique for constant-time cryptography (see UPEC-DIT papers)
