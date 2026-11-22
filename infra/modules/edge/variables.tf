variable "project_id" {
  description = "Project."
  type        = string
}

variable "prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "region" {
  description = "Region of the Cloud Run API."
  type        = string
}

variable "domain" {
  description = "Viewer host name, e.g. staging.example.in (DNS A record -> lb_ip)."
  type        = string
}

variable "api_domain" {
  description = "API host name, e.g. api.staging.example.in (DNS A record -> lb_ip)."
  type        = string
}

variable "api_service_name" {
  description = "Cloud Run API service name."
  type        = string
}

variable "viewer_bucket" {
  description = "Viewer bucket name."
  type        = string
}

variable "waf_preview" {
  description = "Run the OWASP rules in preview (log only). Start true in staging, tune, then false."
  type        = bool
  default     = false
}

variable "requests_per_minute_per_ip" {
  description = "General per-IP request limit per minute."
  type        = number
  default     = 300
}

variable "job_create_path" {
  description = "Path prefix of the job-creation endpoint (POST)."
  type        = string
  default     = "/jobs"
}

variable "job_create_limit_per_10min" {
  description = "Job creations allowed per IP per 10 minutes."
  type        = number
  default     = 10
}

variable "blocked_regions" {
  description = "ISO 3166-1 alpha-2 region codes to block (empty = none)."
  type        = list(string)
  default     = []
}

variable "viewer_response_headers" {
  description = "Security headers on viewer responses."
  type        = list(string)
  default = [
    "Strict-Transport-Security: max-age=31536000; includeSubDomains",
    "X-Content-Type-Options: nosniff",
    "X-Frame-Options: DENY",
    "Referrer-Policy: no-referrer",
    "Permissions-Policy: camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy: same-origin",
    "Cross-Origin-Embedder-Policy: require-corp",
    "Content-Security-Policy-Report-Only: default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; connect-src 'self' https:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
  ]
}

variable "api_response_headers" {
  description = "Security headers on API responses."
  type        = list(string)
  default = [
    "Strict-Transport-Security: max-age=31536000; includeSubDomains",
    "X-Content-Type-Options: nosniff",
    "Cache-Control: no-store",
    "Referrer-Policy: no-referrer",
  ]
}
