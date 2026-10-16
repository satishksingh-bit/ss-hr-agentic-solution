locals {
  firestore_databases = toset([
    "hr-personas",
    "hr-flags",
    "hr-plans",
    "hr-bff",
    "hr-idem-ww",
    "hr-idem-si",
  ])
}

resource "google_firestore_database" "isolated_dbs" {
  for_each                          = local.firestore_databases
  project                           = var.project_id
  name                              = each.value
  location_id                       = var.region
  type                              = "FIRESTORE_NATIVE"
  delete_protection_state           = "DELETE_PROTECTION_ENABLED"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"

  cmek_config {
    kms_key_name = var.cmek_key_id
  }
}

resource "google_bigquery_dataset" "analytics_dataset" {
  dataset_id  = "hr_agent_analytics_${var.env}"
  project     = var.project_id
  location    = var.region
  description = "Pseudonymized HR Agent Audit & Deflection Analytics (400d WORM audit)"

  default_encryption_configuration {
    kms_key_name = var.cmek_key_id
  }
}
