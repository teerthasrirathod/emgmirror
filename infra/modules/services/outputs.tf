output "enabled" {
  description = "Enabled API names (use in depends_on)."
  value       = sort([for s in google_project_service.this : s.service])
}
