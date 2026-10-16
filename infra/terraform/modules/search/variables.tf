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

variable "search_location" {
  type    = string
  default = "eu"
}

variable "cmek_key_id" {
  type = string
}
