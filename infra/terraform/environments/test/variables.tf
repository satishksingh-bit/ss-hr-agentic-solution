variable "project_id" {
  type    = string
  default = "hr-agent-test"
}

variable "env" {
  type    = string
  default = "test"
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
