output "key_ids" {
  description = "Map of key name to crypto key id."
  value       = { for k, v in google_kms_crypto_key.this : k => v.id }
}

output "key_ring_id" {
  description = "Key ring id."
  value       = google_kms_key_ring.this.id
}
