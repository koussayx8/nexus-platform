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
# - terraform >= 1.5.0
#
# Usage:
#   terraform init
#   terraform plan -out=plan.tfplan
#   terraform apply plan.tfplan    # DO NOT apply until credit is active
#
# See ADR-004 for dev→prod promotion path.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.40"
    }
  }

  # Remote state — uncomment when ready
  # backend "s3" {
  #   endpoints = {
  #     s3 = "https://fra1.digitaloceanspaces.com"
  #   }
  #   bucket                      = "nexus-terraform-state"
  #   key                         = "doks/terraform.tfstate"
  #   region                      = "us-east-1"  # Required by backend, not used by DO
  #   skip_credentials_validation = true
  #   skip_requesting_account_id  = true
  #   skip_metadata_api_check     = true
  #   skip_s3_checksum            = true
  # }
}

provider "digitalocean" {
  # Token from DIGITALOCEAN_TOKEN env var
}
