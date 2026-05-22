# Phase 4 — API EC2 ASG behind ALB.
# Wired once we have a working FastAPI image in ECR.
#
# Will mirror ec2-agent module structure with:
# - 2× t4g.medium across AZs
# - ALB target group registration
# - Health check on /healthz
# - SG ingress from sg-alb only
