terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.14" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

variable "name" { type = string }
variable "region" { type = string }
variable "network_id" { type = string }
# tflint-ignore: terraform_unused_declarations
variable "subnet_ids" { type = list(string) } # contract parity; unused on GCP
# tflint-ignore: terraform_unused_declarations
variable "allowed_cidr" {
  type        = string
  description = "Unused on GCP (access is via private service networking on the VPC); kept for contract parity"
  default     = ""
}
variable "size" {
  type = string
  validation {
    condition     = contains(["small", "medium", "large"], var.size)
    error_message = "size must be small, medium or large."
  }
}
variable "db_name" {
  type    = string
  default = "ideas"
}
variable "db_user" {
  type    = string
  default = "ideas"
}
variable "storage_gb" {
  type    = number
  default = 20
}
variable "high_availability" {
  type    = bool
  default = false
}
variable "deletion_protection" {
  type    = bool
  default = false
}

locals {
  tier = {
    small  = "db-f1-micro"
    medium = "db-custom-1-3840"
    large  = "db-custom-2-7680"
  }
}

resource "random_password" "db" {
  length  = 24
  special = false
}

# Private services access: peers the VPC with Google's managed-services network
resource "google_compute_global_address" "private_range" {
  name          = "${var.name}-sql-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = var.network_id
}

resource "google_service_networking_connection" "private" {
  network                 = var.network_id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_range.name]
}

resource "google_sql_database_instance" "this" {
  name                = var.name
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.deletion_protection

  settings {
    edition           = "ENTERPRISE"
    tier              = local.tier[var.size]
    availability_type = var.high_availability ? "REGIONAL" : "ZONAL"
    disk_size         = var.storage_gb
    disk_type         = "PD_SSD"

    ip_configuration {
      ipv4_enabled    = false # private IP only
      private_network = var.network_id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled = var.high_availability
    }
  }

  depends_on = [google_service_networking_connection.private]
}

resource "google_sql_database" "this" {
  name     = var.db_name
  instance = google_sql_database_instance.this.name
}

resource "google_sql_user" "this" {
  name     = var.db_user
  instance = google_sql_database_instance.this.name
  password = random_password.db.result
}

# ---- Contract outputs ----
output "host" { value = google_sql_database_instance.this.private_ip_address }
output "port" { value = 5432 }
output "db_name" { value = var.db_name }
output "user" { value = var.db_user }
output "password" {
  value     = random_password.db.result
  sensitive = true
}
output "connection_url" {
  value     = "postgresql://${var.db_user}:${random_password.db.result}@${google_sql_database_instance.this.private_ip_address}:5432/${var.db_name}?sslmode=require"
  sensitive = true
}
