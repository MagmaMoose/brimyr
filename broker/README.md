# Brimyr token broker

Exchanges a GitHub Actions **OIDC token** for a **repo-scoped GitHub App installation
token**, so Brimyr's PR comment can be authored by `Brimyr[bot]` instead of
`github-actions[bot]`. Runs on AWS Lambda behind an API Gateway HTTP API.

Ported from chargate's broker (`feat/broker-on-aws-lambda`). The Python is a deliberate
copy, the Terraform is a shared-shaped module — see
[`.claude/decisions/0001-broker-python-is-copied-not-shared.md`](../.claude/decisions/0001-broker-python-is-copied-not-shared.md).

> This is a **separate deployable** from the `brimyr` CLI. The CLI is stdlib-only and
> dependency-free; nothing in `src/brimyr` imports anything here, and it never will.

## Cost — read before changing any infrastructure setting

**This bill is paid out of one person's salary.** Every setting in the Terraform is a
spend control. Default to the cheapest thing that works, and if you raise a limit, write
the new worst case down next to it.

**There is no hard spend cap in AWS.** A budget *alarms*; it does not stop anything. On a
public, unauthenticated endpoint the API Gateway stage throttle is the only real cap.

This account is **not** in a 12-month free-tier window — eligibility dates from the
organization's management account, so a member account created later starts with expired
allowances. What still applies:

| Always free, never expires | Billed from the first unit |
|---|---|
| Lambda 1M requests + 400,000 GB-s/month | API Gateway HTTP API, $1.00/million |
| CloudWatch Logs 5 GB ingestion | S3 storage (~$0.00013/mo for one zip) |
| SSM Parameter Store Standard, public ACM certs, SNS first 1,000 emails, first 2 Budgets | |

Worst case if the endpoint were hammered continuously for a month:

| | requests/mo | API GW | Lambda | logs | total |
|---|---|---|---|---|---|
| module defaults (2 rps, 1024 MB) | 5.18M | $5.18 | $11.45 | $0.18 | **$16.81** |
| brimyr's settings (1 rps, 512 MB) | 2.59M | $2.59 | $0.32 | $0.00 | **$2.91** |

At realistic use — a few hundred requests a month — it is fractions of a cent.

Two settings do most of the work. `memory_size` above 512 MB pushes Lambda compute out of
the always-free 400,000 GB-s under load; `throttle_rate_limit` raises the ceiling linearly.
Neither needs raising: real traffic is a handful of requests per pull request.

One more: the hostname must stay a **first-level** subdomain. Cloudflare's free Universal
SSL covers the apex and one label, so `broker-brimyr.magmamoose.com` is free while
`broker.brimyr.magmamoose.com` would need Advanced Certificate Manager at ~$10/month.

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

## Going live

The identity now exists. What is recorded here is non-secret; the private key is not, and
never will be, in this repo.

| | |
|---|---|
| AWS account | `202518311296` |
| Region | `eu-west-1` |
| SSO profile | `mm-prd-brimyr` (portal `https://magmamoose.awsapps.com/start/#`) |
| GitHub App ID | `4124432` |
| App private key | **not in this repo.** 2048-bit PKCS#1, `MD5(DER) = 67:b3:e5:7b:90:4e:b1:fd:01:d8:1f:b3:7f:67:fb:7b` — compare against the App settings page before seeding |
| SSM path | `/brimyr/prod` → `app-id`, `private-key`, both `SecureString` |
| Hostname | `broker-brimyr.magmamoose.com` (first-level, so Cloudflare can proxy it) |

### 1. Sign in

```bash
aws sso login --profile mm-prd-brimyr
aws sts get-caller-identity --profile mm-prd-brimyr   # expect Account: 202518311296
```

### 2. Seed the secrets BY HAND

Never through Terraform — a secret in a resource is a secret in state, and the infra repo
is public. Run these from the directory holding the key, and delete it afterwards.

```bash
aws ssm put-parameter --profile mm-prd-brimyr --region eu-west-1 \
  --name /brimyr/prod/app-id --value '4124432' --type SecureString --overwrite

aws ssm put-parameter --profile mm-prd-brimyr --region eu-west-1 \
  --name /brimyr/prod/private-key \
  --value "file://brimyr.2026-08-19.private-key.pem" --type SecureString --overwrite
```

Verify without printing the key:

```bash
aws ssm get-parameters-by-path --profile mm-prd-brimyr --region eu-west-1 \
  --path /brimyr/prod --recursive --with-decryption \
  --query 'Parameters[].{name:Name,bytes:length(Value)}' --output table
```

### 3. Install the App on the repos it comments on

`pull_requests: write`, nothing else. The installation **is** the allowlist — the module
leaves `allowed_repositories` empty on purpose, so a repo the App is not installed on gets
`app_not_installed` rather than a token.

### 4. Apply

`broker/terraform/prod/eu-west-1/token-broker/` instantiates the module for real. It
carries an `allowed_account_ids` guard, so an apply under the wrong profile fails before
creating anything.

### 5. Then verify, in this order

1. `curl -s -o /dev/null -w '%{http_code}' "$(tofu output -raw execute_api_endpoint)"` →
   **404**. AWS answers a disabled execute-api endpoint with 404, not the 403 the
   phrasing everywhere suggests — verified against both brimyr's and chargate's live
   endpoints. It is still the only proof the custom domain is the sole door; confirm the
   cause with `aws apigatewayv2 get-api --api-id <id> --query DisableExecuteApiEndpoint`.
2. `GET https://broker-brimyr.magmamoose.com/healthz` → 200.
3. `GET https://broker-brimyr.magmamoose.com/readyz` → 200. A 503 means the SSM
   parameters, the IAM statement or the KMS grant are wrong.
4. Run `.github/workflows/broker-smoke.yml` by hand. It is the only thing that ever
   notices this breaking — see below.
5. Set `token_broker_url` on the action and confirm a PR comment is authored by
   `Brimyr[bot]`. The calling job needs `permissions: id-token: write`.

### Why the smoke workflow is not optional

`brimyr.broker_client` fails **soft and silent**: a broken broker means the byline quietly
reverts to `github-actions[bot]`. No check goes red, no comment is lost, nobody notices.
The weekly smoke run is the entire detection mechanism.

### Still outstanding

- The ACM certificate for `broker-brimyr.magmamoose.com` is issued out of band; the leaf
  takes its ARN as a variable rather than inventing one. It must be an **eu-west-1**
  certificate in account `202518311296` — a *regional* API Gateway custom domain takes a
  cert from its own region, and the us-east-1 rule people remember is CloudFront's.
- The Cloudflare DNS record is created out of band: a **proxied CNAME** from
  `broker-brimyr.magmamoose.com` to the leaf's `target_domain_name` output
  (`d-xxxx.execute-api.eu-west-1.amazonaws.com`). Not `api_endpoint`, which is the name
  being created, and **not** `execute_api_endpoint` — that one is disabled on purpose, so
  pointing at it resolves, completes TLS at the edge, then 404s every request while
  looking like the security check passing.
- `magmamoose/infra` still has no account dimension — every leaf sits under
  `terraform/aws/prod/eu-west-1/` with one ambient credential. Until that lands this leaf
  lives here, and moving it is the eventual tidy-up.
