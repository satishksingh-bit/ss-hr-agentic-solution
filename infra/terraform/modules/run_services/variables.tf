variable "project_id" {
  type = string
}

variable "env" {
  type = string
}

variable "region" {
  type    = string
  default = "europe-west4"
}

variable "service_account_emails" {
  type = map(string)
}

variable "min_instances_bff" {
  type    = number
  default = 1
}

variable "min_instances_mcp" {
  type    = number
  default = 1
}

variable "artifact_repo" {
  type    = string
  default = "europe-west4-docker.pkg.dev/hr-agent-cicd/hr-agent-images"
}

variable "image_tag" {
  type    = string
  default = "v0.1.0-mvp1"
}
