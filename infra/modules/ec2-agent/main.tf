terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

# Agent runner EC2. I/O bound LLM orchestration — t4g.medium Graviton.
# See ADR-002.

variable "env" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "instance_type" {
  type    = string
  default = "t4g.medium"
}
variable "ecr_repo_url" { type = string }
variable "image_tag" {
  type    = string
  default = "latest"
}
variable "secret_arns" {
  type = list(string)
}

data "aws_ami" "al2023_arm64" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-arm64"]
  }
}

resource "aws_security_group" "agent" {
  name        = "ai-trader-${var.env}-agent"
  description = "ai-trader agent runner"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "agent" {
  name = "ai-trader-${var.env}-agent"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.agent.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "secrets" {
  name = "ai-trader-${var.env}-agent-secrets"
  role = aws_iam_role.agent.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.secret_arns
    }]
  })
}

resource "aws_iam_instance_profile" "agent" {
  name = "ai-trader-${var.env}-agent"
  role = aws_iam_role.agent.name
}

resource "aws_launch_template" "agent" {
  name_prefix            = "ai-trader-${var.env}-agent-"
  image_id               = data.aws_ami.al2023_arm64.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.agent.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.agent.name
  }

  user_data = base64encode(templatefile("${path.module}/user-data.sh.tftpl", {
    env          = var.env
    ecr_repo_url = var.ecr_repo_url
    image_tag    = var.image_tag
  }))

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "ai-trader-${var.env}-agent"
    }
  }
}

resource "aws_autoscaling_group" "agent" {
  name                = "ai-trader-${var.env}-agent"
  vpc_zone_identifier = var.private_subnet_ids
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1

  launch_template {
    id      = aws_launch_template.agent.id
    version = "$Latest"
  }

  health_check_type         = "EC2"
  health_check_grace_period = 60

  tag {
    key                 = "Name"
    value               = "ai-trader-${var.env}-agent"
    propagate_at_launch = true
  }
}

output "security_group_id" { value = aws_security_group.agent.id }
output "iam_role_arn" { value = aws_iam_role.agent.arn }
