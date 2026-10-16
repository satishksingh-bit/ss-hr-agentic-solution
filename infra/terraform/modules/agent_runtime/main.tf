resource "terraform_data" "vertex_agent_engine_blue" {
  input = {
    project_id         = var.project_id
    region             = var.region
    engine_name        = "hr-coordinator-${var.env}-blue"
    adk_version        = "2.1.0"
    primary_model      = "gemini-3.8-flash"
    fallback_model     = "gemini-3.5-flash"
    complex_model      = "gemini-2.5-pro"
    service_account    = var.agent_service_account_email
    encryption_kms_key = var.cmek_key_id
    memory_bank        = "DISABLED"
  }
}

resource "terraform_data" "vertex_agent_engine_green" {
  input = {
    project_id         = var.project_id
    region             = var.region
    engine_name        = "hr-coordinator-${var.env}-green"
    adk_version        = "2.1.0"
    primary_model      = "gemini-3.8-flash"
    fallback_model     = "gemini-3.5-flash"
    complex_model      = "gemini-2.5-pro"
    service_account    = var.agent_service_account_email
    encryption_kms_key = var.cmek_key_id
    memory_bank        = "DISABLED"
  }
}
