variable "project_id" {
  description = "Project."
  type        = string
}

variable "authorized_domains" {
  description = "Domains allowed for sign-in redirects."
  type        = list(string)
}

variable "allow_self_signup" {
  description = "Allow users to create their own accounts."
  type        = bool
  default     = false
}

variable "mfa_state" {
  description = "DISABLED, ENABLED or MANDATORY."
  type        = string
  default     = "MANDATORY"
}
