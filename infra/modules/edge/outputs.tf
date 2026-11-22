output "lb_ip" {
  description = "Global IP: point the viewer and API DNS A records here."
  value       = google_compute_global_address.this.address
}

output "url_map_name" {
  description = "URL map (CDN cache invalidation target)."
  value       = google_compute_url_map.https.name
}

output "security_policy" {
  description = "Cloud Armor policy name."
  value       = google_compute_security_policy.api.name
}
