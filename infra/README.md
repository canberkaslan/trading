# Infrastructure

Terraform for AWS EC2 deployment on `rootingo` (BetCoreWin account `674594306499`, eu-west-1).

See [ADR-002](../docs/adr/002-aws-ec2-deployment.md) for the deployment decision and [ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the full diagram.

## Layout

```
infra/
├── modules/                 # Reusable, env-agnostic building blocks
│   ├── vpc/                # VPC + subnets + fck-nat NAT
│   ├── ec2-agent/          # Agent runner ASG
│   ├── ec2-api/            # API EC2 ×2 behind ALB
│   ├── ec2-data/           # Data daemon + TimescaleDB EBS
│   ├── ec2-backtest/       # Spot fleet + EventBridge schedule (TODO)
│   ├── rds-aurora/         # Aurora Serverless v2 PostgreSQL
│   ├── elasticache/        # Redis cache.t4g.micro
│   ├── s3-data/            # Buckets + lifecycle + KMS
│   ├── alb-waf/            # ALB + WAFv2
│   ├── apigw-ws/           # API Gateway WebSocket
│   ├── cognito/            # User pool + app client (TODO)
│   ├── secrets/            # Secrets Manager entries
│   └── observability/      # CloudWatch alarms, log groups, SNS → Slack
├── envs/
│   ├── dev/                # Lightweight single-AZ
│   ├── paper/              # Full stack, Alpaca paper key
│   └── live/               # Placeholder — separate AWS account when ready
└── backend.hcl             # S3 + DynamoDB state config
```

## Backend

Terraform state lives in `s3://betcorewin-tfstate/ai-trader/<env>/terraform.tfstate` with DynamoDB lock on the existing table.

## Quick start (dev)

```bash
aws-vault exec rootingo -- terraform -chdir=envs/dev init -backend-config=../../backend.hcl
aws-vault exec rootingo -- terraform -chdir=envs/dev plan -var-file=dev.tfvars
aws-vault exec rootingo -- terraform -chdir=envs/dev apply -var-file=dev.tfvars
```

## Cost target

Paper trade phase: **<$300/mo** (see ADR-002 breakdown).

If we exceed budget:
1. Drop API to single t4g.medium (-$17), accept single-AZ in paper
2. Move Aurora → RDS db.t4g.micro provisioned (-$31)
3. Run backtest weekly not nightly (-$30)
