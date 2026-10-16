terraform {
  backend "gcs" {
    bucket = "hr-agent-tfstate-prodpilot"
    prefix = "terraform/state/prodpilot"
  }
}
