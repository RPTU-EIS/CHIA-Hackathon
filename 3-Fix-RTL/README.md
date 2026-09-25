# CHIA Fix-RTL Loop

A **generic, project-agnostic RTL-debug loop**: given a design with a
**golden, already-validated formal property suite** (bound into the design
via SystemVerilog `bind`, or however the source files already wire it in),
run formal verification, and whenever a property fails, task an LLM with
diagnosing the counterexample and patching the **design RTL** - never the
properties - then re-run. Repeats until every requested engine is clean, or
the round budget runs out.

## Setup

Set your GCP project once (falls back to a project this was developed
against if unset - you almost certainly want to set your own):

```bash
export VERTEX_PROJECT=your-gcp-project-id
```

## Usage

Run from **this directory** (`3-Fix-RTL/`) so `Case Studies/` ships as part
of the job's `working_dir`. **`--working-dir` is required** and must be the
absolute path to this same directory on whatever machine you're running on
- `"$(pwd)"` from here is the simplest way to get it right:

```bash
chia job submit --working-dir . -- python fix-rtl.py \
    --working-dir "$(pwd)" \
    --source-files "Case Studies/01-FIFO/fifo.sv" \
                   "Case Studies/01-FIFO/fifo_checker.sv" \
    --writable "Case Studies/01-FIFO/fifo.sv" \
    --top fifo \
    --engines bmc cover
```

```bash
chia job submit --working-dir . -- python fix-rtl.py \
    --working-dir "$(pwd)" \
    --source-files "Case Studies/02-Processor/proc-package.sv" \
                   "Case Studies/02-Processor/proc-seq-mutated.sv" \
                   "Case Studies/02-Processor/proc_checker.sv" \
    --writable "Case Studies/02-Processor/proc-seq-mutated.sv" \
    --top proc \
    --engines bmc cover
```

```bash
chia job submit --working-dir . -- python fix-rtl.py \
    --working-dir "$(pwd)" \
    --source-files "Case Studies/03-HMAC-DRBG/fv_hmac_drbg_pkg.sv" \
                   "Case Studies/03-HMAC-DRBG/hmac_core.v" \
                   "Case Studies/03-HMAC-DRBG/hmac_drbg.sv" \
                   "Case Studies/03-HMAC-DRBG/sha512_masked_core_stub.sv" \
                   "Case Studies/03-HMAC-DRBG/hmac_drbg_lfsr_stub.sv" \
                   "Case Studies/03-HMAC-DRBG/hmac_checker.sv" \
    --writable "Case Studies/03-HMAC-DRBG/hmac_drbg.sv" \
    --top hmac_drbg \
    --engines bmc cover \
    --bmc-max-depth 100 --cover-start-depth 100 --cover-max-depth 100 \
    --bmc-timeout 28800 --cover-timeout 28800
```

### Key options

| Flag | Meaning |
|---|---|
| `--working-dir` | **Required, no default.** Absolute path to this machine's real checkout of `3-Fix-RTL/` - see Architecture notes below for why there's deliberately no fallback. |
| `--vertex-project` | GCP project for Vertex AI Gemini calls. Default: `$VERTEX_PROJECT` env var if set, else the project this was developed against. |
| `--source-files` | Every file `read_slang` needs, **in order** (packages before things that import them, etc.), as paths relative to `--working-dir`. |
| `--writable` | The **one** file (must be one of `--source-files`) the LLM may edit. Everything else is golden - an edit anywhere else is rejected before it ever reaches disk. |
| `--top` | Top-level module name for `read_slang`/`prep`. |
| `--engines` | One or more of `bmc`, `cover`, `prove`, run **concurrently**. `bmc` alone is the simple single-engine loop. Adding `cover` lets it tell `bmc` how deep it actually needs to go - a depth that's too shallow doesn't just slow bug-hunting down, it can hide bugs entirely (confirmed on the HMAC project: several back-half FSM states were genuinely unreachable at a shallow depth, so a shallow-only loop would never have found bugs there). Adding `prove` attempts an unbounded PDR proof; PDR does **not** converge quickly on non-trivial designs, so expect it to time out and retry rather than resolve fast. |
| `--bmc-start-depth` | Default: ask the LLM to propose one, given the actual RTL/property content. |
| `--bmc-max-depth` / `--cover-start-depth` / `--cover-max-depth` | Depth ceilings/starting points; defaults sized for small designs like the Processor - HMAC needs much deeper (100) and much longer timeouts, see the example above. |
| `--bmc-timeout` / `--cover-timeout` / `--prove-timeout` | Per-invocation kill timers, in seconds. |
| `--max-rounds` | LLM fix-and-restart budget for the whole run. |

