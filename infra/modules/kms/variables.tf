variable "project_id" {
  description = "Project that holds the key ring."
  type        = string
}

variable "location" {
  description = "Key ring location (must match the region of the data it protects)."
  type        = string
}

variable "key_ring_name" {
  description = "Key ring name. Key rings cannot be deleted, so pick a stable name."
  type        = string
}

variable "keys" {
  description = "Crypto key names to create."
  type        = list(string)
}

variable "encrypters" {
  description = "Map of key name to members granted roles/cloudkms.cryptoKeyEncrypterDecrypter."
  type        = map(list(string))
  default     = {}
}

variable "rotation_period" {
  description = "Automatic rotation period."
  type        = string
  default     = "7776000s" # 90 days
}

variable "destroy_scheduled_duration" {
  description = "Delay before a destroyed key version is really destroyed."
  type        = string
  default     = "2592000s" # 30 days
}

variable "protection_level" {
  description = "SOFTWARE or HSM."
  type        = string
  default     = "SOFTWARE"
  validation {
    condition     = contains(["SOFTWARE", "HSM"], var.protection_level)
    error_message = "protection_level must be SOFTWARE or HSM."
  }
}

variable "labels" {
  description = "Labels for the keys."
  type        = map(string)
  default     = {}
}
