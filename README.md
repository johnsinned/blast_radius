# Blast Radius dependency selector

This entry builds a dependency map from the unmodified, pinned sqlglot checkout.
At runtime it selects tests using changed-line coverage, fixture-file reads,
Python statement boundaries, and class/attribute usage. It checks Python ASTs
before skipping comment edits, preserves observed source-read dependencies, and
falls back to the full suite when evidence is missing or the patch is structural.
It tries all worker counts from 1 through 8 using integer-millisecond costs.

No sample answers, patch hashes, or tables of edit kinds are used by the selector
or map builder. Sample answers are read only by development scoring tools.
The submitted image has only the selector and its helpers, the baseline map,
and pinned source.

On the 30 downloaded sample commits, the original starter scores **1,210.470 s**
with zero misses; the previous submission scored **105.688 s** on these samples.
The current candidate extends instruction coverage to edits that move bytecode
offsets, using checked control-flow alignment and cached pristine-file analysis.
It scores **99.549 s simulated** with zero sample misses. Under the competition's
Docker restrictions it used **1.569 s Python CPU**, approximately **101.118 s total**.
This includes a small CPU measurement wrapper. The repository remains below the
20 MB limit.

The latest submitted commit, `b1daa8be79a0dda5f10ad80340d520d4490333ed`, scored
**168.2 s** on the hidden practice batch, as reported by the team. Its simulated
and CPU breakdown was not supplied. The current candidate has not been scored
on that hidden batch; its sample score cannot establish its hidden score.

The clean Linux baseline passed 1,620 tests and 25,440 subtests, with one expected
SQLite skip and process-pool serialization included. Setting reads were measured
in a separate passing Linux run. Matching uses pytest IDs rather than collection
order. Bounded primitive fixtures retained from the earlier Windows baseline are
identified separately in the map provenance.

The map records operand types and bounded primitive input fixtures at comparison
sites, plus reads of individual keys in static configuration dictionaries. At
runtime, supported comparison changes are interpreted against those original
inputs. A value-only dictionary edit selects readers of the changed entries and
any whole-dictionary consumers. Changes to keys or nonliteral initializers keep
the ordinary dependency selection. Import-time value dependencies disable this
pruning. Genuine source inspection is retained; importer source reads are excluded.

Literal class-setting edits additionally use actual inherited attribute reads,
dynamic getattr/vars accesses, class namespace consumers, and direct name reads.
Unrecognized metaclass namespace handling or import-time reads disable this
pruning. Function-body edits can avoid file-wide fallback when the function was
never called; signatures, docstrings, code flags, local/closure layouts, and
import-time calls are checked first. Custom equality is used only where the
original loaded method has a conservative short-circuit proof against None,
with live method/global guards. Custom inequality also checks for instance-level
equality overrides when objects have dictionaries. All equality definitions in
the pinned proof files are protected against patch changes, allowing unrelated
methods in the same file to use the proof. Identical type summaries are merged;
distinct enum class identities remain distinct.

Integer comparisons against literal bounds additionally record enclosing min/max
ranges per test. Runtime checks require predicate agreement over every integer
in those ranges, including values never observed. Noninteger or oversized values
and import-time dependencies disable this pruning. This avoids a dependence on
the bounded primitive fixture limit. Compilation of unused-body metadata is
skipped when a function was executed, because that case cannot prune tests.

The instruction pass observes pristine CPython execution without rewriting
source or bytecode. For edits within existing function bodies, the runtime can
intersect the ordinary selection with tests entering blocks containing changed
instructions. Every instruction within an entered block is treated as executed.
Interpreter version, original-code fingerprint, function metadata, local/closure
layout, instruction offsets, exception tables, stack size, and import-time
execution are guarded. Unsupported changes retain the ordinary selection.
Earlier jump changes caused by operator precedence are included in the diff.
The static index includes functions whose definitions execute but whose bodies do
not. Source readers and tests lacking complete profiles remain selected.

When offsets move, a sequence alignment proposes equivalent instructions, and
both outgoing jump and fallthrough edges must map to the exact corresponding
successor. Removed instructions and old predecessors of inserted/redirected paths
become dependencies; a changed entry retains entry-block callers. Literal pool
indices and values are both compared, and name-load flags are retained. This
shifted-layout path requires unchanged stack/local/closure metadata, no exception
tables or generator/coroutine flags, and at most 4,000 instructions. Unsupported
cases retain the existing strict-offset check or ordinary selection. Original
ASTs and compiled code are reused only for identical source text; source hashes
are still checked on every patch. Changed function bodies are matched once per
patch rather than once for each refinement layer.

