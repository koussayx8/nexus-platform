# DigitalOcean DOKS — NEXUS Production Cluster
#
# This Terraform configuration provisions:
# - A managed Kubernetes cluster (DOKS)
# - A node pool optimized for the NEXUS platform
# - VPC networking
#
# Prerequisites:
# - DigitalOcean account with $200 credit activated ($5 PayPal)
# - DIGITALOCEAN_TOKEN environment variable set
# - terraform ~> 1.5
#
# Usage:
#   terraform init
#   terraform plan -out=plan.tfplan
#   terraform apply plan.tfplan    # DO NOT apply until credit is active
#
# Remote state: Terraform Cloud (nexus_pfe/nexus-doks).
# DIGITALOCEAN_TOKEN set as sensitive env var in TFC workspace.
# See ADR-004 for dev→prod promotion path.

terraform {
  required_version = "~> 1.5"

  cloud {
    organization = "nexus_pfe"

    workspaces {
      name = "nexus-doks"
    }
  }

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.40"
    }
  }
}

provider "digitalocean" {
  # Token from DIGITALOCEAN_TOKEN env var
}
