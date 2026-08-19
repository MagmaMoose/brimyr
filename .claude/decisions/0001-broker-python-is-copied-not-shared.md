# 0001 — The broker's Python is copied from chargate, not shared

**Status:** accepted · **Date:** 2026-08-19 · **Context:** MagmaMoose/brimyr#11

## The decision that was asked for

brimyr#11 names this explicitly and refuses to pre-decide it:

> The Python is the harder call: a shared package means a release train between two repos,
> which is real cost for ~600 lines. Copying means drift. […] **Decide deliberately and
> write it down.**

**Decision: copy.** `broker/app/` is a port of chargate's `feat/broker-on-aws-lambda`, not a
dependency on it. The Terraform, by contrast, **is** shared-shaped: `broker/terraform/modules/
token-broker` is parameterised so chargate can instantiate the same module rather than fork it.

## Why copy the Python

- **A shared package needs a release train.** chargate and brimyr release independently
  (python-semantic-release, per repo, on their own cadences). A shared `magmamoose-broker`
  distribution means a broker change is a publish, a version bump in two repos, and two
  releases — for ~600 lines that change roughly never. `standard` is skipped from the org's
  managed release workflow precisely because multi-package release trains are painful; this
  would add another.
- **The drift risk is bounded by tests, not by hope.** Both repos carry the same suite
  (`tests/test_broker.py`, `test_lambda_handler.py`, `test_ssm.py`, `test_config.py`) and the
  same package-contract check on the built zip. A copy that drifts in a way that matters
  fails those on the side that drifted.
- **This is not security-critical *logic* diverging.** The security controls — issuer pin,
  audience check, `repository`-claim match, `validate_repository` — are each pinned by a named
  test on both sides. A copy is defensible here in a way it would not be for, say, a signature
  verifier with one canonical implementation.

## Why the Terraform is shared-shaped anyway

The module body contains nothing brimyr-specific: name prefix, audience, SSM path, GitHub API
URL, permissions, domain and throttles are all variables. This is the "second instance = the
extraction point" the issue calls for — parameterise before the fork exists, not after.

## What must NOT be shared

**Not one broker for both services.** Two Lambdas, two GitHub Apps, two SSM paths, two
audiences. A single minter holding both Apps' private keys means compromising either surface
yields both identities, and the `aud` check stops being a boundary at all. Two functions at
~$0 each is the cheaper failure mode. The IAM policy scopes SSM reads to the service's own
prefix so this holds even if both ever run in one account.

## Consequences

- A fix to `app/oidc.py`, `app/github.py` or `app/ssm.py` must be applied in **both** repos.
  Cross-reference the commit in both messages so the pair is findable later.
- If a third service ever needs a broker, revisit: three copies is where the arithmetic flips.
- The port is deliberately faithful. Where a docstring described chargate's history (the
  Cloudflare Worker, the k8s deployment, removing pydantic), it was rewritten to describe
  brimyr's actual situation and attribute the origin — inherited comments asserting a history
  this repo never had would be worse than no comment.

See also [[release-is-org-managed]].
