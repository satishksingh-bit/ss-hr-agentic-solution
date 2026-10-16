locals {
  services = {
    "hr-chat-bff" = {
      sa            = lookup(var.service_account_emails, "hr-bff-sa", "hr-bff-sa@${var.project_id}.iam.gserviceaccount.com")
      min_instances = var.min_instances_bff
      ingress       = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    }
    "workweek-mcp" = {
      sa            = lookup(var.service_account_emails, "workweek-mcp-sa", "workweek-mcp-sa@${var.project_id}.iam.gserviceaccount.com")
      min_instances = var.min_instances_mcp
      ingress       = "INGRESS_TRAFFIC_INTERNAL_ONLY"
    }
    "servicedesk-mcp" = {
      sa            = lookup(var.service_account_emails, "servicedesk-mcp-sa", "servicedesk-mcp-sa@${var.project_id}.iam.gserviceaccount.com")
      min_instances = var.min_instances_mcp
      ingress       = "INGRESS_TRAFFIC_INTERNAL_ONLY"
    }
    "hr-admin" = {
      sa            = lookup(var.service_account_emails, "hr-admin-sa", "hr-admin-sa@${var.project_id}.iam.gserviceaccount.com")
      min_instances = 1
      ingress       = "INGRESS_TRAFFIC_INTERNAL_ONLY"
    }
    "audit-writer" = {
      sa            = lookup(var.service_account_emails, "audit-writer-sa", "audit-writer-sa@${var.project_id}.iam.gserviceaccount.com")
      min_instances = 1
      ingress       = "INGRESS_TRAFFIC_INTERNAL_ONLY"
    }
  }

  jobs = {
    "kb-sync" = {
      sa       = lookup(var.service_account_emails, "kb-sync-sa", "kb-sync-sa@${var.project_id}.iam.gserviceaccount.com")
      args     = ["-m", "hr_agent.policy_rag"]
      schedule = "*/15 * * * *"
    }
    "plan-reconciler" = {
      sa       = lookup(var.service_account_emails, "plan-reconciler-sa", "plan-reconciler-sa@${var.project_id}.iam.gserviceaccount.com")
      args     = ["-m", "hr_agent.orchestrator"]
      schedule = "*/2 * * * *"
    }
    "synthetic-prober" = {
      sa       = lookup(var.service_account_emails, "synthetic-prober-sa", "synthetic-prober-sa@${var.project_id}.iam.gserviceaccount.com")
      args     = ["-m", "hr_agent.tools.synthetic_prober", "--env=${var.env}", "--interval=60s"]
      schedule = "* * * * *"
    }
    "engine-drift-job" = {
      sa       = lookup(var.service_account_emails, "engine-drift-sa", "engine-drift-sa@${var.project_id}.iam.gserviceaccount.com")
      args     = ["-m", "hr_agent.tools.engine_drift_job", "--env=${var.env}", "--config=config/engine.yaml"]
      schedule = "*/5 * * * *"
    }
    "dsar-tool" = {
      sa       = lookup(var.service_account_emails, "dsar-tool-sa", "dsar-tool-sa@${var.project_id}.iam.gserviceaccount.com")
      args     = ["scripts/hrctl.py", "dsar-delete"]
      schedule = ""
    }
  }
}

resource "google_cloud_run_v2_service" "run_services" {
  for_each = local.services
  name     = "${each.key}-${var.env}"
  location = var.region
  project  = var.project_id
  ingress  = each.value.ingress

  template {
    service_account = each.value.sa
    scaling {
      min_instance_count = each.value.min_instances
      max_instance_count = 20
    }
    containers {
      image = "${var.artifact_repo}/${each.key}:${var.image_tag}"
      env {
        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
        value = "NO_CONTENT"
      }
      env {
        name  = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"
        value = "false"
      }
      env {
        name  = "GENAI_COMPLETION_HOOKS_ENABLED"
        value = "false"
      }
      env {
        name  = "BQ_AGENT_ANALYTICS_RAW_LOGGING"
        value = "false"
      }
      env {
        name  = "MODEL_ARMOR_PLATFORM_LOGGING"
        value = "false"
      }
      env {
        name  = "LOG_LEVEL"
        value = "INFO"
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      traffic,
    ]
  }
}

resource "google_cloud_run_v2_job" "background_jobs" {
  for_each = local.jobs
  name     = "${each.key}-${var.env}"
  location = var.region
  project  = var.project_id

  template {
    template {
      service_account = each.value.sa
      max_retries     = 1
      timeout         = "300s"
      containers {
        image   = "${var.artifact_repo}/hr-workers:${var.image_tag}"
        command = ["python"]
        args    = each.value.args
        env {
          name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
          value = "NO_CONTENT"
        }
        env {
          name  = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"
          value = "false"
        }
        env {
          name  = "GENAI_COMPLETION_HOOKS_ENABLED"
          value = "false"
        }
        env {
          name  = "BQ_AGENT_ANALYTICS_RAW_LOGGING"
          value = "false"
        }
        env {
          name  = "MODEL_ARMOR_PLATFORM_LOGGING"
          value = "false"
        }
      }
    }
  }
}
