variable "project_id" {
  description = "Existing billed GCP project. Provisioning is an explicit operator action."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Use a valid existing GCP project ID."
  }
}
variable "customer_id" {
  description = "Opaque unique installation slug; do not use a private person's name."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,22}[a-z0-9]$", var.customer_id))
    error_message = "Use a 3–24 character lower-case installation slug."
  }
}
variable "region" {
  type    = string
  default = "us-central1"
  validation {
    condition     = startswith(var.region, "us-")
    error_message = "The initial pilot recipe requires a US region."
  }
}
variable "zone" {
  type    = string
  default = "us-central1-a"
}
variable "domain" {
  description = "Dedicated public DNS name; point its A record to the output address before ACME."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]+\\.[a-z]{2,}$", var.domain))
    error_message = "Use a lower-case DNS hostname without a scheme, path or credentials."
  }
}
variable "workspace_kind" {
  type    = string
  default = "organization"
  validation {
    condition     = contains(["organization", "personal"], var.workspace_kind)
    error_message = "Personal customers require a distinct installation."
  }
}
variable "app_image" {
  description = "Reviewed DOXA image in the configured Artifact Registry, pinned to sha256."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9.-]+-docker\\.pkg\\.dev/[a-z0-9/-]+@sha256:[a-f0-9]{64}$", var.app_image))
    error_message = "Pin a GCP Artifact Registry image by its real sha256 digest."
  }
}
variable "caddy_image" {
  description = "Reviewed official Caddy image, e.g. caddy@sha256:<verified digest>; no floating tags."
  type        = string
  validation {
    condition     = can(regex("^caddy@sha256:[a-f0-9]{64}$", var.caddy_image))
    error_message = "Pin the official Caddy image to a verified sha256 digest."
  }
}
variable "artifact_repository" {
  type    = string
  default = "doxa"
}
variable "access_secret_id" {
  description = "Existing Secret Manager secret ID holding the private access.json, never a secret value."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,255}$", var.access_secret_id))
    error_message = "Use a Secret Manager secret ID."
  }
}
variable "environment_secret_id" {
  description = "Existing secret ID holding the private pilot.env including stable vault and ledger keys."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,255}$", var.environment_secret_id))
    error_message = "Use a Secret Manager secret ID."
  }
}
variable "pricing_secret_id" {
  description = "Existing secret ID holding private server-owned provider/model/endpoint token rates."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,255}$", var.pricing_secret_id))
    error_message = "Use a Secret Manager secret ID."
  }
}
variable "machine_type" {
  type    = string
  default = "e2-standard-2"
}
variable "data_disk_gb" {
  type    = number
  default = 50
  validation {
    condition     = var.data_disk_gb >= 30
    error_message = "Allow at least 30 GiB; measured exports need scratch space beyond source storage."
  }
}
variable "initialize_empty_workspace" {
  description = "Explicit FIRST installation only: permit an empty deletion-ledger initialization when both stores are empty."
  type        = bool
  default     = false
}
