variable "project_id" {
  type    = string
  default = "hr-agent-dev"
}

variable "env" {
  type    = string
  default = "dev"
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
