resource "aws_ssm_parameter" "jwt_secret" {
  name  = "/${var.app_name}/JWT_SECRET"
  type  = "SecureString"
  value = "placeholder"
  lifecycle { ignore_changes = [value] }
}

resource "aws_ssm_parameter" "database_url" {
  name  = "/${var.app_name}/DATABASE_URL"
  type  = "SecureString"
  value = "placeholder"
  lifecycle { ignore_changes = [value] }
}
