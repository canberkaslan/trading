terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
  backend "s3" {
    key = "ai-trader/dev/terraform.tfstate"
  }
}

provider "aws" {
  region  = "eu-west-1"
  profile = "rootingo"

  default_tags {
    tags = {
      Project     = "ai-trader"
      Environment = "dev"
      Owner       = "canberk"
      ManagedBy   = "terraform"
      Repository  = "github.com/canberkaslan/trading"
    }
  }
}

locals {
  env      = "dev"
  vpc_cidr = "10.20.0.0/16"
}

module "vpc" {
  source      = "../../modules/vpc"
  env         = local.env
  cidr        = local.vpc_cidr
  azs         = ["eu-west-1a", "eu-west-1b"]
  use_fck_nat = true
}

module "secrets" {
  source = "../../modules/secrets"
  env    = local.env
  # Secret bodies created out-of-band via AWS CLI to avoid putting them in state plaintext
  secret_names = [
    "anthropic",
    "polygon",
    "finnhub",
    "alpaca-paper",
    "reddit",
    "fred",
  ]
}

module "s3_data" {
  source = "../../modules/s3-data"
  env    = local.env
}

module "elasticache" {
  source             = "../../modules/elasticache"
  env                = local.env
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  node_type          = "cache.t4g.micro"
}

module "rds_aurora" {
  source             = "../../modules/rds-aurora"
  env                = local.env
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  min_capacity       = 0.5
  max_capacity       = 2
}

module "eventbridge_daily" {
  source = "../../modules/eventbridge-daily"
  env    = local.env
  # lambda_image_uri left empty until the agent container is published to ECR;
  # the schedule resource still creates so we can verify the cron expression.
  lambda_image_uri = ""
  lambda_role_arn  = ""
}

module "cognito" {
  source        = "../../modules/cognito"
  env           = local.env
  region        = "eu-west-1"
  domain_prefix = "ai-trader-dev-674594306499"
  # Mobile Expo dev redirect; replace with custom scheme + universal links once we
  # have App Store presence.
  callback_urls = ["trading://auth/callback", "http://localhost:8081/auth/callback"]
  logout_urls   = ["trading://auth/logout", "http://localhost:8081/auth/logout"]
}

# Compute modules (ec2-agent, ec2-api, ec2-data, alb-waf, apigw-ws, cognito, observability)
# wired in Phase 4 once paper-trade-ready code lands in agent/.
