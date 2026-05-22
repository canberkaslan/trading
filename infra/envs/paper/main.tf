terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
  backend "s3" {
    key = "ai-trader/paper/terraform.tfstate"
  }
}

# Paper environment scaffolding. Wired in Phase 4 — see ROADMAP.md.
# Mirrors dev structure but with full HA (2× API, AZ-spread, managed NAT optional).
