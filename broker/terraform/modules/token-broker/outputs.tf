output "api_endpoint" {
  description = "Base URL for the broker. The custom domain in production, execute-api locally."
  value       = var.domain_name != "" && !var.localstack ? "https://${var.domain_name}" : aws_apigatewayv2_api.broker.api_endpoint
}

output "execute_api_endpoint" {
  description = <<-EOT
    The generated execute-api URL, always. In production this MUST return 403 once
    disable_execute_api_endpoint has taken effect — curl it after the first real apply.
    That check is the only thing that proves the custom domain is the sole door.
  EOT
  value       = aws_apigatewayv2_api.broker.api_endpoint
}

output "function_name" {
  value = aws_lambda_function.broker.function_name
}

output "role_arn" {
  value = aws_iam_role.lambda.arn
}

output "target_domain_name" {
  description = <<-EOT
    The API Gateway REGIONAL target for the Cloudflare CNAME — a `d-xxxx.execute-api.
    eu-west-1.amazonaws.com` value. Empty when there is no custom domain (LocalStack).

    This is the last step between `apply` and a working broker, and it is the one value
    that was previously not exposed anywhere. Without it the obvious guess is to CNAME at
    `execute_api_endpoint`, which is exactly wrong: that endpoint is deliberately disabled
    in production, so the record resolves, TLS completes at the Cloudflare edge, and every
    single request 403s at the origin. The smoke workflow then reports
    `GET /healthz -> 403`, which matches nothing in either runbook's error ladder — while
    the leaf README's own "execute-api MUST return 403" check makes the failure look like
    the security control working correctly.
  EOT
  value       = var.domain_name != "" && !var.localstack ? aws_apigatewayv2_domain_name.broker[0].domain_name_configuration[0].target_domain_name : ""
}

output "target_hosted_zone_id" {
  description = "Hosted zone of the regional target, for an alias record if DNS ever moves to Route 53."
  value       = var.domain_name != "" && !var.localstack ? aws_apigatewayv2_domain_name.broker[0].domain_name_configuration[0].hosted_zone_id : ""
}
