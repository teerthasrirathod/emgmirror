variable "project_id" {
  description = "Project."
  type        = string
}

variable "name" {
  description = "Job or service name."
  type        = string
}

variable "region" {
  description = "Region with L4 GPUs for Cloud Run (asia-south1, see GPU_DECISION.md)."
  type        = string
}

variable "mode" {
  description = "job (Cloud Run job, default) or service (Cloud Run GPU service with Pub/Sub push)."
  type        = string
  default     = "job"
  validation {
    condition     = contains(["job", "service"], var.mode)
    error_message = "mode must be job or service."
  }
}

variable "launch_stage" {
  description = "Cloud Run launch stage; set BETA if GPU in the region is still in preview."
  type        = string
  default     = "GA"
}

variable "image" {
  description = "Initial worker image (CI replaces it with a signed digest)."
  type        = string
}

variable "command" {
  description = "Container entrypoint for job mode."
  type        = list(string)
  default     = ["python", "-m", "emgmirror.ctservice.worker"]
}

variable "gpu_type" {
  description = "Cloud Run accelerator."
  type        = string
  default     = "nvidia-l4"
}

variable "cpu" {
  description = "vCPU (L4 needs at least 4)."
  type        = string
  default     = "8"
}

variable "memory" {
  description = "Memory (L4 needs at least 16Gi)."
  type        = string
  default     = "32Gi"
}

variable "timeout_seconds" {
  description = "Hard time limit per CT."
  type        = number
  default     = 1800
}

variable "max_retries" {
  description = "Task retries inside one execution."
  type        = number
  default     = 1
}

variable "max_parallel_jobs" {
  description = "Service mode: maximum GPU instances (bounded by GPU quota)."
  type        = number
  default     = 3
}

variable "service_account_email" {
  description = "Worker runtime service account."
  type        = string
}

variable "dispatcher_service_account_email" {
  description = "Eventarc/Workflows identity (job mode)."
  type        = string
}

variable "push_service_account_email" {
  description = "Pub/Sub push identity (service mode)."
  type        = string
}

variable "deployer_member" {
  description = "CI deployer member."
  type        = string
}

variable "network_id" {
  description = "VPC with no internet egress."
  type        = string
}

variable "subnet_id" {
  description = "Subnet for Direct VPC egress."
  type        = string
}

variable "jobs_topic_id" {
  description = "Jobs topic id."
  type        = string
}

variable "dead_letter_topic_id" {
  description = "Dead-letter topic id."
  type        = string
}

variable "workflow_kms_key_id" {
  description = "CMEK key for workflow execution data (null = Google-managed)."
  type        = string
  default     = null
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

variable "deletion_protection" {
  description = "Protect from terraform destroy."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
