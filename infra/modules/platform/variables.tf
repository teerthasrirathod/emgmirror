variable "project_id" {
  description = "Google Cloud project id."
  type        = string
}

variable "project_number" {
  description = "Google Cloud project number (used to name service agents without API calls)."
  type        = string
}

variable "env" {
  description = "Environment short name: stg or prod."
  type        = string
  validation {
    condition     = contains(["stg", "prod"], var.env)
    error_message = "env must be stg or prod."
  }
}

variable "region" {
  description = "Region for all regional resources (data residency: India)."
  type        = string
  default     = "asia-south1"
  validation {
    condition     = contains(["asia-south1", "asia-south2"], var.region)
    error_message = "Only the India regions asia-south1 and asia-south2 are allowed."
  }
}

variable "domain" {
  description = "Viewer host name."
  type        = string
}

variable "api_domain" {
  description = "API host name."
  type        = string
}

variable "alert_email" {
  description = "Alert recipient (keep it in an uncommitted tfvars file or a CI variable)."
  type        = string
}

variable "deployer_principals" {
  description = "WIF principals allowed to impersonate the deployer."
  type        = list(string)
  default     = []
}

variable "artifact_extra_readers" {
  description = "Extra image readers, e.g. the prod deployer on the staging repository (promote the same signed digest)."
  type        = list(string)
  default     = []
}

variable "deploy_workloads" {
  description = "Create Cloud Run, the load balancer and alerts (false on the very first apply)."
  type        = bool
  default     = true
}

variable "api_image" {
  description = "Initial API image (CI deploys signed digests afterwards)."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "worker_image" {
  description = "Initial worker image (CI deploys signed digests afterwards)."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/job"
}

variable "worker_mode" {
  description = "job (Cloud Run job + Eventarc/Workflows) or service (Cloud Run GPU service + Pub/Sub push)."
  type        = string
  default     = "job"
}

variable "worker_launch_stage" {
  description = "Launch stage for the GPU worker resource."
  type        = string
  default     = "GA"
}

variable "worker_timeout_seconds" {
  description = "Hard time limit per CT job."
  type        = number
  default     = 1800
}

variable "worker_max_parallel" {
  description = "Maximum parallel GPU instances in service mode (GPU quota bound)."
  type        = number
  default     = 3
}

variable "api_max_instances" {
  description = "API autoscaling cap."
  type        = number
  default     = 5
}

variable "health_path" {
  description = "API health path."
  type        = string
  default     = "/healthz"
}

variable "subnet_cidr" {
  description = "Serverless subnet CIDR."
  type        = string
  default     = "10.10.0.0/24"
}

variable "db_tier" {
  description = "Cloud SQL tier."
  type        = string
  default     = "db-f1-micro"
}

variable "db_availability_type" {
  description = "ZONAL or REGIONAL."
  type        = string
  default     = "ZONAL"
}

variable "allow_self_signup" {
  description = "Identity Platform self sign-up."
  type        = bool
  default     = false
}

variable "waf_preview" {
  description = "Cloud Armor OWASP rules in preview mode."
  type        = bool
  default     = false
}

variable "requests_per_minute_per_ip" {
  description = "Per-IP rate limit."
  type        = number
  default     = 300
}

variable "security_log_retention_days" {
  description = "Security log retention (>= 180 for CERT-In)."
  type        = number
  default     = 180
}

variable "lock_security_logs" {
  description = "Lock the security log bucket retention (irreversible)."
  type        = bool
  default     = false
}

variable "enable_org_policies" {
  description = "Apply the org policy suggestions at project level (needs an organization)."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Deletion protection on Cloud SQL and Cloud Run."
  type        = bool
  default     = true
}

variable "force_destroy" {
  description = "Allow destroy of non-empty buckets."
  type        = bool
  default     = false
}

variable "labels" {
  description = "Extra labels."
  type        = map(string)
  default     = {}
}
