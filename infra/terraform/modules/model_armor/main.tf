resource "terraform_data" "model_armor_templates" {
  input = {
    project_id              = var.project_id
    region                  = var.region
    input_template_id       = "hr-agent-input-safety-${var.env}"
    output_template_id      = "hr-agent-output-safety-${var.env}"
    prompt_injection_filter = "BLOCK_MEDIUM_AND_ABOVE"
    jailbreak_filter        = "BLOCK_MEDIUM_AND_ABOVE"
    sdp_deidentify_enabled  = true
    platform_logging        = false
  }
}

resource "google_model_armor_template" "input_safety_template" {
  location    = var.region
  project     = var.project_id
  template_id = "hr-agent-input-safety-${var.env}"

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
    sdp_settings {
      advanced_config {
        inspect_template    = "projects/${var.project_id}/locations/${var.region}/inspectTemplates/hr-agent-sdp-inspect-${var.env}"
        deidentify_template = "projects/${var.project_id}/locations/${var.region}/deidentifyTemplates/hr-agent-sdp-deid-${var.env}"
      }
    }
  }

  template_metadata {
    log_template_operations = false
    log_sanitize_operations = false
  }
}

resource "google_model_armor_template" "output_safety_template" {
  location    = var.region
  project     = var.project_id
  template_id = "hr-agent-output-safety-${var.env}"

  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }
    sdp_settings {
      advanced_config {
        inspect_template    = "projects/${var.project_id}/locations/${var.region}/inspectTemplates/hr-agent-sdp-inspect-${var.env}"
        deidentify_template = "projects/${var.project_id}/locations/${var.region}/deidentifyTemplates/hr-agent-sdp-deid-${var.env}"
      }
    }
  }

  template_metadata {
    log_template_operations = false
    log_sanitize_operations = false
  }
}
