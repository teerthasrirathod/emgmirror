output "emails" {
  description = "Map of role name to service account email."
  value       = { for k, v in google_service_account.this : k => v.email }
}

output "members" {
  description = "Map of role name to IAM member string (serviceAccount:...)."
  value       = { for k, v in google_service_account.this : k => v.member }
}

output "ids" {
  description = "Map of role name to service account resource name."
  value       = { for k, v in google_service_account.this : k => v.name }
}
