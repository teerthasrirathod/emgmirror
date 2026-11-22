output "notification_channel" {
  description = "Email notification channel id."
  value       = google_monitoring_notification_channel.email.id
}
