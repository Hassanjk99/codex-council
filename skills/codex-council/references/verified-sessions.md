# Verified sessions in this fork

This fork adds input identity and recorded evidence. It does not attest model
truth, authenticate imported host observations, or replace project test policy.
No command in this workflow posts to GitHub or changes a project status.

## Prepare and freeze inputs

Create a normal session with the accepted estimate. Finish writing the actual
dispatch prompts and brief before binding. Save the required acceptance criteria
as JSON. Review completion and implementation acceptance are different goals.

```json
{
  "goal": "review",
  "criteria": [
    {"id": "owner-path", "description": "Trace the affected owner and callers", "environment": "local"},
    {"id": "tests", "description": "Run the relevant local tests", "environment": "local"}
  ]
}
```

From the plugin checkout, bind the session to the exact selected working files:

```bash
python3 scripts/codex_council.py bind-inputs --session /absolute/session \
  --include src/affected-feature --instruction openspec/specs/affected/spec.md \
  --requirements /absolute/requirements.json
```

Repeat `--include` and `--instruction` as needed. A directory scope detects new
non-ignored files as well as edits and deletions. An exact file scope covers only
that file. Git HEAD and selected index entries are tracked. Ignored files are
outside the source inventory; explicitly supplied instructions are still hashed.
Credentials, symlinks, nested repositories and nonregular selected files are not
supported source inputs. Select a narrower scope when necessary. No source files
are copied, and nothing is written inside the analyzed repository.

Binding is an observed-stable read, not an atomic snapshot of concurrent writers.
Pause overlapping writers for binding. A session is bound once. Changed source,
context, requirements, instructions or dispatch prompts need a new session.
Instructions are bundled in `repository-instructions.md`; every saved dispatch
prompt tells the recipient to read that bundle. Actually send the saved prompt
and make the bundle available to each participant. A path reference is not proof
that an agent read it.

For implementation, retain the original design/review session as historical
evidence. After the authorized edits, freeze the final working tree and create a
new verification session before running final checks. Do not relabel pre-edit
checks as verification of the final code.

## Truey profile

Add `--repository-profile truey` when analyzing a Truey repository. It requires
and bundles the checkout's AGENTS.md, DOCTRINE.md, TESTING.md, OpenSpec guidance,
and effective-coder, unslop, codex-review and verify-changes skills. Missing files
are errors. Include all applicable nested instructions and explicitly select the
approved specs and additional domain skills with `--instruction`, for example
DiffFlow guidance for engine changes. The profile does not infer contracts, copy
skills into Truey, or authorize new contracts or external writes.

Repository instructions remain authoritative. One owner controls the shared
browser, account, server and build resources. Keep manual browser acceptance,
local checks, hosted CI and deployment evidence separate. Follow the current
repository's video, fixture cleanup, review and OpenSpec requirements.

## Bind a PR

An optional `--pr /absolute/pr.json` takes:

```json
{"url":"https://github.com/owner/repo/pull/123","base":"FULL_BASE_SHA","head":"FULL_HEAD_SHA"}
```

Supply real full hexadecimal SHAs. Resolve these from the actual current PR;
this utility does not query GitHub. For every freshness, completion or handoff
check of a PR-bound session, supply `--current-pr-head CURRENT_FULL_HEAD_SHA`
from a fresh GitHub read. Without it applicability is unknown; a different head
makes the result stale. Local dirty-file checks never establish hosted PR CI.

```bash
python3 scripts/codex_council.py freshness --session /absolute/session
```

Exit 0 means unchanged captured inputs, exit 1 means stale or unknown, and exit 2
means invalid command input. External dependencies and live deployment state are
not rechecked by local hashing.

## Collect evidence

The local command runner records argv, observed exit code and output artifact:

```bash
python3 scripts/codex_council.py run-check --session /absolute/session \
  --criterion tests -- python3 -m unittest discover -s tests -q
```

The criterion must already exist with environment `local`. The command executes
in the captured workspace with the host's current permissions and environment;
this is not a sandbox. Run only authorized commands. Nonzero exits are failures.
If a command changes captured inputs, subsequent validation becomes stale.

Native host tools, browser tools and CI connectors can supply records using
`record-evidence --session /absolute/session --input /absolute/observation.json`:

```json
{
  "kind": "criterion",
  "id": "owner-path",
  "result": "PASS",
  "environment": "local",
  "observation": "host-tool",
  "summary": "Read owner and callers at the captured input version",
  "artifacts": ["observations/owner-path.txt"]
}
```

Save real tool observations at session-relative artifact paths first. Evidence
hashes detect subsequent edits. Imports are explicitly host-supplied observations,
not independently attested executions. `model-claim` records are retained but
cannot satisfy completion. Avoid secrets and unnecessary raw logs.

For each entry in `input-manifest.json.required_contributions`, record a
`contribution` with that exact path as its `id`, environment `local`, and include
the actual output path in `artifacts`. This includes members, reviewers, browser
runners when selected, and final synthesis. Agent names and transport success do
not count as completion. Unfilled heading-only scaffolds do not count either.

Criterion environments are `local`, `ci`, `deployed`, and `browser`. CI evidence
requires a PR-bound session and matching `pr_head`. Deployed and browser criteria
must declare an expected `target_url`; evidence must match it. Deployed criteria
and browser criteria for hosted targets need an expected
`deployment_revision` matched by the evidence to record PASS. If it is unavailable,
retain UNKNOWN evidence and report acceptance as incomplete. For localhost browser checks no
deployment revision is required, and the result establishes localhost coverage
only. Use UNKNOWN when the required revision cannot be established.

Record interrupted, blocked or incomplete work as UNKNOWN and observed failures
as FAIL. Required FAIL or UNKNOWN prevents completion. A later PASS cannot erase
a recorded failure in the same session; use a new session with fresh evidence.

## Validate and hand off

```bash
python3 scripts/codex_council.py validate-completion --session /absolute/session
python3 scripts/codex_council.py handoff --session /absolute/session
```

Both run structural validation, input freshness, contribution coverage and
criterion checks. They return JSON and exit 1 for incomplete results. Handoff
adds PR identity and explicitly reports no posting. Review completion says the
review and its required checks are recorded; it does not approve implementation,
prove a deployment, move an issue to QA, or establish product acceptance.

`validate-session` retains its upstream meaning: valid artifact structure,
including newly created scaffolds. `doctor` and token estimates also retain their
upstream meanings. Use `validate-completion` for the new completion contract.
