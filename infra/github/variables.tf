variable "state_encryption_passphrase" {
  description = "Passphrase for PBKDF2 encryption of state and saved plans."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.state_encryption_passphrase) >= 16
    error_message = "The state encryption passphrase must contain at least 16 characters."
  }
}
