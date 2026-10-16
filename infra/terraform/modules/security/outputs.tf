output "cmek_key_id" {
  value = google_kms_crypto_key.cmek_data.id
}

output "uct_signing_key_id" {
  value = google_kms_crypto_key.uct_signing_key.id
}

output "ct_signing_key_id" {
  value = google_kms_crypto_key.ct_signing_key.id
}

output "cloud_armor_policy_id" {
  value = google_compute_security_policy.cloud_armor_edge.id
}
