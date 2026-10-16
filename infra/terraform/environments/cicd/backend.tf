terraform {
  backend "gcs" {
    bucket = "hr-agent-tfstate-cicd"
    prefix = "terraform/state/cicd"
  }
}
