output "connection_name" {
  description = "Instance connection name (PROJECT:REGION:INSTANCE)."
  value       = google_sql_database_instance.this.connection_name
}

output "private_ip" {
  description = "Private IP address."
  value       = google_sql_database_instance.this.private_ip_address
}

output "instance_name" {
  description = "Instance name."
  value       = google_sql_database_instance.this.name
}

output "database_name" {
  description = "Database name."
  value       = google_sql_database.this.name
}
