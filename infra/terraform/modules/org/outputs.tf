output "enabled_services" {
  value       = [for s in google_project_service.enabled_apis : s.service]
  description = "List of enabled GCP APIs"
}
