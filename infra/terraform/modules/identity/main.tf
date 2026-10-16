locals {
  service_accounts = {
    "hr-bff-sa"           = "HR Chat BFF HTTP/SSE Gateway Service Account"
    "hr-agent-sa"         = "Vertex AI Agent Engine Runtime Service Account"
    "workweek-mcp-sa"     = "WorkWeek HRIS MCP Server Service Account"
    "servicedesk-mcp-sa"  = "ServiceImmediately ITSM MCP Server Service Account"
    "kb-sync-sa"          = "Policy Corpus Sync Worker Service Account"
    "plan-reconciler-sa"  = "Stale ActionPlan Reconciler Service Account"
    "hr-admin-sa"         = "Kill-Switch Control Plane Service Account"
    "audit-writer-sa"     = "Audit Log Writer Service Account"
    "synthetic-prober-sa" = "Synthetic Availability Prober Service Account"
    "engine-drift-sa"     = "Agent Engine Drift Detector Service Account"
    "dsar-tool-sa"        = "GDPR DSAR Erasure & Crypto-Shredding Service Account"
    "cicd-runner-sa"      = "CI/CD Pipeline Runner Service Account"
  }

  # Per-database least-privilege Firestore IAM matrix per ADR-019 (BH-05-1-04, BH-05-1-05, BH-05-2-01)
  firestore_iam_matrix = {
    "hr-personas" = {
      writers = ["cicd-runner-sa"]
      readers = ["hr-bff-sa", "cicd-runner-sa"]
    }
    "hr-flags" = {
      writers = ["hr-admin-sa", "cicd-runner-sa"]
      readers = ["hr-bff-sa", "hr-agent-sa", "workweek-mcp-sa", "servicedesk-mcp-sa", "hr-admin-sa"]
    }
    "hr-plans" = {
      writers = ["hr-agent-sa", "plan-reconciler-sa"]
      readers = ["hr-agent-sa", "plan-reconciler-sa"]
    }
    "hr-bff" = {
      writers = ["hr-bff-sa"]
      readers = ["hr-bff-sa"]
    }
    "hr-idem-ww" = {
      writers = ["workweek-mcp-sa"]
      readers = ["workweek-mcp-sa"]
    }
    "hr-idem-si" = {
      writers = ["servicedesk-mcp-sa"]
      readers = ["servicedesk-mcp-sa"]
    }
  }
}

resource "google_service_account" "accounts" {
  for_each     = local.service_accounts
  project      = var.project_id
  account_id   = each.key
  display_name = each.value
}

resource "google_iam_workload_identity_pool" "cicd_pool" {
  project                   = var.project_id
  workload_identity_pool_id = "hr-agent-cicd-pool-${var.env}"
  display_name              = "HR Agent Keyless CI/CD WIF Pool (${var.env})"
  description               = "Workload Identity Federation pool for keyless CI/CD runners"
}

resource "terraform_data" "firestore_per_db_iam_conditions" {
  input = {
    project_id = var.project_id
    env        = var.env
    matrix     = local.firestore_iam_matrix
  }
}
