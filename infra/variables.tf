variable "project" {
  description = "Cost rollup identifier across all resources"
  type        = string
  default     = "beckn-slot-booking"
}

variable "component" {
  description = "Which application this resource belongs to"
  type        = string
  validation {
    condition     = contains(["registry", "beckn-gateway", "bap", "bpp"], var.component)
    error_message = "component must be one of: registry, beckn-gateway, bap, bpp"
  }
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "preprod", "production"], var.environment)
    error_message = "environment must be one of: dev, staging, preprod, production"
  }
}

variable "owner" {
  description = "Team or contact responsible for this resource"
  type        = string
}

variable "lifecycle_stage" {
  description = "Matches the lifecycle tags used throughout livetracker1.md"
  type        = string
  validation {
    condition     = contains(["mvp", "pilot", "beta", "ent"], var.lifecycle_stage)
    error_message = "lifecycle_stage must be one of: mvp, pilot, beta, ent"
  }
}

locals {
  common_tags = {
    project         = var.project
    component       = var.component
    environment     = var.environment
    owner           = var.owner
    lifecycle_stage = var.lifecycle_stage
  }
}