Pure factory calls in module/class initialization additionally track consumers of
the returned function, distinguishing closures by their actual captured objects.
The factory must consist only of a nested function definition and its return;
callback metadata must not depend on factory inputs. All creations require
matching observed returns, and unknown closure environments are retained
conservatively. Factory source spans are protected. Literal argument edits can
then select only callback users, including entries within inherited dispatch
dictionaries. Import-time callback consumers disable this pruning.

Expression `arg_types` boolean values have one special derived consumer: the
initializer constructs `required_args`, and the validator checks those keys.
A separate pristine pass records invalid or unknown argument inputs per test,
using actual dictionary identity to include inheritance. Only None and empty
plain lists count as missing. List subclasses, modified metadata, raw dictionary
consumers, import-time missing arguments, and multiple-error ordering retain
dependencies. Dictionary copies/spreads disable this refinement for their source.
The initializer and validator are fingerprinted, their direct derived-set uses
are checked against pinned source, and their source spans are protected.

A native filesystem trace includes children and C-level file operations, with
independent Python-open records and source hashes. Existing files with no content
or sensitive metadata consumers can skip content edits; existence and file-type
checks are allowed only when their counts match native pathname checks exactly.
Mode, add/delete, and rename changes retain fallback. SQL fixture pruning requires
the exact original helper and filter, matching native/Python open counts, and
identical filtered input. Only empty lines and lines beginning with `--` disappear;
metadata, inline comments, and SQL content remain significant. Diff hunk contents
that resemble file headers are now parsed correctly.

The entire AST edit must be covered by these checks, and the commit must change
one file. Missing profiles, oversized or nonprimitive inputs, overloaded methods,
uncertain identity/interning, changed operand computations, and unrelated edits
retain the ordinary dependency selection. Original alias observations are not
assumed stable between interpreter runs.

No changed operation is evaluated or scored during map collection. Original
values and reads are recorded; the incoming patch is interpreted only at runtime.
No patch-to-answer or edit-kind-to-failure table is stored. Evidence is empirical
and depends on the test environment. The map is collected from the pinned source;
an independent baseline pass verifies dictionary/file reads without predicate
instrumentation. All **107 focused regression tests** pass in the pinned Linux image,
including 64,800 interval cases checked against direct enumeration.
Three fresh boundary mutations were also checked against real time-format tests;
the selector retained all failures found in that bounded validation.
Five additional fresh mutations were checked against the complete Linux suite:
three setting changes produced 9, 3, and 6 failing numbered tests respectively,
all retained; a None-equality change and an unused public function-body change
produced no failures. These development results are not embedded in the map.
Two further fresh inequality changes, in expression hashing and table generation,
passed the complete Linux suite. The old selector chose batches costing 25.656 s
and 27.454 s for those edits; the current candidate chooses zero tests. These
bounded examples do not predict the hidden batch's composition or score.
Five fresh changes validate the instruction/callback layer against the complete
Linux suite: the parser change selected all 4 failures from a former selection
of 1,334 tests; the generator change retained all 3 failures; a used callback
retained both failures; the optimizer and unused-callback changes had no failures.
No validation results or mutant outputs are consumed by the selector.
Five further fresh edits validate the file/validator layer against the complete
Linux suite: the unused entrypoint, filtered fixture comment, and Column flag
produced no failures and selected no tests. Making Table's `this` argument required
selected 22 tests and retained all 20 failures, down from 929 selected tests.
Making Order's `expressions` argument optional selected its one actual failing
test, down from 174 selected tests. All five checks had zero missed failures.
Four more fresh edits validate control-flow alignment against the complete Linux
suite. Removing a parser fallback reduced selection from 515 to 30 tests and had
no failures. Removing a generator condition retained its one failure among 106
tests, down from 358; a tokenizer condition retained both failures among 6 tests,
down from 842. Adding a parser short-circuit condition selected 25 tests and had
no failures. The first three selections save 21.780, 16.568, and 33.059 simulated
seconds respectively. All four checks had zero missed failures. Separate opcode
trace checks verify an executed divergence witness for more than 1,000 changed
outcomes across short-circuit, conditional, call, and comparison expressions.

The default Docker entrypoint has been tested as nobody, without a network, with
a read-only filesystem, 64 MB temporary directory, 2 CPUs, and the scorer's memory
and process limits. Ordinary coverage and the new setting/type observations use
the pinned Linux/Python 3.13 environment; regeneration is available below.

