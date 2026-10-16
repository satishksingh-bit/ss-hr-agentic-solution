resource "google_logging_project_bucket_config" "worm_audit_bucket" {
  project        = var.project_id
  location       = var.region
  bucket_id      = "hr-agent-audit-worm-${var.env}"
  retention_days = 400
  locked         = var.env == "prodpilot" ? true : false
  description    = "400-day WORM locked audit log bucket (FR-007, NFR-004)"
}

resource "google_logging_project_bucket_config" "ops_log_bucket" {
  project        = var.project_id
  location       = var.region
  bucket_id      = "hr-agent-ops-${var.env}"
  retention_days = 30
  description    = "30-day operational telemetry log bucket (NFR-010)"
}

resource "google_pubsub_topic" "audit_events_dlq" {
  name    = "audit-events-dlq-${var.env}"
  project = var.project_id
}

resource "google_pubsub_topic" "audit_events" {
  name    = "audit-events-${var.env}"
  project = var.project_id

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

resource "google_pubsub_subscription" "audit_events_push" {
  name    = "audit-events-push-${var.env}"
  project = var.project_id
  topic   = google_pubsub_topic.audit_events.name

  ack_deadline_seconds = 30

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.audit_events_dlq.id
    max_delivery_attempts = 5
  }
}

resource "google_data_loss_prevention_inspect_template" "sdp_inspect_template" {
  parent       = "projects/${var.project_id}/locations/${var.region}"
  display_name = "HR Agent Zero-SPII Inspect Template (${var.env})"
  description  = "Inspects BSN, Steuer-ID, AHV, IBAN, and HR SPII before sink ingestion"

  inspect_config {
    min_likelihood = "POSSIBLE"
    info_types {
      name = "NETHERLANDS_BSN_NUMBER"
    }
    info_types {
      name = "GERMANY_TAX_IDENTIFICATION_NUMBER"
    }
    info_types {
      name = "SWITZERLAND_SOCIAL_SECURITY_NUMBER"
    }
    info_types {
      name = "IBAN_CODE"
    }
    info_types {
      name = "EMAIL_ADDRESS"
    }
    info_types {
      name = "PHONE_NUMBER"
    }
  }
}

resource "google_data_loss_prevention_deidentify_template" "sdp_deid_template" {
  parent       = "projects/${var.project_id}/locations/${var.region}"
  display_name = "HR Agent Zero-SPII De-identify Template (${var.env})"
  description  = "Replaces detected SPII with [REDACTED_PII]"

  deidentify_config {
    info_type_transformations {
      transformations {
        primitive_transformation {
          replace_config {
            new_value {
              string_value = "[REDACTED_PII]"
            }
          }
        }
      }
    }
  }
}

resource "google_monitoring_alert_policy" "availability_slo_burn" {
  display_name = "HR Agent 99.9% Availability SLO Fast Burn (${var.env})"
  project      = var.project_id
  combiner     = "OR"
  conditions {
    display_name = "1-minute service window bad_window breach"
    condition_threshold {
      filter          = "resource.type = \"cloud_run_revision\""
      duration        = "60s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0.05
    }
  }
}
