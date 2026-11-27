# Budget against the Rs 30,000 Google Cloud credits (expire 13 Jan 2027).
# Credits are EXCLUDED, so the budget measures gross usage = credit burn; with credits
# included the net cost would read zero until the credits are gone.
# Alerts at 25/50/75/90 % of actual spend and at 100 % of forecast spend.
resource "google_billing_budget" "credits" {
  billing_account = var.billing_account
  display_name    = var.display_name

  budget_filter {
    projects               = [for n in var.project_numbers : "projects/${n}"]
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
    calendar_period        = null
    custom_period {
      start_date {
        year  = var.start_date.year
        month = var.start_date.month
        day   = var.start_date.day
      }
      end_date {
        year  = 2027
        month = 1
        day   = 13
      }
    }
  }

  amount {
    specified_amount {
      currency_code = var.currency_code
      units         = tostring(var.amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = [0.25, 0.5, 0.75, 0.9]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = var.notification_channels
    disable_default_iam_recipients   = false
  }
}

# Monthly budget that continues after the credits expire (alerts on real money).
resource "google_billing_budget" "monthly" {
  count = var.monthly_amount > 0 ? 1 : 0

  billing_account = var.billing_account
  display_name    = "${var.display_name} (monthly, after credits)"

  budget_filter {
    projects               = [for n in var.project_numbers : "projects/${n}"]
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
    calendar_period        = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = var.currency_code
      units         = tostring(var.monthly_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = [0.5, 0.9, 1.0]
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  all_updates_rule {
    monitoring_notification_channels = var.notification_channels
    disable_default_iam_recipients   = false
  }
}
