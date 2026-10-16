variable "project_id" {
  type    = string
  default = "hr-agent-cicd"
}

variable "env" {
  type    = string
  default = "cicd"
}

variable "region" {
  type    = string
  default = "europe-west4"
}

variable "min_instances_bff" {
  type    = number
  default = 1
}

variable "min_instances_mcp" {
  type    = number
  default = 1
}
