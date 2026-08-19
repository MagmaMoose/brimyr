# PR comment

Brimyr can post the patch-coverage verdict as a comment on the pull request, so a
reviewer sees the number without opening the job log. It is **opt-in**
(`pr_comment: 'true'`), there is always **exactly one** comment per PR, and it is
**never** able to change the gate verdict.

The comment body is the same Markdown Brimyr writes to the GitHub job summary —
one renderer, so the two can never disagree about the run they are describing.

## What it looks like

A failing gate renders the number, the threshold, and the changed lines the tests
never executed — the only three things you need to know what to write a test for:

```markdown
## 🟣 Brimyr — patch coverage

**Mode:** `pr` · **Gate:** `fail` · **Ecosystem:** Python

| Metric | Value |
|--------|-------|
| Patch coverage | **72.0%** |
| Covered / changed executable lines | 18 / 25 |
| Threshold | 80.0% |

❌ **Patch coverage 72.0% is below the 80.0% threshold.** Uncovered changed lines:

- `src/app/billing.py` — 41, 42, 43, 57
- `src/app/invoices.py` — 12, 13
```

A passing gate collapses to a single line under the same table (`✅ Patch coverage
92.3% meets the 80.0% threshold.`), and a PR that changed nothing coverable says so
(`✅ No changed executable lines to cover — vacuous pass.`).

