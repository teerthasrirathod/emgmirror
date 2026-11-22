output "jobs_topic_id" {
  description = "Jobs topic id (projects/P/topics/T)."
  value       = google_pubsub_topic.jobs.id
}

output "jobs_topic_name" {
  description = "Jobs topic short name ($JOBS_TOPIC)."
  value       = google_pubsub_topic.jobs.name
}

output "dead_letter_topic_id" {
  description = "Dead-letter topic id."
  value       = google_pubsub_topic.dead_letter.id
}

output "dead_letter_subscription" {
  description = "Dead-letter subscription name (alert on its backlog)."
  value       = google_pubsub_subscription.dead_letter.name
}
