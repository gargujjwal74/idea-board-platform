# Same variable surface on every cloud (plus cloud-specific identity below).
variable "name" {
  type        = string
  description = "Environment name; prefixes all resources, e.g. idea-board-demo"
  default     = "idea-board"
}

variable "region" {
  type        = string
  description = "Cloud region"
}

variable "node_count" {
  type    = number
  default = 2
}

variable "node_size" {
  type        = string
  default     = "small"
  description = "Abstract size: small | medium | large"
}

variable "db_size" {
  type        = string
  default     = "small"
  description = "Abstract size: small | medium | large"
}

variable "high_availability" {
  type    = bool
  default = false
}

variable "kubernetes_version" {
  type    = string
  default = null
}

variable "api_allowed_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to reach the public Kubernetes API (IAM-authenticated). Default is open so GitHub-hosted runners can deploy; restrict if you use self-hosted runners / static egress."
}
