terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

# Cognito User Pool — single-region, password-only (TOTP MFA on first
# login). App Client is mobile-flavored: no client secret, PKCE flow.
#
# ALB / API Gateway downstream of this validates the issued JWT via
# JWKS at https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json.

variable "env" { type = string }
variable "region" {
  type    = string
  default = "eu-west-1"
}
variable "domain_prefix" {
  type        = string
  description = "Subdomain on amazoncognito.com. Must be globally unique. e.g. 'ai-trader-dev'"
  default     = ""
}
variable "callback_urls" {
  type        = list(string)
  description = "OAuth redirect URIs. Mobile (Expo) usually uses 'trading://auth/callback'."
  default     = ["trading://auth/callback"]
}
variable "logout_urls" {
  type    = list(string)
  default = ["trading://auth/logout"]
}

locals {
  effective_domain = var.domain_prefix != "" ? var.domain_prefix : "ai-trader-${var.env}"
}

resource "aws_cognito_user_pool" "main" {
  name = "ai-trader-${var.env}"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 7
  }

  # TOTP MFA is enforced after sign-in. SMS is intentionally omitted —
  # SIM-swap risk in fintech.
  mfa_configuration = "ON"
  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT" # SES upgrade is a separate change
  }

  admin_create_user_config {
    allow_admin_create_user_only = false
  }

  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  deletion_protection = "ACTIVE"

  schema {
    name                = "email"
    attribute_data_type = "String"
    mutable             = true
    required            = true
  }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = local.effective_domain
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_pool_client" "mobile" {
  name         = "ai-trader-${var.env}-mobile"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret              = false # public client (mobile)
  prevent_user_existence_errors = "ENABLED"

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = ["email", "openid", "profile"]

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  supported_identity_providers = ["COGNITO"]

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_AUTH",
  ]

  # Access tokens last 1h; refresh 30d. Refresh-token rotation handled
  # by Amplify on mobile.
  access_token_validity  = 1
  id_token_validity      = 1
  refresh_token_validity = 30
  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }

  read_attributes = [
    "email", "email_verified",
  ]
  write_attributes = ["email"]
}

output "user_pool_id" { value = aws_cognito_user_pool.main.id }
output "user_pool_arn" { value = aws_cognito_user_pool.main.arn }
output "app_client_id" { value = aws_cognito_user_pool_client.mobile.id }
output "hosted_ui_domain" {
  value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
}
output "jwks_url" {
  value = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.main.id}/.well-known/jwks.json"
}
