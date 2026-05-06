variable "do_region" {
  description = "DigitalOcean region for the DOKS cluster"
  type        = string
  default     = "fra1" # Frankfurt — closest to Tunisia, good EU latency
}

variable "cluster_name" {
  description = "Name of the DOKS cluster"
  type        = string
  default     = "nexus-prod"
}

variable "k8s_version" {
  description = "Kubernetes version for DOKS (must match available versions)"
  type        = string
  default     = "1.32" # DOKS latest stable; prefix match used by DO
}

variable "node_size" {
  description = "Droplet size for worker nodes"
  type        = string
  default     = "s-2vcpu-4gb" # $24/month — fits within $200 credit for 8+ months
}

variable "node_count" {
  description = "Number of worker nodes"
  type        = number
  default     = 2

  validation {
    condition     = var.node_count >= 1 && var.node_count <= 5
    error_message = "Node count must be between 1 and 5 (budget constraint: $200 credit)."
  }
}

variable "min_nodes" {
  description = "Minimum nodes for autoscaler"
  type        = number
  default     = 1
}

variable "max_nodes" {
  description = "Maximum nodes for autoscaler"
  type        = number
  default     = 3
}

variable "tags" {
  description = "Tags applied to all resources"
  type        = list(string)
  default     = ["nexus", "pfe", "production"]
}
