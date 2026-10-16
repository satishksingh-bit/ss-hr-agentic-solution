resource "google_compute_network" "hr_vpc" {
  name                    = "hr-agent-vpc-${var.env}"
  project                 = var.project_id
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "serverless_egress" {
  name                     = "hr-agent-egress-${var.env}"
  project                  = var.project_id
  region                   = var.region
  network                  = google_compute_network.hr_vpc.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

resource "google_compute_address" "egress_static_ip" {
  name    = "hr-agent-nat-ip-${var.env}"
  project = var.project_id
  region  = var.region
}

resource "google_compute_router" "nat_router" {
  name    = "hr-agent-router-${var.env}"
  project = var.project_id
  region  = var.region
  network = google_compute_network.hr_vpc.id
}

resource "google_compute_router_nat" "egress_nat" {
  name                               = "hr-agent-nat-${var.env}"
  project                            = var.project_id
  router                             = google_compute_router.nat_router.name
  region                             = var.region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.egress_static_ip.self_link]
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

resource "google_compute_ssl_policy" "tls13_policy" {
  name            = "hr-agent-tls13-policy-${var.env}"
  project         = var.project_id
  profile         = "RESTRICTED"
  min_tls_version = "TLS_1_3"
}

resource "google_compute_region_network_endpoint_group" "bff_serverless_neg" {
  name                  = "hr-chat-bff-neg-${var.env}"
  project               = var.project_id
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = "hr-chat-bff-${var.env}"
  }
}

resource "google_dns_managed_zone" "private_googleapis" {
  name        = "private-googleapis-${var.env}"
  project     = var.project_id
  dns_name    = "googleapis.com."
  description = "Private Google Access DNS zone for restricted.googleapis.com"
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.hr_vpc.id
    }
  }
}
