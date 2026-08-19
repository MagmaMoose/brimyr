# The Brimyr token broker in the real account: MagmaMoose prd, 202518311296, eu-west-1.
#
# This root instantiates `../../../modules/token-broker` — the same module
# `broker/localstack/main.tf` calls. There is no production-only copy of the stack to keep
# in sync; everything that differs between a local run and this one is a flag or a
# variable, and `localstack = false` below is the flag that flips the production controls
# on.
#
# ── WHY THIS LEAF IS IN THE BRIMYR REPO AND NOT IN INFRA ──────────────────────────────
#
# It belongs in magmamoose/infra and is expected to move there. infra has no account
# dimension today: every leaf sits under `terraform/aws/prod/eu-west-1/<stack>` and runs
# against one ambient credential, so there is nowhere to say "this one applies into
# 202518311296 through the mm-prd-brimyr SSO profile" (MagmaMoose/infra#641, and
# broker/README.md step 2). Parking it here keeps the module, the Lambda source and the
# leaf that deploys them inside one reviewable change instead of splitting a first apply
# across two repos. The path deliberately mirrors infra's <env>/<region>/<stack> layout so
# the eventual move is a `git mv` plus a state migration, not a rewrite.
#
# ── WHAT TERRAFORM DELIBERATELY DOES NOT OWN HERE ─────────────────────────────────────
#
#   The App identity   /brimyr/prod/app-id and /brimyr/prod/private-key are SecureStrings
#                      seeded BY HAND (broker/README.md step 3). There is no
#                      `aws_ssm_parameter` in this leaf and there must never be one: a
#                      secret passed to a Terraform resource is a secret sitting in
#                      plaintext in Terraform state, which is a strictly worse place for
#                      it than Parameter Store. The stack only ever *reads* those two.
#   The certificate    ACM issues and DNS-validates out of band; its ARN comes in as a
#                      variable. See var.certificate_arn.
#   The DNS record     broker-brimyr.magmamoose.com is a Cloudflare-proxied CNAME to the
#                      API Gateway regional domain name, and Cloudflare zones are not
#                      managed from this repo.

provider "aws" {
  region = "eu-west-1"

  # The guard that turns a wrong-profile apply into an immediate, total no-op.
  #
  # Credentials here are ambient: an SSO session for mm-prd-brimyr, picked up from
  # AWS_PROFILE or the default profile. A stale session, a shell that still has the
  # management account exported, or a plain forgotten `export AWS_PROFILE=...` would
  # otherwise apply this entire stack — IAM role, Lambda, public HTTP API — into whatever
  # account STS happened to answer for, and the first anyone would know is a resource
  # named brimyr-broker in the wrong place. With the account pinned, the provider fails
  # while it is being configured, before a single resource is planned, and prints the
  # account id it actually got.
  allowed_account_ids = ["202518311296"]
}

variable "certificate_arn" {
  description = <<-EOT
    ACM certificate covering broker-brimyr.magmamoose.com, issued and DNS-validated out of
    band and passed in here.

    Not created by this leaf on purpose: an `aws_acm_certificate` plus its validation
    records would make every apply of the broker wait on DNS this stack does not own, and
    the certificate's lifecycle is longer than the stack's — destroying and recreating the
    broker should not churn a certificate.

    MUST be an eu-west-1 certificate. An HTTP API *regional* custom domain takes a
    certificate from its own region; the us-east-1 rule is CloudFront's, and getting the
    two confused fails at apply with a message that does not say so.
  EOT
  type        = string

  validation {
    condition     = can(regex("^arn:aws:acm:eu-west-1:202518311296:certificate/", var.certificate_arn))
    error_message = "certificate_arn must be an ACM certificate ARN in eu-west-1 AND in account 202518311296 — a regional API Gateway custom domain cannot use a us-east-1 certificate, and a cert ARN from another account (chargate's, most plausibly) fails at apply with an opaque BadRequestException instead."
  }
}

variable "lambda_zip_path" {
  description = <<-EOT
    The package built by `broker/scripts/build_lambda_zip.py`, the same script `make -C
    broker zip` and CI use — a path, because the artifact is built and never committed.

    Build it for the RUNTIME, not for the laptop:

      uv run --extra dev python scripts/build_lambda_zip.py \
        --out dist/broker.zip --platform x86_64-manylinux_2_28

    `cryptography` ships compiled wheels, so a macOS-ARM build applies cleanly and then
    ImportErrors on the first cold start. The module leaves the function on the default
    x86_64 architecture, which is what that platform triple targets.
  EOT
  type        = string
}

module "broker" {
  source = "../../../modules/token-broker"

  name_prefix = "brimyr-broker"

  # Must equal the `audience:` the consumer's workflow asks GitHub for. It is the boundary
  # that stops chargate's runners minting brimyr's identity, so it is per-service and
  # never a shared value.
  oidc_audience = "brimyr"

  # Read-only from this stack's point of view; seeded by hand. See the header.
  secret_path = "/brimyr/prod"

  lambda_zip_path = var.lambda_zip_path

  # FIRST-LEVEL subdomain, deliberately. Cloudflare's free Universal SSL covers the apex
  # and exactly one label, so `broker-brimyr.magmamoose.com` can be proxied and
  # `broker.brimyr.magmamoose.com` cannot — nievah and caldrith are both stuck two labels
  # deep and need renames (MagmaMoose/nievah#187, MagmaMoose/caldrith#68). Do not "tidy"
  # this into a nested name.
  domain_name     = "broker-brimyr.magmamoose.com"
  certificate_arn = var.certificate_arn

  # Load-bearing, not boilerplate. `false` is what makes the module set
  # disable_execute_api_endpoint = true (with domain_name non-empty), so the custom domain
  # becomes the only door, and what makes it apply log retention. `true` against real AWS
  # would leave the generated execute-api URL publicly reachable and unproxied.
  localstack = false

  # Everything else is the module's default and correct here: github_api_url is
  # api.github.com (no GHES), token_permissions_json is `pull_requests: write` and nothing
  # else, allowed_repositories is empty because the App installation IS the allowlist,
  # and the 5 rps / 10 burst stage throttle is the deterministic spend cap on a public
  # unauthenticated endpoint.
}

output "api_endpoint" {
  description = "Base URL for the broker — the custom domain. This is what BRIMYR_BROKER_URL points at."
  value       = module.broker.api_endpoint
}

output "execute_api_endpoint" {
  description = <<-EOT
    The generated execute-api URL. MUST return 403 after the first apply — that curl is
    the only check proving the custom domain is the sole door. See the README.
  EOT
  value       = module.broker.execute_api_endpoint
}

output "target_domain_name" {
  description = <<-EOT
    CNAME broker-brimyr.magmamoose.com at THIS value in Cloudflare, proxied. Not at
    api_endpoint (that is the name being created) and emphatically not at
    execute_api_endpoint (deliberately disabled — every request would 403).
  EOT
  value       = module.broker.target_domain_name
}

output "function_name" {
  description = "For `aws logs tail /aws/lambda/<name>` when the smoke workflow goes red."
  value       = module.broker.function_name
}
