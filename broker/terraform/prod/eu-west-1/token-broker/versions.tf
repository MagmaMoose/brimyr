# Version floor and provider pin, kept out of main.tf so the leaf's actual content is the
# module call and nothing else.
#
# Both constraints are copies of what `../../../modules/token-broker` already declares.
# That is not redundancy: a root module's constraints are the ones OpenTofu enforces and
# the ones .terraform.lock.hcl is written from, and a leaf that declared nothing would
# silently accept whatever provider version the caller's cache happened to hold. Bump the
# two together or not at all.
#
# >= 1.9 is a real floor, not a guess — the module uses `null` for retention_in_days and
# the org's leaves rely on 1.9 variable validation. Homebrew's `terraform` formula is
# pinned at 1.5.7 (the last MPL release) and will refuse this; use `tofu`.

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # TODO(MagmaMoose/infra#641): remote state, once infra grows an account dimension and
  # this leaf moves there. Local state on purpose in the meantime — inventing an S3
  # bucket + DynamoDB lock table here would create a second, brimyr-only state backend
  # that the eventual move would immediately have to migrate off, and org state lives in
  # infra's backend, not in a service repo.
  #
  # Consequences while it is local, and they are the reason this is a TODO and not a
  # decision: state lives on one laptop, there is no locking, and `terraform.tfstate` is
  # gitignored (see the repo root .gitignore) so it can never be committed. State holds no
  # secret — the App id and private key are SSM SecureStrings this stack only *reads*, and
  # the Lambda environment carries the SSM path, not the values — but it does hold every
  # resource id, so losing it means importing the stack back rather than re-applying it.
  #
  # backend "s3" {}
}
