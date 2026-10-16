module "org" {
  source     = "../../modules/org"
  project_id = var.project_id
}

resource "google_artifact_registry_repository" "hr_agent_images" {
  location      = var.region
  project       = var.project_id
  repository_id = "hr-agent-images"
  description   = "Container image registry for HR Agentic Solution (MVP 1)"
  format        = "DOCKER"

  docker_config {
    immutable_tags = true
  }
}

resource "google_cloudbuild_worker_pool" "private_pool" {
  name     = "hr-agent-private-pool"
  location = var.region
  project  = var.project_id

  worker_config {
    disk_size_gb   = 100
    machine_type   = "e2-standard-4"
    no_external_ip = true
  }
}

locals {
  tf_state_envs = toset(["cicd", "dev", "test", "prodpilot"])
}

resource "google_storage_bucket" "tf_state_buckets" {
  for_each                    = local.tf_state_envs
  name                        = "hr-agent-tfstate-${each.value}"
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }
}

resource "google_service_account" "cloudbuild_release_sa" {
  account_id   = "cloudbuild-release-sa"
  display_name = "Cloud Build Release & Deployment Pipeline SA"
  project      = var.project_id
}
