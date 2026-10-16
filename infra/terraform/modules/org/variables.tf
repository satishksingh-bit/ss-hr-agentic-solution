variable "project_id" {
  type        = string
  description = "Target GCP project ID"
}

variable "organization_domain_id" {
  type        = string
  description = "Cloud Identity customer ID for Domain Restricted Sharing"
  default     = "C00synthetic01"
}

variable "enabled_services" {
  type        = list(string)
  description = "Required GCP APIs for HR Agentic Solution"
  default = [
    "aiplatform.googleapis.com",
    "compute.googleapis.com",
    "run.googleapis.com",
    "firestore.googleapis.com",
    "cloudkms.googleapis.com",
    "secretmanager.googleapis.com",
    "discoveryengine.googleapis.com",
    "modelarmor.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
  ]
}
