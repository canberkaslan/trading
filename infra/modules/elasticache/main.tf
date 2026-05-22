terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

variable "env" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "node_type" {
  type    = string
  default = "cache.t4g.micro"
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "ai-trader-${var.env}"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "redis" {
  name        = "ai-trader-${var.env}-redis"
  description = "ai-trader Redis"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id       = "ai-trader-${var.env}"
  description                = "ai-trader ${var.env} cache"
  node_type                  = var.node_type
  num_cache_clusters         = 1
  engine                     = "valkey"
  engine_version             = "8.0"
  parameter_group_name       = "default.valkey8"
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.this.name
  security_group_ids         = [aws_security_group.redis.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

  automatic_failover_enabled = false
  multi_az_enabled           = false
}

output "primary_endpoint" { value = aws_elasticache_replication_group.this.primary_endpoint_address }
output "security_group_id" { value = aws_security_group.redis.id }
