variable "project_id" {
  description = "Project for the topics."
  type        = string
}

variable "prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK key."
  type        = string
}

variable "allowed_regions" {
  description = "Regions where Pub/Sub may store messages."
  type        = list(string)
}

variable "pubsub_service_agent" {
  description = "Email of the Pub/Sub service agent (service-NUMBER@gcp-sa-pubsub.iam.gserviceaccount.com)."
  type        = string
}

variable "publishers" {
  description = "Members allowed to publish jobs."
  type        = list(string)
  default     = []
}

variable "dead_letter_publishers" {
  description = "Members allowed to publish to the dead-letter topic (the dispatcher)."
  type        = list(string)
  default     = []
}

variable "push_endpoint" {
  description = "Worker push URL (https://.../pubsub). null = no push subscription (job mode)."
  type        = string
  default     = null
}

variable "push_service_account_email" {
  description = "Identity Pub/Sub uses to sign the OIDC token on push."
  type        = string
  default     = null
}

variable "push_audience" {
  description = "OIDC audience on push (the worker service URL)."
  type        = string
  default     = null
}

variable "max_delivery_attempts" {
  description = "Deliveries before a message goes to the dead-letter topic."
  type        = number
  default     = 5
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
