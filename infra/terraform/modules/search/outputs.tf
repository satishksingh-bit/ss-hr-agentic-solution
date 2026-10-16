output "data_store_id" {
  value = google_discovery_engine_data_store.hr_policy_store.data_store_id
}

output "policy_bucket_name" {
  value = google_storage_bucket.policy_corpus_bucket.name
}
