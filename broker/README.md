# Brimyr token broker

Exchanges a GitHub Actions **OIDC token** for a **repo-scoped GitHub App installation
token**, so Brimyr's PR comment can be authored by `Brimyr[bot]` instead of
`github-actions[bot]`. Runs on AWS Lambda behind an API Gateway HTTP API.

Ported from chargate's broker (`feat/broker-on-aws-lambda`). The Python is a deliberate
copy, the Terraform is a shared-shaped module — see
[`.claude/decisions/0001-broker-python-is-copied-not-shared.md`](../.claude/decisions/0001-broker-python-is-copied-not-shared.md).

> This is a **separate deployable** from the `brimyr` CLI. The CLI is stdlib-only and
> dependency-free; nothing in `src/brimyr` imports anything here, and it never will.

## The local loop

```bash
make -C broker all      # up -> zip -> apply -> seed -> smoke
```

Needs Docker running, plus `tofu` (or terraform ≥ 1.9), `aws`, `uv` and `openssl`.
Individual steps: `up`, `zip`, `apply`, `seed`, `smoke`, `test`, `fmt`, `down`.

Nothing here touches real AWS — `localstack/provider.tf` points every endpoint at
`:4566` with LocalStack's documented fake credentials.

## What a local run proves, and what it can't

| Proven locally | How |
|---|---|
| The zip imports on the real runtime | Lambda cold-starts from the built artifact, python3.12 image |
| SSM + IAM + KMS wiring | `/readyz` is 503 unseeded, 200 after `seed.sh` |
| Routing, 404 vs 405, trailing slash | driven through API Gateway payload format 2.0 |
| The `/token` error ladder | `invalid_json` → `missing_fields` → `invalid_repository` → `invalid_oidc` |

**Not provable locally:** a successful mint. It needs a token signed by GitHub's Actions
OIDC key *and* a Brimyr App installed on the repo — LocalStack emulates AWS, not GitHub.

The issuer and JWKS URL in `app/config.py` are **not configurable on purpose**. Making
them overridable would let a local run "prove" a mint against a self-signed key, and
issuer pinning is exactly the control that stops anyone holding any valid Actions OIDC
token from minting. A local run that cannot fake it is the correct trade.

Also unproven locally: the stage throttle (LocalStack accepts but does not enforce it)
and `disable_execute_api_endpoint`. Verify both after the first real apply.

## Going live — what is still needed

Everything below is deliberately **not** committed. These are the values to supply.

1. **A `Brimyr` GitHub App** — its own App, never shared with chargate. Permissions:
   `pull_requests: write`, nothing else. Note the App ID and generate a private key.
2. **An AWS member account** for brimyr, as chargate / nievah / caldrith each have. The
   justification is blast-radius isolation and per-account SCPs, not Free Tier — Free
   Tier is pooled across the organization (MagmaMoose/infra#641).
3. **Seed SSM by hand** — never via Terraform, or the secret lands in Terraform state:
   ```bash
   aws ssm put-parameter --name /brimyr/prod/app-id      --value '<APP_ID>'    --type SecureString
   aws ssm put-parameter --name /brimyr/prod/private-key --value file://key.pem --type SecureString
   ```
4. **A real leaf** calling `terraform/modules/token-broker` with `localstack = false`,
   `secret_path = "/brimyr/prod"`, and
   `domain_name = "broker-brimyr.magmamoose.com"` — **first-level subdomain**, since
   Cloudflare's free Universal SSL covers the apex and one label only.
   `broker.brimyr.magmamoose.com` cannot be proxied; nievah and caldrith are both stuck
   two labels deep and need renames (MagmaMoose/nievah#187, MagmaMoose/caldrith#68).
   This depends on `magmamoose/infra` growing an account dimension — every leaf currently
   sits under `terraform/aws/prod/eu-west-1/` with one ambient credential.
5. **After the first apply**, curl the `execute_api_endpoint` output. It must return
   **403**. That is the only check proving the custom domain is the sole door.
6. **A weekly smoke workflow** against the deployed broker, shipped at the same time and
   not later. The client fails soft by design, so a broken broker is *silent* — a byline
   quietly reverting to `github-actions[bot]` and no red check anywhere. The smoke run is
   the only thing that notices.

Until all of that exists, Brimyr's comment posts as `github-actions[bot]`, which is a
working product — see MagmaMoose/brimyr#13.
