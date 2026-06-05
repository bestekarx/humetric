output "api_url" {
  description = "API service URL"
  value       = aws_lb.api.dns_name
}

output "db_endpoint" {
  description = "RDS endpoint"
  value       = aws_db_instance.main.endpoint
}
