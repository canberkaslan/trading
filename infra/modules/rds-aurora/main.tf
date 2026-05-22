terraform {
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

variable "env" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "min_capacity" {
  type    = number
  default = 0.5
}
variable "max_capacity" {
  type    = number
  default = 2
}

resource "random_password" "master" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "this" {
  name       = "ai-trader-${var.env}"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "aurora" {
  name        = "ai-trader-${var.env}-aurora"
  description = "ai-trader Aurora"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_rds_cluster" "this" {
  cluster_identifier              = "ai-trader-${var.env}"
  engine                          = "aurora-postgresql"
  engine_mode                     = "provisioned"
  engine_version                  = "16.4"
  database_name                   = "trader"
  master_username                 = "trader"
  master_password                 = random_password.master.result
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.aurora.id]
  storage_encrypted               = true
  backup_retention_period         = 7
  preferred_backup_window         = "03:00-04:00"
  skip_final_snapshot             = var.env == "dev"
  final_snapshot_identifier       = var.env == "dev" ? null : "ai-trader-${var.env}-final"
  enabled_cloudwatch_logs_exports = ["postgresql"]

  serverlessv2_scaling_configuration {
    min_capacity = var.min_capacity
    max_capacity = var.max_capacity
  }
}

resource "aws_rds_cluster_instance" "this" {
  cluster_identifier = aws_rds_cluster.this.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.this.engine
  engine_version     = aws_rds_cluster.this.engine_version
}

# Master password stored in Secrets Manager out-of-band — not in TF state.
# Use AWS Secrets Manager managed master password feature in production.

output "endpoint" { value = aws_rds_cluster.this.endpoint }
output "reader_endpoint" { value = aws_rds_cluster.this.reader_endpoint }
output "security_group_id" { value = aws_security_group.aurora.id }
