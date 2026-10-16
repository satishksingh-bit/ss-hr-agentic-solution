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

variable "subnet_cidr" {
  type    = string
  default = "10.24.0.0/24"
}
