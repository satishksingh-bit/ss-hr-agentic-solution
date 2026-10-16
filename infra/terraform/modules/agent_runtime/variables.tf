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

variable "agent_service_account_email" {
  type = string
}

variable "cmek_key_id" {
  type = string
}
