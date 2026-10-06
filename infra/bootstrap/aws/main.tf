# One-time bootstrap (run locally with admin creds, state stays local):
#   - S3 bucket for Terraform state (versioned, encrypted, locking via S3 native lockfile)
#   - GitHub OIDC provider + IAM role the pipeline assumes (no stored AWS keys)
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}
variable "github_repo" {
  type        = string
  description = "owner/repo allowed to assume the role, e.g. acme/idea-board-platform"
}
variable "github_extra_sub_patterns" {
  type        = list(string)
  default     = []
  description = <<-EOT
    Extra OIDC `sub` patterns allowed to assume the role. GitHub's newer "immutable subject" claims look like
    repo:OWNER@OWNER_ID/REPO@REPO_ID:...  (get the prefix with:
    gh api repos/OWNER/REPO/actions/oidc/customization/sub --jq .sub_claim_prefix) and then append ":*".
  EOT
}
variable "state_bucket_name" {
  type        = string
  description = "Globally unique bucket name for Terraform state"
}

provider "aws" { region = var.region }

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only THIS repository can assume the role
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = concat(["repo:${var.github_repo}:*"], var.github_extra_sub_patterns)
    }
  }
}

resource "aws_iam_role" "pipeline" {
  name               = "idea-board-github-pipeline"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}

# Demo-grade: the pipeline provisions VPC/EKS/RDS/IAM so it needs broad rights.
# For production, replace with a scoped policy and add a permissions boundary.
resource "aws_iam_role_policy_attachment" "admin" {
  role       = aws_iam_role.pipeline.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

output "role_arn" { value = aws_iam_role.pipeline.arn }
output "state_bucket" { value = aws_s3_bucket.state.bucket }
output "region" { value = var.region }
