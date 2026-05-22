terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

variable "env" { type = string }
variable "secret_names" {
  type        = list(string)
  description = "Logical secret names (e.g. anthropic, polygon). Full ARN becomes ai-trader/<env>/<name>"
}

# Bodies must be set out-of-band:
#   aws --profile rootingo secretsmanager put-secret-value \
#     --secret-id ai-trader/dev/anthropic --secret-string sk-ant-...
resource "aws_secretsmanager_secret" "this" {
  for_each    = toset(var.secret_names)
  name        = "ai-trader/${var.env}/${each.key}"
  description = "AI Trader ${var.env} — ${each.key}"

  recovery_window_in_days = 7
}

output "secret_arns" {
  value = { for k, v in aws_secretsmanager_secret.this : k => v.arn }
}
