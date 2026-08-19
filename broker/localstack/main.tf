# The Brimyr token broker, deployed to LocalStack.
#
# This root instantiates the SAME module a real AWS leaf would (`../terraform/modules/
# token-broker`), so what runs here is the real module code rather than a parallel
# local-only definition. The only differences live in `provider.tf` (endpoints pointed at
# LocalStack, fake credentials) and in the `localstack = true` flag the module reads.
#
# ── WHAT A LOCAL RUN PROVES ───────────────────────────────────────────────────────────
#
#   The zip imports          The Lambda cold-starts from the artifact build_lambda_zip.py
#                            produced, on the real python3.12 runtime image, with only the
#                            vendored wheels plus the runtime's own boto3. An ImportError
#                            here is the same ImportError production would get.
#   SSM wiring               /readyz resolves APP_ID and PRIVATE_KEY through app.ssm ->
#                            get_parameters_by_path -> SecureString decryption. It answers
#                            503 until the parameters are seeded and 200 after, which is
#                            the IAM statement and the KMS grant doing their jobs.
#   Routing                  /healthz, /readyz, /token, 404 for anything else, 405 for the
#                            wrong verb — through API Gateway payload format 2.0, not by
#                            calling the handler directly.
#   The /token error ladder  invalid_json, missing_fields, invalid_repository and
#                            invalid_oidc, in the evaluation order app.broker fixes.
#
# ── WHAT IT DOES NOT PROVE, AND CANNOT ────────────────────────────────────────────────
#
#   A successful mint        Needs GitHub, twice over: a token signed by GitHub's Actions
#                            OIDC key, and a Brimyr App actually installed on the repo.
#                            LocalStack emulates AWS, not GitHub.
#
#                            The issuer and JWKS URL in app/config.py are deliberately NOT
#                            configurable. Making them overridable would let a local run
#                            "prove" a mint against a self-signed key, which is exactly the
#                            check that must never be weakenable — issuer pinning is the
#                            control that stops anyone with any valid OIDC token from
#                            minting. A local run that cannot fake it is the correct
#                            trade; the real path is covered by the smoke workflow against
#                            the deployed broker.
#   Throttling               The stage throttle is set below and accepted, but LocalStack
#                            does not enforce it. It is the deterministic cap on
#                            invocations, compute and egress, so verify it after the first
#                            real apply.
#   The custom domain        `disable_execute_api_endpoint` and the Cloudflare-proxied
#                            first-level hostname (broker-brimyr.magmamoose.com) are
#                            production-only. Check the execute-api URL returns 404 after
#                            the first real apply.

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "lambda_zip_path" {
  description = <<-EOT
    The built Lambda package. `make -C broker zip` produces it with the same
    scripts/build_lambda_zip.py that CI publishes with, so the local run and the released
    artifact cannot be built differently.
  EOT
  type        = string
}

module "broker" {
  source = "../terraform/modules/token-broker"

  name_prefix     = "brimyr-broker"
  oidc_audience   = "brimyr"
  secret_path     = "/brimyr/local"
  lambda_zip_path = var.lambda_zip_path

  # No custom domain locally: there is no ACM certificate and no Cloudflare in front,
  # so the execute-api endpoint stays enabled and is the door the smoke test knocks on.
  localstack = true
}

output "api_endpoint" {
  description = "Base URL the smoke test drives."
  value       = module.broker.api_endpoint
}

output "function_name" {
  value = module.broker.function_name
}
