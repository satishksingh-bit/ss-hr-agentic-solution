terraform {
  backend "gcs" {
    bucket = "hr-agent-tfstate-test"
    prefix = "terraform/state/test"
  }
}
