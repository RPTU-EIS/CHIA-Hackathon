# CHIA Wishbone Property-Discovery Experiments

Two different CHIA loops for the same design (`wishbone_ram.sv`, a classic
single-cycle Wishbone-slave RAM, checked against the Wishbone B4
specification PDF, `wbspec_b4.pdf`) - same DUT, two different philosophies
for how an LLM should author its property set.

## Setup

```bash
export VERTEX_PROJECT=your-gcp-project-id
```

Both scripts require `--working-dir`, the absolute path to this directory
on whatever machine you're running on - `"$(pwd)"` from here is the
simplest way to get it right (see each script's own `--working-dir`
`--help` text for why there's deliberately no default).

## `discover-covering-properties.py` - property SYNTHESIS, judged by mutation coverage

Never fixes anything, and never diagnoses `wishbone_ram.sv` as buggy - the
DUT is fixed for the whole run. Each round an LLM proposes ONE new SVA
property; it's kept only if it (a) holds cleanly and is non-vacuous, and
(b) either improves `mcy` mutation-coverage or closes a gap in an
LLM-extracted checklist of individual spec requirements (mutation coverage
alone saturates fast and stops distinguishing further properties once it
does - the checklist keeps rounds targeting genuinely new spec ground after
that point). A genuine counterexample doesn't get diagnosed/patched either
- it goes through a two-step triage (initial call, then a deliberately
skeptical independent second opinion) to decide whether it's a real spec
violation worth reporting or just a bad property, and either way gets
discarded from the live checker (a permanently-failing assert would break
every later round's sanity gate).

```bash
chia job submit --working-dir . -- python discover-covering-properties.py \
    --working-dir "$(pwd)" \
    --rounds 40
```

Key flags: `--rounds` (candidate-property budget), `--mutation-size`
(mcy sample size per round), `--sby-timeout` / `--mcy-init-timeout` /
`--mcy-run-timeout` (kill timers - a single pathological property can
otherwise hang a solver indefinitely with no way to recover). Run
`python discover-covering-properties.py --help` for the full list.

## `discover-spec-property.py` - property DISCOVERY, report-and-fix

The older, simpler sibling: derives an initial property set from the spec
PDF, and when a property fails, triages whether the fix belongs in the
property (misread the spec) or the DUT (a genuine bug - unlike the
Fix-RTL case studies, this DUT is not assumed golden going in) and applies
it directly, converging toward one property set that holds. Predates the
file_contents-via-Ray's-object-store fix used elsewhere in this project
(see `discover-covering-properties.py`'s own FILESYSTEM docstring note) -
still uses the older remote-read/remote-write/snapshot-back pattern.

```bash
chia job submit --working-dir . -- python discover-spec-property.py \
    --working-dir "$(pwd)"
```

## Files

| File | Description |
|---|---|
| `wishbone_ram.sv` | The DUT - a single-cycle, non-pipelined Wishbone-slave RAM. |
| `wbspec_b4.pdf` | The Wishbone B4 specification, attached natively to every spec-grounded LLM call (Gemini reads it directly, including tables/timing diagrams). |
| `wishbone_ram_checker.sv` | The current checker (whichever script last wrote it - re-derived from scratch each run, not hand-maintained). |
| `wishbone_ram_checker_2bugs.sv` | A saved checker snapshot from a run that found genuine spec violations. |
| `config.mcy`, `test_bmc.sh`, `test_bmc.sby` | Generated mutation-testing harness for `discover-covering-properties.py`'s coverage signal. |
| `wishbone_ram.sby`, `wishbone_ram_cover.sby` | Generated SymbiYosys job files. |
| `wishbone_properties.sva` | Empty - leftover from an earlier, unused OneSpin-based flow, not part of either CHIA loop here. |
