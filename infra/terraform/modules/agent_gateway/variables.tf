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

variable "workweek_mcp_url" {
  type = string
}

variable "servicedesk_mcp_url" {
  type = string
}
