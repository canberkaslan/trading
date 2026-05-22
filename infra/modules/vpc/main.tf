terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

variable "env" { type = string }
variable "cidr" { type = string }
variable "azs" { type = list(string) }
variable "use_fck_nat" {
  type    = bool
  default = true
}

locals {
  public_subnet_cidrs  = [for i, _ in var.azs : cidrsubnet(var.cidr, 8, i + 1)]
  private_subnet_cidrs = [for i, _ in var.azs : cidrsubnet(var.cidr, 8, i + 10)]
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name = "ai-trader-${var.env}"
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "ai-trader-${var.env}" }
}

resource "aws_subnet" "public" {
  count                   = length(var.azs)
  vpc_id                  = aws_vpc.this.id
  cidr_block              = local.public_subnet_cidrs[count.index]
  availability_zone       = var.azs[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "ai-trader-${var.env}-public-${substr(var.azs[count.index], -2, 2)}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.azs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = local.private_subnet_cidrs[count.index]
  availability_zone = var.azs[count.index]

  tags = {
    Name = "ai-trader-${var.env}-private-${substr(var.azs[count.index], -2, 2)}"
    Tier = "private"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "ai-trader-${var.env}-public" }
}

resource "aws_route_table_association" "public" {
  count          = length(var.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# fck-nat: $4/mo vs $32/mo managed NAT Gateway. See ADR-002.
# In paper/live envs with HA requirements, swap to aws_nat_gateway.
resource "aws_security_group" "fck_nat" {
  count       = var.use_fck_nat ? 1 : 0
  name        = "ai-trader-${var.env}-fck-nat"
  description = "fck-nat instance"
  vpc_id      = aws_vpc.this.id

  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# AMI lookup + instance + EIP + private route table all wired in modules/vpc/fck-nat.tf
# (split for clarity once paper env brings up real instances).

output "vpc_id" { value = aws_vpc.this.id }
output "public_subnet_ids" { value = aws_subnet.public[*].id }
output "private_subnet_ids" { value = aws_subnet.private[*].id }
output "cidr" { value = var.cidr }
