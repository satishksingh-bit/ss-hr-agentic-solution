resource "google_kms_key_ring" "hr_keyring" {
  name     = "hr-agent-keyring-${var.env}"
  project  = var.project_id
  location = var.region
}

resource "google_kms_crypto_key" "cmek_data" {
  name            = "hr-agent-cmek-data"
  key_ring        = google_kms_key_ring.hr_keyring.id
  rotation_period = "7776000s" # 90 days (NFR-006, NFR-011)
  purpose         = "ENCRYPT_DECRYPT"
}

resource "google_kms_crypto_key" "uct_signing_key" {
  name     = "hr-agent-uct-es256"
  key_ring = google_kms_key_ring.hr_keyring.id
  purpose  = "ASYMMETRIC_SIGN"

  version_template {
    algorithm        = "EC_SIGN_P256_SHA256"
    protection_level = "SOFTWARE"
  }
}

resource "google_kms_crypto_key" "ct_signing_key" {
  name     = "hr-agent-ct-es256"
  key_ring = google_kms_key_ring.hr_keyring.id
  purpose  = "ASYMMETRIC_SIGN"

  version_template {
    algorithm        = "EC_SIGN_P256_SHA256"
    protection_level = "SOFTWARE"
  }
}

resource "google_kms_crypto_key" "psn_hmac_key" {
  name     = "hr-agent-psn-hmac-sha256"
  key_ring = google_kms_key_ring.hr_keyring.id
  purpose  = "MAC"

  version_template {
    algorithm        = "HMAC_SHA256"
    protection_level = "SOFTWARE"
  }
}

locals {
  secret_ids = toset([
    "hr-agent-uct-key-version",
    "hr-agent-ct-key-version",
    "hr-agent-psn-hmac-key-v1",
    "hr-agent-psn-hmac-key-v2",
    "hr-agent-mock-saas-iap-client-id",
  ])
}

resource "google_secret_manager_secret" "managed_secrets" {
  for_each  = local.secret_ids
  project   = var.project_id
  secret_id = "${each.value}-${var.env}"

  replication {
    user_managed {
      replicas {
        location = var.region
        customer_managed_encryption {
          kms_key_name = google_kms_crypto_key.cmek_data.id
        }
      }
    }
  }
}

resource "google_compute_security_policy" "cloud_armor_edge" {
  name    = "hr-agent-edge-armor-${var.env}"
  project = var.project_id

  # Rule 50: Strip/Reject untrusted client-supplied X-Corporate-Origin-Region header (SDD §4.2, 03-deployment §3.6, BH-05-2-01)
  rule {
    action   = "deny(403)"
    priority = 50
    match {
      expr {
        expression = "has(request.headers['x-corporate-origin-region']) && !inIpRange(origin.ip, '10.128.0.0/9')"
      }
    }
    description = "Reject untrusted client-supplied X-Corporate-Origin-Region header outside trusted egress CIDR"
  }

  # Rule 200: Explicit Mainland China (CN) regional allow before TW block (FR-027, FR-028, ADR-020, BH-05-2-01)
  rule {
    action   = "allow"
    priority = 200
    match {
      expr {
        expression = "origin.region_code == 'CN'"
      }
    }
    description = "Explicitly allow Mainland China (CN) origin traffic"
  }

  # Rule 900: Explicit Taiwan (TW) regional deny (FR-027, ADR-020)
  rule {
    action   = "deny(403)"
    priority = 900
    match {
      expr {
        expression = "origin.region_code == 'TW'"
      }
    }
    description = "Deny Taiwan (TW) origin traffic at Cloud Armor edge"
  }

  # Rule 1000: OWASP CRS 3.3 SQL Injection protection
  rule {
    action   = "deny(403)"
    priority = 1000
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable')"
      }
    }
    description = "OWASP CRS 3.3 SQL Injection WAF rule"
  }

  # Rule 1010: OWASP CRS 3.3 Cross-Site Scripting (XSS) protection
  rule {
    action   = "deny(403)"
    priority = 1010
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-v33-stable')"
      }
    }
    description = "OWASP CRS 3.3 XSS WAF rule"
  }

  # Rule 1020: OWASP CRS 3.3 Remote Code Execution (RCE) / LFI protection
  rule {
    action   = "deny(403)"
    priority = 1020
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('rce-v33-stable')"
      }
    }
    description = "OWASP CRS 3.3 RCE/LFI WAF rule"
  }

  # Rule 1100: Edge IP Rate Limiting (120 req/min per IP)
  rule {
    action   = "throttle"
    priority = 1100
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 120
        interval_sec = 60
      }
    }
    description = "Edge per-IP rate limiting (120 req/60s)"
  }

  rule {
    action   = "allow"
    priority = 2147483647
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default allow after OWASP CRS and TW regional block"
  }
}
