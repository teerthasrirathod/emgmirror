output "oidc_issuer" {
  description = "Issuer of Identity Platform ID tokens ($OIDC_ISSUER)."
  value       = "https://securetoken.google.com/${var.project_id}"
}

output "oidc_audience" {
  description = "Audience of Identity Platform ID tokens ($OIDC_AUDIENCE)."
  value       = var.project_id
}
