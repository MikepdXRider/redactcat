variable "region" {
  type        = string
  default     = "us-west-2"
  description = "AWS region for all resources"
}

variable "app_name" {
  type        = string
  default     = "redactcat"
  description = "Application name — used as a prefix for all resource names"
}
