# Google Identity Platform (OIDC issuer for the API: $OIDC_ISSUER / $OIDC_AUDIENCE).
# Email + password with TOTP multi-factor. Self sign-up is off by default: staging and the
# clinical tier are invite-only. Identity Platform is a global service: it stores account
# data (email, display name), never CT data (see MEITY_AND_RESIDENCY.md).
resource "google_identity_platform_config" "this" {
  project                    = var.project_id
  autodelete_anonymous_users = true
  authorized_domains         = var.authorized_domains

  sign_in {
    allow_duplicate_emails = false

    email {
      enabled           = true
      password_required = true
    }
  }

  mfa {
    state = var.mfa_state

    provider_configs {
      state = "ENABLED"
      totp_provider_config {
        adjacent_intervals = 1
      }
    }
  }

  client {
    permissions {
      disabled_user_signup   = !var.allow_self_signup
      disabled_user_deletion = false
    }
  }

  monitoring {
    request_logging {
      enabled = true
    }
  }
}
