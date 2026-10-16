output "blue_engine_id" {
  value = terraform_data.vertex_agent_engine_blue.output.engine_name
}

output "green_engine_id" {
  value = terraform_data.vertex_agent_engine_green.output.engine_name
}
