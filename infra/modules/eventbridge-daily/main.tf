terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

# EventBridge rule + Lambda target: invoke agent pipeline daily at US post-close
# (22:30 UTC) with a 30-min reconciliation buffer over Polygon EOD feed.
#
# The Lambda container image is built+pushed by the agent-ci workflow; this
# module wires the trigger only. To run, point `lambda_image_uri` at an
# ECR-hosted image whose entrypoint executes `python -m scripts.trade
# --ticker $TICKER --submit`.

variable "env" { type = string }
variable "tickers" {
  type        = list(string)
  description = "Tickers to decide on each day"
  default     = ["SPY", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"]
}
variable "schedule_expression" {
  type        = string
  description = "EventBridge cron — 22:30 UTC every weekday (post-close + 30min)"
  default     = "cron(30 22 ? * MON-FRI *)"
}
variable "lambda_image_uri" {
  type        = string
  description = "ECR image URI of the agent runtime. Empty disables deployment."
  default     = ""
}
variable "lambda_role_arn" {
  type        = string
  description = "IAM role ARN with: Secrets Manager read, S3 R/W to data bucket, Aurora connect"
  default     = ""
}
variable "vpc_subnet_ids" {
  type    = list(string)
  default = []
}
variable "vpc_security_group_ids" {
  type    = list(string)
  default = []
}

locals {
  has_lambda = var.lambda_image_uri != "" && var.lambda_role_arn != ""
}

resource "aws_lambda_function" "agent" {
  count         = local.has_lambda ? 1 : 0
  function_name = "ai-trader-${var.env}-agent-daily"
  role          = var.lambda_role_arn
  package_type  = "Image"
  image_uri     = var.lambda_image_uri
  timeout       = 900  # 15 min — full 7-agent pipeline ~10 min
  memory_size   = 2048 # I/O bound; this is generous

  environment {
    variables = {
      ENVIRONMENT = var.env
      AWS_REGION  = "eu-west-1"
    }
  }

  vpc_config {
    subnet_ids         = var.vpc_subnet_ids
    security_group_ids = var.vpc_security_group_ids
  }

  ephemeral_storage {
    size = 1024 # MB, for vendored upstream + parquet caching
  }
}

resource "aws_cloudwatch_event_rule" "daily" {
  name                = "ai-trader-${var.env}-daily"
  description         = "Trigger agent decision pipeline at US post-close"
  schedule_expression = var.schedule_expression
}

# Fan out to each ticker as a separate Lambda invocation — parallelism +
# isolated failure domains (one ticker LLM error doesn't break the others).
resource "aws_cloudwatch_event_target" "per_ticker" {
  for_each = local.has_lambda ? toset(var.tickers) : toset([])

  rule      = aws_cloudwatch_event_rule.daily.name
  target_id = "agent-${replace(each.value, ".", "-")}"
  arn       = aws_lambda_function.agent[0].arn

  input = jsonencode({
    ticker = each.value
    submit = true
  })
}

resource "aws_lambda_permission" "eventbridge" {
  count         = local.has_lambda ? 1 : 0
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily.arn
}

output "rule_arn" { value = aws_cloudwatch_event_rule.daily.arn }
output "lambda_arn" {
  value = local.has_lambda ? aws_lambda_function.agent[0].arn : ""
}
