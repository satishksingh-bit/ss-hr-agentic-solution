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

variable "cmek_key_id" {
  type = string
}
