# `prod/eu-west-1/token-broker`

The production instance of [`../../../modules/token-broker`](../../../modules/token-broker):
the Brimyr GitHub App token broker, in AWS account **202518311296** (MagmaMoose prd),
region **eu-west-1**, behind **`https://broker-brimyr.magmamoose.com`**.

Same module `broker/localstack` applies. This leaf adds three things and nothing else: the
real provider, the real values, and `localstack = false`.

## Why this lives in the brimyr repo

It belongs in `magmamoose/infra`, and it is expected to move there.

infra has **no account dimension** today — every leaf sits under
`terraform/aws/prod/eu-west-1/<stack>` and runs against a single ambient credential, so
there is nowhere to express "apply this one into 202518311296 via the `mm-prd-brimyr` SSO
profile" (MagmaMoose/infra#641). Until that lands, keeping the leaf next to the module and
the Lambda source it deploys means the first apply is one reviewable change instead of a
cross-repo handshake.

The path deliberately mirrors infra's `<env>/<region>/<stack>` layout, so the move is
`git mv` plus a state migration rather than a rewrite.

## Prerequisites

| | |
|---|---|
| SSO session | `aws sso login --profile mm-prd-brimyr` |
| SSM seeded | `/brimyr/prod/app-id` and `/brimyr/prod/private-key`, both `SecureString`, **by hand** |
| ACM certificate | covers `broker-brimyr.magmamoose.com`, issued and validated **in eu-west-1** |
| Lambda zip | built for the runtime, not for your laptop |
| `tofu` ≥ 1.9 | Homebrew's `terraform` is pinned at 1.5.7 and fails `required_version` |

**The App private key is never in this repo, in any file, in any form.** It is seeded into
Parameter Store by hand precisely so it stays out of Terraform state — a secret handed to
a Terraform resource is a secret stored in plaintext in state. There is no
`aws_ssm_parameter` here and there must never be one. This stack only *reads* those two
parameters, through an IAM statement scoped to `/brimyr/prod` and `/brimyr/prod/*`.

```bash
# --overwrite on both: this is the path used for a key rotation and for re-seeding after
# a failed first attempt, and without it those exit ParameterAlreadyExists. There is no
# Terraform to fall back on here by design.
aws ssm put-parameter --profile mm-prd-brimyr --region eu-west-1 \
  --name /brimyr/prod/app-id --value '4124432' --type SecureString --overwrite
aws ssm put-parameter --profile mm-prd-brimyr --region eu-west-1 \
  --name /brimyr/prod/private-key --value file://<key>.pem --type SecureString --overwrite
```

## Apply

```bash
# 1. build the artifact — x86_64 Linux wheels, NOT the build machine's
cd broker && uv run --extra dev python scripts/build_lambda_zip.py \
  --out dist/broker.zip --platform x86_64-manylinux_2_28

# 2. plan, with the account guard doing its job if the profile is wrong
cd terraform/prod/eu-west-1/token-broker
AWS_PROFILE=mm-prd-brimyr tofu init
AWS_PROFILE=mm-prd-brimyr tofu plan \
  -var="lambda_zip_path=../../../../dist/broker.zip" \
  -var="certificate_arn=arn:aws:acm:eu-west-1:202518311296:certificate/<id>"
```

`allowed_account_ids = ["202518311296"]` in `main.tf` means a wrong or stale credential
fails while the provider is being configured — before anything is planned, let alone
created — and prints the account it actually reached. A public HTTP API and an IAM role
named `brimyr-broker` appearing in someone else's account is the failure that guard exists
to prevent.

State is **local** for now (`versions.tf` carries the TODO). It is gitignored; it holds no
secret, but it holds every resource id, so losing it means importing the stack back rather
than re-applying it.

## After the first apply — the closed-door check

```bash
curl -si "$(tofu output -raw execute_api_endpoint)/healthz" | head -1
# HTTP/2 404        <- REQUIRED
```

`execute_api_endpoint` **must return 404.** This is not a nicety. With a custom domain in
front, the generated `https://<id>.execute-api.eu-west-1.amazonaws.com` URL is a second
door into the same function that bypasses Cloudflare entirely, and it is the one an
attacker finds first. `localstack = false` plus a non-empty `domain_name` makes the module
set `disable_execute_api_endpoint = true`; the curl is the only thing that proves it took
effect. LocalStack cannot prove it — it does not implement the flag.

A `200` here means the stack is live on an unproxied endpoint. Treat it as an incident, not
a to-do.

Then check the real door, and the throttle LocalStack also could not prove:

```bash
curl -s https://broker-brimyr.magmamoose.com/healthz     # 200
curl -s https://broker-brimyr.magmamoose.com/readyz      # 200 once SSM is seeded, else 503
for i in $(seq 40); do curl -so /dev/null -w '%{http_code} ' \
  https://broker-brimyr.magmamoose.com/healthz; done     # 429s appear past ~5 rps
```

## Outputs

| Output | Use |
|---|---|
| `api_endpoint` | `https://broker-brimyr.magmamoose.com` — what `BRIMYR_BROKER_URL` points at |
| `execute_api_endpoint` | the URL that **must** 404; see above |
| `function_name` | `aws logs tail /aws/lambda/brimyr-broker --follow` when the smoke run goes red |

## Not owned here

- **DNS.** `broker-brimyr.magmamoose.com` is a Cloudflare-proxied CNAME to the API Gateway
  regional domain name. Cloudflare is not managed from this repo. The hostname is a
  **first-level** subdomain on purpose: free Universal SSL covers the apex and one label
  only, which is why nievah and caldrith are stuck (MagmaMoose/nievah#187,
  MagmaMoose/caldrith#68). Do not nest it.
- **The certificate.** Issued out of band; its ARN is a variable.
- **The App identity.** Seeded by hand, as above.
- **The DNS record.** Cloudflare-proxied CNAME from `broker-brimyr.magmamoose.com` to the
  `target_domain_name` output — the API Gateway regional target. **Not** to
  `execute_api_endpoint`: that is disabled on purpose, so the record would resolve, finish
  TLS at the Cloudflare edge, and 404 every request at the origin — which looks exactly
  like the "execute-api must return 404" check above passing.

The weekly smoke workflow is **not** outstanding: `.github/workflows/broker-smoke.yml`
ships in this same change, and `broker/README.md` step 5.4 runs it by hand at go-live. It
is the part that matters most, because the client fails soft — a dead broker is *silent*,
the PR byline quietly reverting to `github-actions[bot]` with no red check anywhere.
