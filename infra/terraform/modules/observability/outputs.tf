output "audit_bucket_id" {
  value = google_logging_project_bucket_config.worm_audit_bucket.bucket_id
}

output "ops_bucket_id" {
  value = google_logging_project_bucket_config.ops_log_bucket.bucket_id
}