## Architecture notes

- **Engines run concurrently and cross-inform each other**, via a real
  non-blocking wait-for-any (`chia_wait`/`TrackedRef`), not sequential
  blocking calls. The moment any engine reports a genuine `FAIL`, the
  others are cancelled (their result would be checking RTL that's about to
  change anyway) before the fix is diagnosed and applied, then every
  requested engine restarts fresh.
- **Grounding requirement**: the prompt requires the LLM to quote the exact
  current line(s) it's diagnosing, verbatim from the file content it was
  shown, before proposing a fix. Added after a real round (on the Processor
  case study) where the reasoning confidently described a line that did not
  exist anywhere in the file, reasoned about it at length, and proposed a
  fix for a bug that was never there.
- **Filesystem**: the driver process (running `main()`) reads/writes
  `--working-dir` directly - real, working NFS access, confirmed by every
  successful run **when `--working-dir` is a real absolute path**. This
  flag has no default for a reason: confirmed directly that the driver's
  own OS working directory is Ray's ephemeral per-job snapshot (deleted
  once the job exits), never the directory you actually ran
  `chia job submit` from - a file written to that default was gone the
  moment the job ended. The `sby`-resourced *worker* that actually runs
  verification does **not** share that filesystem at all (confirmed from a
  real crash: a worker turned out to execute under a completely separate
  environment - `/home/ray/anaconda3/...`, no NFS mount whatsoever - even
  on the same physical node the driver itself runs on). So every file a
  given `sby` invocation needs is passed explicitly as a `file_contents`
  argument, through Ray's own object store, and written into that task's
  own flat, node-local directory before `sby` runs - not assumed to be
  visible via any shared path. The generated `.sby` content and every
  source file are referenced by **bare basename** on the worker side for
  exactly this reason (a nested relative path would need its parent
  directory created first, and a shared path might not exist on the worker
  at all).
- **Cost tracking**: LLM token usage (and, if you fill in current Vertex
  pricing at the top of `fix-rtl.py`, an estimated $ cost) is tracked and
  reported per round and cumulatively.

## Case Studies

- **`01-FIFO/`** - a small synchronous FIFO (`addr_gen` + `fifo`). 
  `fifo.sv` is the writable DUT, deliberately run at `MAX_DATA=17`
  - a non-power-of-two depth that exercises two real, stacked
  bugs: `count`/`data_count` are only 4 bits wide (can't represent all 17
  occupancy levels, 0 through `MAX_DATA`), and once that's widened, the
  read/write addresses (also 4 bits) can't represent all 17 slot positions
  either - confirmed empirically that fixing only the first bug still
  fails, and both need widening (matching `ADDR_BITS`-style generalization)
  before the property suite passes. `fifo_checker.sv` is the golden
  property suite. Its `count`/`waddr`/`raddr` observation ports are deliberately **oversized** (8 bits)
  so the bind connection stays valid regardless of how wide the LLM's fix
  ends up making the DUT's own ports partway through - confirmed
  empirically that a narrower DUT signal connecting into a wider checker
  port zero-extends correctly and gives the same verdict as an exactly-
  matching width, at both the buggy and fixed ends of this case study.
- **`02-Processor/`** - a small 5-stage sequential RISC-style processor,
  translated from a OneSpin TDA property suite. `proc-seq-mutated.sv` has
  one or more injected bugs; `proc_checker.sv` is the golden, validated
  property suite (`bind`-based).
- **`03-HMAC-DRBG/`** - an HMAC-DRBG random number generator. `hmac_drbg.sv`
  has four injected bugs (a register-capture swap, a magic-constant typo, a
  skipped FSM state, and an off-by-one boundary check); `hmac_checker.sv` is
  the golden property suite. Note: this suite deliberately does **not**
  check the cryptographic correctness of the computed tag value - the
  SHA-512 cores are blackboxed, so only FSM/control/handshake timing is checked.

