variable "humetric_version" {
  description = "Docker image tag for Humetric"
  type        = string
  default     = "latest"
}

variable "instance_count" {
  description = "Number of API service instances"
  type        = number
  default     = 2
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "humetric"
}

variable "db_username" {
  description = "Database username"
  type        = string
  default     = "humetric"
  sensitive   = true
}

variable "db_password" {
  description = "Database password"
  type        = string
  sensitive   = true
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}
