resource "google_project_service" "enabled_apis" {
  for_each           = toset(var.enabled_services)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_org_policy_policy" "domain_restricted_sharing" {
  name   = "projects/${var.project_id}/policies/iam.allowedPolicyMemberDomains"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      values {
        allowed_values = [var.organization_domain_id]
      }
    }
  }
}

resource "google_org_policy_policy" "resource_locations" {
  name   = "projects/${var.project_id}/policies/gcp.resourceLocations"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      values {
        allowed_values = ["in:europe-west4-locations", "in:eu-locations"]
      }
    }
  }
}

resource "google_org_policy_policy" "disable_sa_key_creation" {
  name   = "projects/${var.project_id}/policies/iam.disableServiceAccountKeyCreation"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }
}

resource "google_org_policy_policy" "uniform_bucket_level_access" {
  name   = "projects/${var.project_id}/policies/storage.uniformBucketLevelAccess"
  parent = "projects/${var.project_id}"

  spec {
    rules {
      enforce = "TRUE"
    }
  }
}

resource "google_access_context_manager_service_perimeter" "hr_agent_vpc_sc_perimeter" {
  parent         = "accessPolicies/000000000000"
  name           = "accessPolicies/000000000000/servicePerimeters/hr_agent_perimeter"
  title          = "HR Agentic Solution VPC-SC Perimeter (${var.project_id})"
  perimeter_type = "PERIMETER_TYPE_REGULAR"

  status {
    resources = ["projects/${var.project_id}"]
    restricted_services = [
      "aiplatform.googleapis.com",
      "discoveryengine.googleapis.com",
      "firestore.googleapis.com",
      "secretmanager.googleapis.com",
      "cloudkms.googleapis.com",
      "bigquery.googleapis.com",
      "storage.googleapis.com",
      "run.googleapis.com",
      "logging.googleapis.com",
      "monitoring.googleapis.com",
      "modelarmor.googleapis.com",
      "artifactregistry.googleapis.com",
    ]
  }
}