A **broken test run** replaces the table entirely with a blockquote saying the tests
failed or produced no coverage and that this is a tool error, **not** 0% patch
coverage — the same distinction the [gate itself makes](patch-coverage.md#why-a-broken-run-is-not-0).
Reporting a crashed suite as "0%" in a comment would be the most misleading thing
Brimyr could put on a PR.

Long lists are capped so the comment stays readable on a big refactor: at most 15
line numbers per file, at most 20 files, with a `… and N more file(s)` tail. If a
`sonar-scanner` run happened, its outcome is appended as a final `**SonarQube:**`
line.

## Exactly one comment, updated in place

The comment carries a hidden HTML marker:

```html
<!-- brimyr:pr-summary -->
```

Invisible in rendered Markdown, but it is how a later run finds the comment it
already owns. Every run lists the PR's comments, and if one carries the marker it
is `PATCH`ed; otherwise a new one is `POST`ed. So a PR that gets pushed to forty
times ends with one comment showing the current number, not forty comments and an
archaeology problem.

!!! warning "The marker is namespaced on purpose"
    It is `brimyr:pr-summary`, not a generic "our comment" match. Chargate comments
    on the same PRs; a looser match ("any comment we can parse") would have the two
    tools overwriting each other's summaries. One owner per surface.

The lookup is paginated and capped at 20 pages (2000 comments), so a pathological
thread cannot spin the job forever — past that cap Brimyr simply posts a new
comment rather than hanging.

## Wiring it up

```yaml
permissions:
  contents: read
  pull-requests: write     # ← required; the default `read` cannot comment

jobs:
  brimyr:
    runs-on: ubuntu-latest
    steps:
      # ... checkout with fetch-depth: 0, install your test deps ...
      - uses: magmamoose/brimyr@v1
        with:
          pr_comment: 'true'
          # github_token defaults to the job token → comments as github-actions[bot]
```

`github_token` defaults to the job's `GITHUB_TOKEN`. Point it at a different token
only if you want a different identity and are not using the broker below.

!!! note "Composite action only, today"
    The reusable workflow (`.github/workflows/gate.yml`) does not forward
    `pr_comment` or `token_broker_url`, and declares `pull-requests: read`. If you
    want the comment, use the composite action as above.

Outside a pull request there is nothing to comment on — on a push/baseline run the
step is a no-op, no matter what `pr_comment` says.

## It never fails the gate

A missing token, a `403` from a job with only `pull-requests: read`, a network
blip, GitHub being down — every one of them is reported and then dropped. The run
prints a line like

```
brimyr: PR comment updated as Brimyr[bot]
```

on stderr (suppressed by `--quiet`) and the exit code is whatever the coverage gate
decided. This is the same contract the [SonarQube step](setup.md#sonarqube)
follows: a comment is a convenience, and a convenience must never be able to turn a
green PR red.

The corollary is worth stating plainly, because it is the failure people actually
hit: **if you forget `pull-requests: write`, nothing goes red.** The comment just
never appears, and the reason is one line in the step log.

## Authoring as Brimyr[bot]

By default the comment is authored by `github-actions[bot]`, shared with every
other workflow in the repo. Set `token_broker_url` and it is authored by
**`Brimyr[bot]`** instead — its own identity and avatar, so the comment is
recognisable at a glance and distinct from Chargate's on the same PR.

```yaml
permissions:
  contents: read
  pull-requests: write
  id-token: write          # ← required; without it there is no OIDC token to exchange

# ...
      - uses: magmamoose/brimyr@v1
        with:
          pr_comment: 'true'
          token_broker_url: https://broker-brimyr.magmamoose.com
```

What happens inside that one job:

1. The job asks the Actions runtime for an **OIDC token** with `aud=brimyr`. The
   runtime only issues one when the job declares `id-token: write`, and the token
   is signed by GitHub — a runner cannot forge one, which is the entire basis of
   the design.
2. Brimyr `POST`s it to the broker along with the `owner`/`repo` being commented
   on.
3. The broker verifies the signature, the issuer, the audience, and that the
   token's own `repository` claim **matches the repo asked for** — then mints a
   GitHub App installation token scoped to that one repo with `pull_requests:
   write` and nothing else.

The minted token is short-lived, is handed straight to the comment client, and is
never printed, logged, written to a step output, or put in the job summary — not
even truncated.

!!! danger "It fails soft, and it fails silently"
    Every failure path — no OIDC token, broker down, App not installed, DNS gone —
    falls back to `GITHUB_TOKEN`. The comment still posts, the gate verdict is
    untouched, and **the only symptom is the byline reverting to
    `github-actions[bot]`**. Nothing turns red. Nobody reads a byline.

    That is the right trade — a broken broker must never cost anyone a merge — but
    it means the broker can be dead for months without anyone noticing. This is
    exactly why `.github/workflows/broker-smoke.yml` exists and is not optional: a
    weekly (Mondays, 07:23 UTC) real mint against the deployed broker, plus
    `workflow_dispatch` to re-arm it, because GitHub disables scheduled workflows
    on a repo after 60 days of inactivity and a silently disabled smoke test is
    exactly as useless as no smoke test.

When the mint fails, the reason is appended to that same stderr line — the byline
is the symptom, this is the diagnosis:

| Log message | What to fix |
| --- | --- |
| ``no Actions OIDC token — does the job declare `permissions: id-token: write`?`` | Add `id-token: write` to the job. By far the most common cause. |
| `broker returned 401 (invalid_oidc)` | The token failed signature/issuer/audience verification. |
| `broker returned 403 (repo_mismatch)` | The OIDC token's `repository` claim is not the repo the mint asked for. |
| `broker returned 403 (repo_not_allowed)` | The repo is not on the broker's allowlist. |
| `broker returned 403 (app_not_installed)` | The Brimyr GitHub App is not installed on that repo. |
| `broker returned 502 (mint_failed)` | GitHub refused the mint; check the App's private key and permissions. |
| `broker unreachable (URLError)` | DNS, TLS, or the broker being down. The exception *class name* is deliberate — nothing request-derived is logged, because the OIDC token travels in that request. |

Only that fixed vocabulary of broker error codes is ever echoed. A broker that
decided to reflect the request back could otherwise put the OIDC token straight
into your build log.

## The broker is a separate deployable

`broker/` in this repository is the AWS Lambda service that does the minting. It is
**not** part of the `brimyr` CLI: it has its own `pyproject.toml`, its own lockfile,
its own Ruff config and its own CI job, and nothing under `src/brimyr` imports
anything from it. The CLI stays stdlib-only and dependency-free; `broker_client.py`
speaks to the broker over HTTPS like any other stranger.

**You do not need to run one.** Leave `token_broker_url` unset and everything on
this page still works — only the byline differs.

If you do want to stand one up, its documentation lives with the code:

- **[`broker/README.md`](https://github.com/MagmaMoose/brimyr/blob/main/broker/README.md)** —
  architecture, the LocalStack loop (`make -C broker all`), what a local run can and
  cannot prove, and the full go-live runbook: seeding the App private key into SSM
  by hand, installing the App, applying the Terraform, and the post-apply checks.

That runbook is deliberately **not** duplicated here. It is operational detail tied
to specific account IDs, key fingerprints and Terraform outputs — it belongs next to
the Terraform it describes, where a change to one is a change to the other in the
same diff.
