output "upload_bucket" {
  description = "Uploads bucket name ($UPLOAD_BUCKET)."
  value       = google_storage_bucket.phi["uploads"].name
}

output "results_bucket" {
  description = "Results bucket name ($RESULTS_BUCKET)."
  value       = google_storage_bucket.phi["results"].name
}

output "viewer_bucket" {
  description = "Viewer bucket name."
  value       = google_storage_bucket.viewer.name
}

output "logs_bucket" {
  description = "Access-log bucket name."
  value       = google_storage_bucket.logs.name
}
