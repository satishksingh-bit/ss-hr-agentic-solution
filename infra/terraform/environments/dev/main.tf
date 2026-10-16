module "org" {
  source     = "../../modules/org"
  project_id = var.project_id
}

module "network" {
  source     = "../../modules/network"
  project_id = var.project_id
  env        = var.env
  region     = var.region
}

module "security" {
  source     = "../../modules/security"
  project_id = var.project_id
  env        = var.env
  region     = var.region
}

module "identity" {
  source     = "../../modules/identity"
  project_id = var.project_id
  env        = var.env
}

module "data" {
  source      = "../../modules/data"
  project_id  = var.project_id
  env         = var.env
  region      = var.region
  cmek_key_id = module.security.cmek_key_id
}

module "search" {
  source      = "../../modules/search"
  project_id  = var.project_id
  env         = var.env
  region      = var.region
  cmek_key_id = module.security.cmek_key_id
}

module "model_armor" {
  source     = "../../modules/model_armor"
  project_id = var.project_id
  env        = var.env
  region     = var.region
}

module "run_services" {
  source                 = "../../modules/run_services"
  project_id             = var.project_id
  env                    = var.env
  region                 = var.region
  service_account_emails = module.identity.service_account_emails
  min_instances_bff      = var.min_instances_bff
  min_instances_mcp      = var.min_instances_mcp
}

module "agent_runtime" {
  source                      = "../../modules/agent_runtime"
  project_id                  = var.project_id
  env                         = var.env
  region                      = var.region
  agent_service_account_email = module.identity.service_account_emails["hr-agent-sa"]
  cmek_key_id                 = module.security.cmek_key_id
}

module "agent_gateway" {
  source                      = "../../modules/agent_gateway"
  project_id                  = var.project_id
  env                         = var.env
  region                      = var.region
  agent_service_account_email = module.identity.service_account_emails["hr-agent-sa"]
  workweek_mcp_url            = module.run_services.service_urls["workweek-mcp"]
  servicedesk_mcp_url         = module.run_services.service_urls["servicedesk-mcp"]
}

module "observability" {
  source     = "../../modules/observability"
  project_id = var.project_id
  env        = var.env
  region     = var.region
}
