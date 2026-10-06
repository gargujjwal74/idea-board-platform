# Modules share one input/output contract across clouds, so some implementations
# intentionally accept variables they do not need (e.g. region, subnet_ids on GCP SQL).
rule "terraform_unused_declarations" {
  enabled = false
}
