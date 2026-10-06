# One-time bootstrap (run locally with owner creds, state stays local):
#   - GCS bucket for Terraform state (versioned)
#   - Workload Identity Federation pool/provider trusting GitHub OIDC (no service-account keys)
#   - Service account the pipeline impersonates
terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.14" }
  }
}

variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "github_repo" {
  type        = string
  description = "owner/repo allowed to impersonate the service account"
}
variable "state_bucket_name" { type = string }

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "this" {}

resource "google_project_service" "bootstrap" {
  for_each = toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "state" {
  name                        = var.state_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning { enabled = true }
}

resource "google_service_account" "pipeline" {
  account_id   = "idea-board-pipeline"
  display_name = "GitHub Actions pipeline"
  depends_on   = [google_project_service.bootstrap]
}

# Demo-grade broad roles (pipeline creates network/GKE/SQL/IAM). Scope down for production.
resource "google_project_iam_member" "pipeline" {
  for_each = toset([
    "roles/editor",
    "roles/container.admin",
    "roles/iam.serviceAccountAdmin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/servicenetworking.networksAdmin",
    "roles/serviceusage.serviceUsageAdmin",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  depends_on                = [google_project_service.bootstrap]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  # Hard gate: tokens from any other repository are rejected at the provider
  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc { issuer_uri = "https://token.actions.githubusercontent.com" }
}

resource "google_service_account_iam_member" "wif" {
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

output "workload_identity_provider" { value = google_iam_workload_identity_pool_provider.github.name }
output "service_account" { value = google_service_account.pipeline.email }
output "state_bucket" { value = google_storage_bucket.state.name }
output "project_number" { value = data.google_project.this.number }
