output "service_urls" {
  value = { for k, s in google_cloud_run_v2_service.run_services : k => s.uri }
}
