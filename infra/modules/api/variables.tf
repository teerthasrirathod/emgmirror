variable "project_id" {
  description = "Project."
  type        = string
}

variable "name" {
  description = "Service name."
  type        = string
}

variable "region" {
  description = "Region."
  type        = string
}

variable "image" {
  description = "Initial image (CI replaces it with a signed digest)."
  type        = string
}

variable "service_account_email" {
  description = "Runtime service account."
  type        = string
}

variable "deployer_member" {
  description = "CI deployer member."
  type        = string
}

variable "network_id" {
  description = "VPC for Direct VPC egress (Cloud SQL private IP)."
  type        = string
}

variable "subnet_id" {
  description = "Subnet for Direct VPC egress."
  type        = string
}

variable "env" {
  description = "Plain environment variables."
  type        = map(string)
  default     = {}
}

variable "secret_env" {
  description = "Environment variables from Secret Manager: name => secret id."
  type        = map(string)
  default     = {}
}

variable "health_path" {
  description = "Health check path (see RUNTIME_CONTRACT.md)."
  type        = string
  default     = "/healthz"
}

variable "cpu" {
  description = "vCPU limit."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory limit."
  type        = string
  default     = "512Mi"
}

variable "concurrency" {
  description = "Requests per instance."
  type        = number
  default     = 40
}

variable "min_instances" {
  description = "Minimum instances (0 = scale to zero)."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum instances (cost and abuse cap)."
  type        = number
  default     = 5
}

variable "deletion_protection" {
  description = "Protect the service from terraform destroy."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
