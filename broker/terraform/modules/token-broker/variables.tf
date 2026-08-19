# Parameterised on purpose. MagmaMoose/brimyr#11 calls the second broker "the extraction
# point": chargate was the first, brimyr is the second, and the moment to parameterise a
# module is before it has been forked, not after. Every value that differs between the two
# services is a variable here; nothing about brimyr is baked into the module body.
#
# Do NOT collapse the two deployments onto one Lambda. A single minter holding both Apps'
# private keys means a compromise of either surface yields both identities, and the
# audience check stops being a boundary. Two functions at ~$0 each is the cheaper failure
# mode.

variable "name_prefix" {
  description = "Prefix for every resource name, e.g. \"brimyr-broker\"."
  type        = string
}

variable "oidc_audience" {
  description = <<-EOT
    The OIDC `aud` the consumer's action requests, e.g. "brimyr". Must match on both
    sides, and must differ per service — it is the boundary that stops one service's
    runners minting the other's identity.
  EOT
  type        = string
}

variable "secret_path" {
  description = <<-EOT
    SSM Parameter Store prefix holding `app-id` and `private-key`, e.g. "/brimyr/prod".
    Seeded BY HAND, never by Terraform: a secret in a Terraform resource is a secret in
    Terraform state.
  EOT
  type        = string
}

variable "lambda_zip_path" {
  description = "Path to the package built by scripts/build_lambda_zip.py."
  type        = string
}

variable "github_api_url" {
  description = "GitHub API base URL. Override for GHES."
  type        = string
  default     = "https://api.github.com"
}

variable "token_permissions_json" {
  description = <<-EOT
    Least privilege for the minted token. brimyr only ever comments on a PR, so
    `pull_requests: write` is the whole grant.
  EOT
  type        = string
  default     = "{\"pull_requests\": \"write\"}"
}

variable "allowed_repositories" {
  description = <<-EOT
    Optional comma-separated owner/repo allowlist. Empty means any repo the App is
    installed on, which is the public-app model — the App installation IS the allowlist.
  EOT
  type        = string
  default     = ""
}

variable "domain_name" {
  description = <<-EOT
    Custom domain for the API, e.g. "broker-brimyr.magmamoose.com".

    MUST BE A FIRST-LEVEL SUBDOMAIN if it is to be Cloudflare-proxied on the free plan:
    Universal SSL covers the apex and one label only. `broker-brimyr.magmamoose.com` is
    correct; `broker.brimyr.magmamoose.com` is two labels deep and cannot be proxied —
    nievah and caldrith are both stuck there and need a rename (MagmaMoose/nievah#187,
    MagmaMoose/caldrith#68). Do not repeat it.

    Empty disables the custom domain, which is the LocalStack case.
  EOT
  type        = string
  default     = ""
}

variable "certificate_arn" {
  description = "ACM certificate for domain_name. Required when domain_name is set."
  type        = string
  default     = ""
}

variable "throttle_burst_limit" {
  description = <<-EOT
    Stage burst limit. This is the deterministic cap on invocations, compute, logs and
    egress, and it is the reason the module uses an API Gateway HTTP API rather than a
    Lambda Function URL — a Function URL has no throttle at all.
  EOT
  type        = number
  default     = 10
}

variable "throttle_rate_limit" {
  description = "Stage steady-state rate limit, requests/second."
  type        = number
  default     = 5
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention. Short by default; these logs carry no request-derived strings."
  type        = number
  default     = 14
}

variable "localstack" {
  description = <<-EOT
    Local run. Keeps the execute-api endpoint enabled (there is no custom domain in front
    of it locally) and skips the log-group retention setting LocalStack does not honour.
    NEVER true against real AWS: it is what leaves the default endpoint reachable.
  EOT
  type        = bool
  default     = false
}
