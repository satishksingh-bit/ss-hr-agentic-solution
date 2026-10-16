variable "project_id" {
  type    = string
  default = "hr-agent-prodpilot"
}

variable "env" {
  type    = string
  default = "prodpilot"
}

variable "region" {
  type    = string
  default = "europe-west4"
}

variable "min_instances_bff" {
  type    = number
  default = 2
}

variable "min_instances_mcp" {
  type    = number
  default = 2
}
