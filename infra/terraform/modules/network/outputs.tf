output "vpc_id" {
  value = google_compute_network.hr_vpc.id
}

output "subnetwork_id" {
  value = google_compute_subnetwork.serverless_egress.id
}
