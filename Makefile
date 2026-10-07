.PHONY: help up down test-ai tf-validate helm-lint kind-test demo-ai

help:          ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

up:            ## run the whole stack locally -> http://localhost:3000
	docker compose up --build

down:          ## stop the stack and wipe local data
	docker compose down -v

ai/.venv:
	python3 -m venv ai/.venv
	ai/.venv/bin/pip install -q -r ai/requirements-dev.txt

test-ai: ai/.venv  ## unit tests for the AI tools and their guardrails (no API key needed)
	cd ai && .venv/bin/python -m pytest -q

tf-validate:   ## fmt + validate every Terraform root (no cloud credentials needed)
	terraform fmt -check -recursive infra
	@for d in infra/envs/aws infra/envs/gcp infra/bootstrap/aws infra/bootstrap/gcp; do \
	  (cd $$d && terraform init -backend=false -input=false >/dev/null && terraform validate) || exit 1; done

helm-lint:     ## lint the chart for every target
	@for c in local aws gcp; do helm lint charts/idea-board -f charts/idea-board/values/$$c.yaml --set database.url=postgresql://u:p@h:5432/d; done

kind-test:     ## install the chart into a throwaway local Kubernetes cluster and call the API
	docker build -q -t local/idea-board-backend:dev app/backend
	docker build -q -t local/idea-board-frontend:dev app/frontend
	kind create cluster --name ib-test --wait 120s
	kind load docker-image local/idea-board-backend:dev local/idea-board-frontend:dev --name ib-test
	helm upgrade --install ib charts/idea-board -f charts/idea-board/values/local.yaml --wait --timeout 4m
	kubectl port-forward svc/ib-frontend 18080:80 & sleep 3; curl -s localhost:18080/api/ideas; echo; kill %1; \
	kind delete cluster --name ib-test

demo-ai: ai/.venv  ## offline demo of the AI tools with canned model responses (no API key)
	@echo "\n--- environment sizing"; cd ai && AI_MOCK_RESPONSE=fixtures/mock_env_profile.json .venv/bin/python env_profile.py --goal "cost-sensitive staging" --out /tmp/ib-gen
	@echo "\n--- health judge on a crash-looping release (exit 1 => rollback)"; cd ai && AI_MOCK_RESPONSE=fixtures/mock_health_unhealthy.json .venv/bin/python health_judge.py --release ib --cloud aws --snapshot fixtures/crashloop.json --out /tmp/ib-health; echo "exit code: $$?"
