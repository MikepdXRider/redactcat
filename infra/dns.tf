resource "aws_route53_zone" "main" {
  name = "redactcat.com"
}

resource "aws_route53_record" "api" {
  zone_id = aws_route53_zone.main.zone_id
  name    = "api.redactcat.com"
  type    = "CNAME"
  ttl     = 300
  records = [aws_apprunner_custom_domain_association.api.dns_target]
}

resource "aws_route53_record" "api_cert_validation" {
  for_each = {
    for record in aws_apprunner_custom_domain_association.api.certificate_validation_records :
    record.name => record
  }

  zone_id = aws_route53_zone.main.zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 300
  records = [each.value.value]
}
