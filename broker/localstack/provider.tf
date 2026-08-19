# LocalStack-only provider. Every AWS call is redirected to the container on :4566 and the
# credentials are the fake ones LocalStack expects. This file is the ONLY difference
# between the local root and a real leaf — main.tf calls the same module either way.

provider "aws" {
  region                      = "eu-west-1"
  access_key                  = "test"
  secret_key                  = "test" # DevSkim: ignore DS173237 - LocalStack's documented fake credential, not a secret
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    apigateway     = "http://localhost:4566" # DevSkim: ignore DS137138
    apigatewayv2   = "http://localhost:4566" # DevSkim: ignore DS137138
    cloudwatchlogs = "http://localhost:4566" # DevSkim: ignore DS137138
    iam            = "http://localhost:4566" # DevSkim: ignore DS137138
    kms            = "http://localhost:4566" # DevSkim: ignore DS137138
    lambda         = "http://localhost:4566" # DevSkim: ignore DS137138
    ssm            = "http://localhost:4566" # DevSkim: ignore DS137138
    sts            = "http://localhost:4566" # DevSkim: ignore DS137138
  }
}
