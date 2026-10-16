terraform {
  backend "gcs" {
    bucket = "hr-agent-tfstate-dev"
    prefix = "terraform/state/dev"
  }
}
