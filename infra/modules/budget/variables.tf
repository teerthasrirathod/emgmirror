variable "billing_account" {
  description = "Billing account id (XXXXXX-XXXXXX-XXXXXX)."
  type        = string
}

variable "project_numbers" {
  description = "Project numbers the budget covers."
  type        = list(string)
}

variable "display_name" {
  description = "Budget name."
  type        = string
  default     = "CT platform - Rs 30,000 credits"
}

variable "amount" {
  description = "Budget amount in currency_code units."
  type        = number
  default     = 30000
}

variable "currency_code" {
  description = "Must equal the billing account currency (INR for an Indian billing account)."
  type        = string
  default     = "INR"
}

variable "start_date" {
  description = "Start of the credit budget period."
  type = object({
    year  = number
    month = number
    day   = number
  })
  default = { year = 2026, month = 11, day = 1 }
}

variable "monthly_amount" {
  description = "Monthly budget after credits (0 = none)."
  type        = number
  default     = 5000
}

variable "notification_channels" {
  description = "Monitoring notification channel ids (max 5)."
  type        = list(string)
  default     = []
}
