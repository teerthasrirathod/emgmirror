output "secret_ids" {
  description = "Map of secret id to resource id."
  value       = { for k, v in google_secret_manager_secret.this : k => v.id }
}
