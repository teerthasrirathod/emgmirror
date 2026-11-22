output "network_id" {
  description = "VPC id."
  value       = google_compute_network.this.id
}

output "network_name" {
  description = "VPC name."
  value       = google_compute_network.this.name
}

output "subnet_id" {
  description = "Serverless subnet id."
  value       = google_compute_subnetwork.serverless.id
}

output "psa_connection" {
  description = "Private services access connection (depend on it before creating Cloud SQL)."
  value       = google_service_networking_connection.psa.id
}
