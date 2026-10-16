.PHONY: install lint test eval verify-specs run-playground run-workweek-mcp run-servicedesk-mcp deploy publish-gemini

export PATH := $(HOME)/.local/bin:$(PATH)

install:
	uv sync --extra eval --extra lint

lint:
	uv run ruff check app src tests scripts

verify-specs:
	uv run python scripts/verify_openapi_allowlist.py
	uv run python scripts/verify_openapi_contract.py

test:
	uv run pytest -q

eval:
	uv run python scripts/run_all_evals.py
	uv run python scripts/assert_eval_thresholds.py
	uv run python scripts/assert_latency_sla.py

run-playground:
	agents-cli playground

run-workweek-mcp:
	uv run uvicorn app.workweek_proxy:app --host 0.0.0.0 --port 8081

run-servicedesk-mcp:
	uv run uvicorn app.servicedesk_proxy:app --host 0.0.0.0 --port 8082

deploy:
	agents-cli deploy --project $${GOOGLE_CLOUD_PROJECT:-argolis-sandbox-main} --region $${VERTEX_AGENT_ENGINE_REGION:-us-central1}

publish-gemini:
	agents-cli publish gemini \
		--registration-type adk \
		--agent-runtime-id "projects/823956409216/locations/us-central1/reasoningEngines/6477434380338855936" \
		--gemini-app-id "projects/823956409216/locations/global/collections/default_collection/engines/ss-ge-app_1790047996783" \
		--display-name "SS-Enterprise HR AI Assistant (MVP 1)" \
		--description "Enterprise Multi-Agent HR & IT Service Delivery Assistant for Policy Q&A (NL, DE, CN, GLOBAL), WorkWeek HCM leave & profile operations, and ServiceImmediately ITSM tickets with HITL confirmation and Zero-Trust guardrails." \
		--tool-description "Answers employee HR policy questions with citations, checks and submits WorkWeek HCM leave requests and contact updates, and creates or updates ServiceImmediately IT/HR support tickets." \
		--project-id $${GOOGLE_CLOUD_PROJECT:-argolis-sandbox-main}
