output "firestore_database_names" {
  value = [for db in google_firestore_database.isolated_dbs : db.name]
}

output "analytics_dataset_id" {
  value = google_bigquery_dataset.analytics_dataset.dataset_id
}
