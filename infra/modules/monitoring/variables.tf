variable "project_id" {
  description = "Project."
  type        = string
}

variable "prefix" {
  description = "Name prefix."
  type        = string
}

variable "alert_email" {
  description = "Email that receives alerts (set in tfvars, not committed)."
  type        = string
}

variable "api_domain" {
  description = "API host name for the uptime check."
  type        = string
}

variable "health_path" {
  description = "Health path."
  type        = string
  default     = "/healthz"
}

variable "api_service_name" {
  description = "Cloud Run API service name."
  type        = string
}

variable "worker_name" {
  description = "Cloud Run GPU job name."
  type        = string
}

variable "dead_letter_subscription" {
  description = "Dead-letter subscription name."
  type        = string
}
