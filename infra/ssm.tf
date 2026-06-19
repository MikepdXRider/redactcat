resource "aws_ssm_parameter" "jwt_secret" {
  name  = "/${var.app_name}/JWT_SECRET"
  type  = "SecureString"
  value = "placeholder"
  lifecycle { ignore_changes = [value] }
}
