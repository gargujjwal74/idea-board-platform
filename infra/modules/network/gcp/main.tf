terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.14" }
  }
}

variable "name" { type = string }
variable "region" { type = string }
variable "cidr" {
  type    = string
  default = "10.10.0.0/16"
}

resource "google_compute_network" "this" {
  name                    = var.name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "private" {
  name                     = "${var.name}-private"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = cidrsubnet(var.cidr, 4, 0)
  private_ip_google_access = true
}

# Private nodes need Cloud NAT to pull images / reach the internet
resource "google_compute_router" "this" {
  name    = var.name
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "this" {
  name                               = var.name
  router                             = google_compute_router.this.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# ---- Contract outputs (identical names/types across clouds) ----
output "network_id" { value = google_compute_network.this.id }
output "subnet_ids" { value = [google_compute_subnetwork.private.id] }
output "cidr" { value = var.cidr }
