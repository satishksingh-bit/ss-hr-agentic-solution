resource "google_discovery_engine_data_store" "hr_policy_store" {
  location                    = var.search_location
  data_store_id               = "hr-policy-corpus-${var.env}"
  display_name                = "HR Policy Grounded Corpus (${var.env})"
  industry_vertical           = "GENERIC"
  content_config              = "CONTENT_REQUIRED"
  solution_types              = ["SOLUTION_TYPE_SEARCH"]
  create_advanced_site_search = false
  project                     = var.project_id
}

resource "google_discovery_engine_search_engine" "hr_policy_search_engine" {
  engine_id      = "hr-policy-engine-${var.env}"
  collection_id  = "default_collection"
  location       = var.search_location
  project        = var.project_id
  display_name   = "HR Policy Search Engine (${var.env})"
  data_store_ids = [google_discovery_engine_data_store.hr_policy_store.data_store_id]

  search_engine_config {
    search_tier    = "SEARCH_TIER_ENTERPRISE"
    search_add_ons = ["SEARCH_ADD_ON_LLM"]
  }
}

resource "google_storage_bucket" "policy_corpus_bucket" {
  name                        = "${var.project_id}-hr-policy-docs-${var.env}"
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  encryption {
    default_kms_key_name = var.cmek_key_id
  }
}
