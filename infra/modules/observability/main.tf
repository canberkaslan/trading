# Phase 4 — CloudWatch alarms, log groups, SNS → Slack webhook integration.
#
# Reuses existing Prometheus/Grafana on EKS via remote_write (see ADR-002).
# This module wires:
#   - log groups with 1-day retention + S3 export
#   - alarm: drawdown >2% intraday
#   - alarm: agent decision latency p99 >30s
#   - alarm: Alpaca 5xx >1%
#   - alarm: Polygon WS disconnect >5min
#   - alarm: kill-switch toggled (audit)
