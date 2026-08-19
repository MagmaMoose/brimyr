#!/usr/bin/env bash
# Seed the broker's secrets into LocalStack's SSM Parameter Store.
#
# Mirrors what an operator does by hand against real AWS — the parameters are NEVER
# created by Terraform, because a secret in a Terraform resource is a secret in Terraform
# state and the infra repo is public.
#
# The key here is a THROWAWAY generated on the spot. It is never a real GitHub App key:
# the local stack cannot mint anyway (LocalStack emulates AWS, not GitHub), so a real key
# would buy nothing and put a production credential on a developer's disk. What this does
# prove is the whole retrieval path — get_parameters_by_path, SecureString decryption, the
# IAM statement and the KMS ViaService condition — which is the part that is easy to get
# wrong and invisible until /readyz answers 503.
#
# Usage: ./seed.sh [secret-path]   (default /brimyr/local)
set -euo pipefail

SECRET_PATH="${1:-/brimyr/local}"
ENDPOINT="${AWS_ENDPOINT_URL:-http://localhost:4566}"  # DevSkim: ignore DS162092

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-eu-west-1}"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# PKCS#1 — the `BEGIN RSA PRIVATE KEY` form GitHub actually hands out, so app.config's
# PEM handling is exercised in the shape production sees rather than a PKCS#8 stand-in.
openssl genrsa -traditional -out "${tmp}/key.pem" 2048 2>/dev/null

aws --endpoint-url "${ENDPOINT}" ssm put-parameter \
  --name "${SECRET_PATH}/app-id" \
  --value "123456" \
  --type SecureString \
  --overwrite >/dev/null

aws --endpoint-url "${ENDPOINT}" ssm put-parameter \
  --name "${SECRET_PATH}/private-key" \
  --value "file://${tmp}/key.pem" \
  --type SecureString \
  --overwrite >/dev/null

echo "Seeded ${SECRET_PATH}/app-id and ${SECRET_PATH}/private-key (throwaway 2048-bit key)."
echo "Parameter names map to BrokerConfig fields by app.ssm._field_name: app-id -> app_id."
