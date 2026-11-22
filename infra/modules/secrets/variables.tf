variable "project_id" {
  description = "Project for the secrets."
  type        = string
}

variable "location" {
  description = "Single replica location (India)."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK key."
  type        = string
}

variable "secret_ids" {
  description = "Secret ids to create (values are added out of band)."
  type        = list(string)
}

variable "accessors" {
  description = "Who may read which secret."
  type = list(object({
    secret = string
    member = string
  }))
  default = []
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
