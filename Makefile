.PHONY: dev down logs test build edge-network prod-up prod-down prod-logs backup train

EDGE_NETWORK := openjack-edge

dev:
	docker compose --env-file .env -f compose.yaml up --build

down:
	docker compose --env-file .env -f compose.yaml down

logs:
	docker compose --env-file .env -f compose.yaml logs -f --tail=200

test:
	docker compose --env-file .env -f compose.yaml --profile tests run --rm backend-test
	cd frontend && npm run lint && npm run build

build:
	docker compose --env-file .env -f compose.prod.yaml build

edge-network:
	@docker network inspect $(EDGE_NETWORK) >/dev/null 2>&1 || docker network create $(EDGE_NETWORK)

prod-up: edge-network
	docker compose --env-file .env -f compose.prod.yaml up -d --build --remove-orphans

prod-down:
	docker compose --env-file .env -f compose.prod.yaml down

prod-logs:
	docker compose --env-file .env -f compose.prod.yaml logs -f --tail=200

backup:
	docker compose --env-file .env -f compose.prod.yaml exec scheduler python -m app.jobs.backup

train:
	docker compose --env-file .env -f compose.prod.yaml exec scheduler python -m app.ml.train
