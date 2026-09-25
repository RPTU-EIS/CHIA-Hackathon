# CHIA Hackathon Submission

Four CHIA-orchestrated, LLM-driven formal-verification experiments, meant to
be **repeatable on any machine** without editing any script. This document
is the single entry point: one-time setup, then a run command for each
experiment. Folder-level detail (what each experiment actually does, file
listings, prompts) lives in that folder's own `README.md`.

| # | Folder | What it does |
|---|---|---|
| 1 | [`1-Translate-Properties/`](1-Translate-Properties/README.md) | Ports a GCD core's formal property set from a commercial tool (OneSpin) to open-source SymbiYosys. |
| 2 | [`2-Generate-Properties/Wishbone/`](2-Generate-Properties/Wishbone/README.md) | Two loops that author SVA properties for a Wishbone-slave RAM from its spec PDF - one judged by mutation-coverage, one report-and-fix. |
| 2 | [`2-Generate-Properties/APB4-formal/`](2-Generate-Properties/APB4-formal/apb4_gemini_formal/RESULTS.md) | Discovers and reports RTL bugs in an APB4 master/slave system via LLM-authored formal properties (never modifies RTL). |
| 3 | [`3-Fix-RTL/`](3-Fix-RTL/README.md) | Generic RTL-debug loop: a golden property suite catches a bug, an LLM diagnoses and patches the RTL. Three case studies (FIFO, Processor, HMAC-DRBG). |
| 4 | [`4-Security-Verification/`](4-Security-Verification/README.md) | UPEC-DIT timing-side-channel verification loop - reports genuine timing leaks rather than patching them away. Four case studies (RSA, SHA-1/256/512). |

## One-time setup

Copy these files in your existing CHIA install. 
Run `docker build -f SbyDockerfile -t chia-sby:latest .`

### 1. A running CHIA/Ray cluster

Every experiment below needs an already-running cluster with `sby`-resourced
formal-verification workers and Vertex AI credentials. This repo's own
cluster config is a **template**, not a ready-to-use file, since it
hardcodes nothing about any specific machine - see `cluster.yaml.template`
at the repo root:

```bash
export CHIA_HEAD_IP=your.cluster.head.ip
export VERTEX_PROJECT=your-gcp-project-id   # every experiment below reads this too
envsubst '${CHIA_HEAD_IP} ${VERTEX_PROJECT}' < cluster.yaml.template > cluster.yaml
chia up cluster.yaml
```

For running the experiments in folder 3-Fix-RTL use `3-Fix-RTL-cluster.yaml.template`

### 2. Important information

Every experiment that persists output to your own filesystem (1, 2, 3) takes an
explicit `--working-dir` argument instead of assuming one - see "Why
`--working-dir` is required, not defaulted" below before wondering why it's
not just optional.

## Running each experiment

All of these assume you `cd` into the named directory first, so relative
paths in the commands resolve correctly.

**1 - Translate-Properties** (GCD, OneSpin → SymbiYosys):
```bash
cd 1-Translate-Properties
chia job submit --working-dir . -- python translate-properties.py --working-dir "$(pwd)"
```

**2 - Generate-Properties / Wishbone** (property synthesis, judged by mutation coverage):
```bash
cd 2-Generate-Properties/Wishbone
chia job submit --working-dir . -- python discover-covering-properties.py --working-dir "$(pwd)" --rounds 40
```
See [its README](2-Generate-Properties/Wishbone/README.md) for the second,
report-and-fix-style script (`discover-spec-property.py`) in the same
folder.

**2 - Generate-Properties / APB4-formal** (bug discovery, RTL never modified):
```bash
cd 2-Generate-Properties/APB4-formal
export APB4_RESULT_DIR="$(pwd)/apb4_gemini_formal/live_result_shorter"
chia job submit --working-dir . -- python apb4_gemini_formal/loop-apb4-formal_shorter.py
```
See its own [RESULTS.md](2-Generate-Properties/APB4-formal/apb4_gemini_formal/RESULTS.md)
for a saved example run.

**3 - Fix-RTL** (three case studies - FIFO shown, see its README for Processor/HMAC-DRBG):
```bash
cd 3-Fix-RTL
chia job submit --working-dir . -- python fix-rtl.py \
    --working-dir "$(pwd)" \
    --source-files "Case Studies/01-FIFO/fifo.sv" "Case Studies/01-FIFO/fifo_checker.sv" \
    --writable "Case Studies/01-FIFO/fifo.sv" \
    --top fifo \
    --engines bmc cover
```

**4 - Security-Verification** (UPEC-DIT, example: SHA-1):
```bash
cd 4-Security-Verification
chia job submit --submission-id sha1_verify --working-dir . -- \
  python upec-dit-check.py --source-files sha1.v sha1_miter.sv
```
No `--working-dir` flag needed here - see its README for why.
