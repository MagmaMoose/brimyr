# A GitHub Actions OIDC -> repo-scoped App installation token broker on AWS Lambda,
# fronted by an API Gateway HTTP API.
#
# Design decisions inherited from chargate's ADR (0001-broker-on-aws-lambda) rather than
# re-litigated here:
#
#   HTTP API, not a Function URL   A Function URL has no throttle. The stage throttle is
#                                  what deterministically caps invocations, compute, logs
#                                  and egress on a public unauthenticated endpoint.
#   SSM Parameter Store            Standard-tier SecureString is free, storage and reads
#                                  alike. Secrets Manager is $0.40/secret/month for the
#                                  same job. Seeded by hand, never by Terraform.
#   No KMS signing                 pyjwt[crypto] signs the App JWT in-process. KMS
#                                  asymmetric is ~$1/mo/key plus $0.15/10k Sign calls,
#                                  roughly 100x the rest of the stack.

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ── Execution role ────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.name_prefix}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "lambda" {
  # Read the App identity, and nothing else. Scoped to this service's own prefix so the
  # brimyr broker cannot read chargate's key even if both run in one account.
  statement {
    sid    = "ReadOwnSecrets"
    effect = "Allow"
    actions = [
      "ssm:GetParametersByPath",
      "ssm:GetParameter",
      "ssm:GetParameters",
    ]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${var.secret_path}",
      "arn:aws:ssm:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:parameter${var.secret_path}/*",
    ]
  }

  # SecureString decryption goes through the account's default SSM key. Scoped by the
  # ViaService condition so this grant cannot be used to decrypt anything that did not
  # come out of Parameter Store.
  statement {
    sid       = "DecryptOwnSecrets"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${data.aws_region.current.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda.arn}:*"]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${var.name_prefix}-policy"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "lambda" {
  name = "/aws/lambda/${var.name_prefix}"
  # LocalStack accepts retention_in_days but does not enforce it; setting it there just
  # produces a perpetual diff.
  retention_in_days = var.localstack ? null : var.log_retention_days
}

# ── Function ──────────────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "broker" {
  function_name = var.name_prefix
  role          = aws_iam_role.lambda.arn
  runtime       = "python3.12"
  handler       = "app.lambda_handler.handler"

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  # Cold start is the dominant latency here and the zip is deliberately small (no
  # FastAPI, no pydantic, no bundled boto3). 512 MB buys proportionally more CPU than
  # the memory is worth on a JWT signing path.
  memory_size = 512
  timeout     = 20

  environment {
    variables = {
      # Secrets are NOT here. Anything holding lambda:GetFunctionConfiguration could read
      # them; they come from SSM at request time instead. Only policy lives here.
      SECRET_PATH            = var.secret_path
      OIDC_AUDIENCE          = var.oidc_audience
      GITHUB_API_URL         = var.github_api_url
      TOKEN_PERMISSIONS_JSON = var.token_permissions_json
      ALLOWED_REPOSITORIES   = var.allowed_repositories
    }
  }

  depends_on = [aws_iam_role_policy.lambda, aws_cloudwatch_log_group.lambda]
}

# ── HTTP API ──────────────────────────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "broker" {
  name          = var.name_prefix
  protocol_type = "HTTP"

  # With a custom domain in front, the generated execute-api URL is a second, unthrottled-
  # by-DNS door into the same function. Close it — the custom domain becomes the only way
  # in. Locally there is no custom domain, so it has to stay open.
  disable_execute_api_endpoint = var.localstack ? false : (var.domain_name != "")
}

resource "aws_apigatewayv2_integration" "broker" {
  api_id           = aws_apigatewayv2_api.broker.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.broker.invoke_arn
  # Format 2.0 is what app.lambda_handler parses: rawPath with no stage prefix, and
  # byte-for-byte what a Function URL delivers.
  payload_format_version = "2.0"
}

# One catch-all route. The handler owns 404 and 405 so that "wrong verb" stays
# distinguishable from "no such endpoint" — API Gateway would collapse both.
resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.broker.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.broker.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id = aws_apigatewayv2_api.broker.id
  # $default puts no stage prefix in the path, which is what lets rawPath be the real
  # path. A named stage would prefix every route and every request would 404.
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = var.throttle_burst_limit
    throttling_rate_limit  = var.throttle_rate_limit
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.broker.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.broker.execution_arn}/*/*"
}

# ── Custom domain (production only) ───────────────────────────────────────────────────

resource "aws_apigatewayv2_domain_name" "broker" {
  count       = var.domain_name != "" && !var.localstack ? 1 : 0
  domain_name = var.domain_name

  domain_name_configuration {
    certificate_arn = var.certificate_arn
    endpoint_type   = "REGIONAL"
    security_policy = "TLS_1_2"
  }
}

resource "aws_apigatewayv2_api_mapping" "broker" {
  count       = var.domain_name != "" && !var.localstack ? 1 : 0
  api_id      = aws_apigatewayv2_api.broker.id
  domain_name = aws_apigatewayv2_domain_name.broker[0].id
  stage       = aws_apigatewayv2_stage.default.id
}