## Run locally without Docker

The pinned source checkout is already in `.work/sqlglot` on this machine.
With Python 3.13 or newer, run:

```sh
python tools/evaluate.py --verbose
python -m unittest discover -s tests -v
```

Instruction profiles require a matching interpreter. Use Docker to reproduce
the reported Linux/Python 3.13 score; other versions keep the ordinary fallback.

To recreate the source checkout on a different machine:

```sh
git clone --filter=blob:none --no-checkout https://github.com/tobymao/sqlglot.git .work/sqlglot
git -C .work/sqlglot checkout eb11aec4be8cb2122afe713a4816662c3f0fb2fa
```

The local score is the exact simulated cost for the sample set. Wall time is
reported separately; it is not the CPU time the server adds to your score.

## Docker validation and Linux map regeneration

`make sample` still builds and runs under the original scorer restrictions.
The runtime image installs no testing dependencies and downloads only the
permitted sqlglot archive at the pinned commit.

On Windows, make is optional. The Docker helper finds a per-user Docker Desktop
installation and measures the sample under the scorer's restrictions:

```sh
python tools/docker_sample.py
python tools/docker_sample.py --skip-build --entrypoint
```

The first map was collected on Windows with Python 3.13. A separate build recipe
regenerates it inside the scorer's pinned Linux image and package environment:

```sh
docker build --platform linux/amd64 -f Dockerfile.maps --target export --output type=local,dest=maps .
make sample
```

Regenerating the map is a development step. The normal submission build copies
the committed JSON map and does not rerun the test suite. Review the baseline
reports in the map's `provenance` field after regeneration. Coverage is empirical;
zero misses on samples does not establish zero misses on unseen commits.

To collect a map directly in a Python environment containing the packages in
`requirements.lock` plus `coverage==7.13.4`:

```sh
python tools/collect_map.py .work/sqlglot
python tools/compile_map.py .work/sqlglot .work/raw-map.json
python tools/collect_map.py .work/sqlglot --reads-only --output .work/reads.json
python tools/apply_read_map.py maps/dependencies.json .work/reads.json
python tools/collect_map.py .work/sqlglot --attributes-only --output .work/attributes.json
python tools/apply_attribute_map.py maps/dependencies.json .work/attributes.json
python tools/collect_map.py .work/sqlglot --types-only --ranges-only --output .work/semantic.json
python tools/apply_type_map.py maps/dependencies.json .work/semantic.json
python tools/apply_range_map.py maps/dependencies.json .work/semantic.json
python tools/attach_proof_spans.py maps/dependencies.json .work/sqlglot
python tools/compact_profiles.py maps/dependencies.json
python tools/collect_map.py .work/sqlglot --blocks-only --output .work/blocks.json
python tools/apply_block_map.py maps/dependencies.json .work/blocks.json
python tools/complete_static_codes.py maps/dependencies.json .work/sqlglot
python tools/collect_map.py .work/sqlglot --factories-only --output .work/factories.json
python tools/apply_factory_map.py maps/dependencies.json .work/factories.json
python tools/collect_map.py .work/sqlglot --requirements-only --output .work/requirements.json
python tools/apply_requirement_map.py maps/dependencies.json .work/requirements.json
```

Native filesystem evidence is collected separately, with no tracer in the
submission image. Using a shell with Docker bind-mount support, create `.work/linux`
and run:

```sh
docker build -f Dockerfile.maps --target environment -t blast-radius-map-environment .
docker build -f Dockerfile.files -t blast-radius-file-environment .
docker run --rm --network none -v "$PWD:/project:ro" -v "$PWD/.work/linux:/results" blast-radius-file-environment python /project/tools/profile_filesystem.py /opt/sqlglot /results/filesystem
python tools/apply_filesystem_map.py maps/dependencies.json .work/linux/filesystem
```

The collection plugin executes process-pool jobs synchronously with input/output
pickle round trips, so both execution and serialization are recorded under the
correct test context. It does not alter files in sqlglot. On Python 3.12 or newer
it records each executed line once per context with `sys.monitoring`, including
function entries to capture one-line lambdas. Older Python uses coverage.py.
Full fixture collection can take substantially longer than the uninstrumented
suite; it is a development step and is not performed during submission builds.
The `.work` and `.venv` directories are excluded from both git and Docker.

---

## Original starter instructions

A working submission for the Blast Radius track. It runs every test on every commit, so it never misses a failure and scores badly. Your job is to run less without missing.

