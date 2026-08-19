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
