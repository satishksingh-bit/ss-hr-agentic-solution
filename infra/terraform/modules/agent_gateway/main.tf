resource "terraform_data" "agent_gateway_policy" {
  input = {
    project_id          = var.project_id
    region              = var.region
    gateway_name        = "hr-agent-mcp-gateway-${var.env}"
    caller_sa           = var.agent_service_account_email
    workweek_mcp_url    = var.workweek_mcp_url
    servicedesk_mcp_url = var.servicedesk_mcp_url
    allowed_tools = [
      "retrieve_policy_chunks",
      "evaluate_eligibility",
      "verify_grounding_and_repair",
      "get_my_profile",
      "get_my_leave_balances",
      "validate_leave_request",
      "validate_contact_update",
      "submit_my_leave_request",
      "update_my_contact_info",
      "get_my_ticket",
      "list_my_open_tickets",
      "validate_incident",
      "validate_ticket_update",
      "create_incident",
      "add_ticket_comment",
      "update_ticket_status",
      "get_write_status",
    ]
  }
}
