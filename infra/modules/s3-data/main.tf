terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

variable "env" { type = string }

resource "aws_kms_key" "data" {
  description             = "ai-trader ${var.env} data encryption"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "data" {
  name          = "alias/ai-trader-${var.env}"
  target_key_id = aws_kms_key.data.id
}

resource "aws_s3_bucket" "data" {
  bucket = "ai-trader-data-${var.env}"
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "intelligent-tiering"
    status = "Enabled"
    filter { prefix = "historical/" }
    transition {
      days          = 0
      storage_class = "INTELLIGENT_TIERING"
    }
    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }
  }

  rule {
    id     = "expire-old-logs"
    status = "Enabled"
    filter { prefix = "logs/" }
    expiration { days = 90 }
  }
}

output "bucket_name" { value = aws_s3_bucket.data.bucket }
output "kms_key_arn" { value = aws_kms_key.data.arn }