## The problem

Your image gets a batch of code changes to [sqlglot](https://github.com/tobymao/sqlglot) at commit `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. For each change, print which tests to run and how to split them across up to 8 workers.

| | |
| --- | --- |
| `/data/commits/<id>.patch` | One unified diff against the pinned commit. Each stands alone: none builds on another. |
| `/data/tests.json` | Every test, numbered, with its file and the seconds it took us to run. |
| stdout | One JSON line a commit: `{"commit": "k7q2", "workers": [[12, 845, 901], [3, 77]]}` |

Each inner list is one worker's test numbers. `"workers": []` runs nothing, which is the right answer for a change that breaks nothing.

## Scoring

Lower is better. [SCORING.md](SCORING.md) has the exact rules; in short:

- A commit costs the recorded seconds of its busiest worker, plus 2 seconds for every worker.
- A commit where you left out a test that fails adds 300 seconds.
- The CPU seconds your program uses are added. Running sqlglot's tests while you decide is allowed, and you pay for the time.
- Your image runs 3 times and the middle total counts.

"Fails" means pytest reports a failure, an error or a timeout for the test, or the test's file cannot be imported. A test with subtests is one test: one failing subtest fails it.

## Try it

You need Docker, make and Python 3.9 or newer. The kit unpacks into the folder you are in, so unpack it into an empty one.

```sh
make sample   # build, run once on the 30 sample commits under the scorer's limits, and score
make check    # the output's format only
```

`make sample` prints your simulated seconds and misses, which are exact for that output. The board adds the CPU seconds your container used and takes the middle of 3 runs, so expect it a little higher. Your program never sees `sample/answers.json`: only the patches and `tests.json` are mounted.

`sample/answers.json` lists the tests each sample commit breaks and the kind of change it is. The practice and final commits are hidden, and so is everything your program prints when it runs on them. A rejected submission tells you which rule it broke and nothing more, so reproduce problems here.

## Submit

Push to a public GitHub repository with the `Dockerfile` at its top, then enter the repository and the commit hash on the track page. The final scores the commit you pick. If you pick none, your best practice submission counts.

## Rules

- Submit your own team's repository. A submission with the same repository, commit or archive as another team's is disqualified, the later one.
- Your build may download only from public package registries: PyPI, npm, Docker Hub, GitHub releases at a fixed tag, and sqlglot’s own repository at the pinned commit. Nothing from an address your team controls. The final rebuilds every image after the cutoff, so anything else could change a submission after the deadline.
- No answers worked out ahead of time and looked up by patch: no table from a patch, or from a kind of edit, to the tests it breaks. Maps built from the pinned commit (coverage, imports, fixtures) are allowed. Top entries are read.
- Only the final counts. The practice board carries no prize.

## How your image runs

- The build has the network, 4 cores, 8 GB of memory and 10 minutes. It may clone sqlglot at the pinned commit, install it and run its tests to make coverage or import maps.
- Your repository may hold 20 MB. Git submodules and Git LFS files are not fetched: clone sqlglot in the `Dockerfile` and commit maps as plain files.
- The run has no network, 2 cores, 4 GB of memory and 3 minutes for the whole batch (4.5 in the final, which has about one and a half times as many commits).
- The disk is read-only. `/tmp` holds 64 MB and is empty at the start of every run.
- The container runs as user `nobody`, with `HOME=/tmp` and at most 256 processes.
- stdout may hold 2 MB. More rejects the run.

## Things that will catch you

- Logs go to stderr. Any line on stdout that is not an answer rejects the run.
- `score.py` names a broken rule by its code, as in `Rejected: not_json on line 1`. The table in SCORING.md says what each code means.
- Files your program needs must be readable by `nobody`. A map written by `root` during the build with mode 600 is not.
- pytest 9 prints a test as `PASSED` beside `SUBFAILED` lines when one of its subtests fails. That test failed.
- sqlglot skips its executor tests unless `SKIP_INTEGRATION=0` is set. We labeled with it set, so those tests can fail.
- `requirements.lock` is every package in the environment we labeled in, on the image the `Dockerfile` starts from. To run sqlglot's tests the way we did, `pip install -r requirements.lock` and put the sqlglot checkout on `PYTHONPATH`; sqlglot's own `pip install -e .[dev]` picks newer versions.
- The scorer is linux/amd64. On an Apple Silicon Mac, build and run with `--platform linux/amd64`, as the Makefile does, or your timings and wheels will differ.
- A change that only adds a comment is not promised to be harmless.
