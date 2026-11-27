# Global external Application Load Balancer:
#   https://<domain>/...      -> viewer bucket via Cloud CDN (static, public, no personal data)
#   https://<api_domain>/...  -> Cloud Run API via serverless NEG, protected by Cloud Armor
# Google-managed certificate, TLS 1.2+ (MODERN), HTTP redirected to HTTPS.
# The load balancer, certificate and Cloud Armor are global resources; they terminate TLS
# at Google's edge but store no customer content (see MEITY_AND_RESIDENCY.md).

resource "google_compute_global_address" "this" {
  project = var.project_id
  name    = "${var.prefix}-lb-ip"
}

resource "google_compute_managed_ssl_certificate" "this" {
  project = var.project_id
  name    = "${var.prefix}-cert"

  managed {
    domains = [var.domain, var.api_domain]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_ssl_policy" "this" {
  project         = var.project_id
  name            = "${var.prefix}-tls"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

# ------------------------------------------------------------------ Cloud Armor ---------
resource "google_compute_security_policy" "api" {
  project     = var.project_id
  name        = "${var.prefix}-api-armor"
  description = "WAF and per-IP rate limits for the CT API."
  type        = "CLOUD_ARMOR"

  adaptive_protection_config {
    layer_7_ddos_defense_config {
      enable = true
    }
  }

  advanced_options_config {
    json_parsing = "STANDARD"
    log_level    = "VERBOSE"
  }

  # OWASP CRS preconfigured rules (sensitivity 1 = fewest false positives).
  dynamic "rule" {
    for_each = {
      1000 = "sqli-v33-stable"
      1001 = "xss-v33-stable"
      1002 = "lfi-v33-stable"
      1003 = "rfi-v33-stable"
      1004 = "rce-v33-stable"
      1005 = "methodenforcement-v33-stable"
      1006 = "scannerdetection-v33-stable"
      1007 = "protocolattack-v33-stable"
      1008 = "sessionfixation-v33-stable"
    }
    content {
      action   = var.waf_preview ? "allow" : "deny(403)"
      priority = rule.key
      preview  = var.waf_preview
      match {
        expr {
          expression = "evaluatePreconfiguredWaf('${rule.value}', {'sensitivity': 1})"
        }
      }
      description = "OWASP CRS ${rule.value}"
    }
  }

  # Log4j / CVE canary rule.
  rule {
    action   = "deny(403)"
    priority = 1010
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('cve-canary')"
      }
    }
    description = "Log4j CVE-2021-44228 and similar"
  }

  # Strict limit on job creation (uploads start a GPU job and cost money).
  rule {
    action   = "throttle"
    priority = 2000
    match {
      expr {
        expression = "request.method == 'POST' && request.path.startsWith('${var.job_create_path}')"
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = var.job_create_limit_per_10min
        interval_sec = 600
      }
    }
    description = "Per-IP limit on job creation"
  }

  # General per-IP rate limit with a temporary ban for abusers.
  rule {
    action   = "rate_based_ban"
    priority = 3000
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      enforce_on_key   = "IP"
      ban_duration_sec = 600
      rate_limit_threshold {
        count        = var.requests_per_minute_per_ip
        interval_sec = 60
      }
    }
    description = "Per-IP rate limit, 10 min ban when exceeded"
  }

  dynamic "rule" {
    for_each = length(var.blocked_regions) > 0 ? [1] : []
    content {
      action   = "deny(403)"
      priority = 4000
      match {
        expr {
          expression = join(" || ", [for r in var.blocked_regions : "origin.region_code == '${r}'"])
        }
      }
      description = "Geo block list"
    }
  }

  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default allow"
  }
}

# ------------------------------------------------------------------- backends -----------
resource "google_compute_region_network_endpoint_group" "api" {
  project               = var.project_id
  name                  = "${var.prefix}-api-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = var.api_service_name
  }
}

resource "google_compute_backend_service" "api" {
  project               = var.project_id
  name                  = "${var.prefix}-api"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.api.id
  enable_cdn            = false

  backend {
    group = google_compute_region_network_endpoint_group.api.id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }

  custom_response_headers = var.api_response_headers
}

resource "google_compute_backend_bucket" "viewer" {
  project     = var.project_id
  name        = "${var.prefix}-viewer"
  bucket_name = var.viewer_bucket
  enable_cdn  = true

  cdn_policy {
    cache_mode        = "CACHE_ALL_STATIC"
    default_ttl       = 3600
    max_ttl           = 86400
    client_ttl        = 3600
    negative_caching  = true
    serve_while_stale = 86400
  }

  custom_response_headers = var.viewer_response_headers
}

# -------------------------------------------------------------------- routing -----------
resource "google_compute_url_map" "https" {
  project         = var.project_id
  name            = "${var.prefix}-https"
  default_service = google_compute_backend_bucket.viewer.id

  host_rule {
    hosts        = [var.api_domain]
    path_matcher = "api"
  }

  host_rule {
    hosts        = [var.domain]
    path_matcher = "viewer"
  }

  path_matcher {
    name            = "api"
    default_service = google_compute_backend_service.api.id
  }

  path_matcher {
    name            = "viewer"
    default_service = google_compute_backend_bucket.viewer.id
  }
}

resource "google_compute_target_https_proxy" "this" {
  project          = var.project_id
  name             = "${var.prefix}-https"
  url_map          = google_compute_url_map.https.id
  ssl_certificates = [google_compute_managed_ssl_certificate.this.id]
  ssl_policy       = google_compute_ssl_policy.this.id
}

resource "google_compute_global_forwarding_rule" "https" {
  project               = var.project_id
  name                  = "${var.prefix}-https"
  target                = google_compute_target_https_proxy.this.id
  ip_address            = google_compute_global_address.this.id
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_url_map" "redirect" {
  project = var.project_id
  name    = "${var.prefix}-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  project = var.project_id
  name    = "${var.prefix}-http-redirect"
  url_map = google_compute_url_map.redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  project               = var.project_id
  name                  = "${var.prefix}-http"
  target                = google_compute_target_http_proxy.redirect.id
  ip_address            = google_compute_global_address.this.id
  port_range            = "80"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}
