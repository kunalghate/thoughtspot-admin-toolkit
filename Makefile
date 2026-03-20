.PHONY: dev build test lint release install

# ── Developer commands ─────────────────────────────────────────────────────────

# Start FastAPI + Next.js dev servers with hot reload
dev:
	@echo "Starting FastAPI on :8000 and Next.js on :3000..."
	@trap 'kill 0' SIGINT; \
	uvicorn ts_admin.main:app --reload --port 8000 & \
	cd frontend && npm run dev & \
	wait

# Install Python package in editable mode + dev deps
install:
	pip install -e ".[dev]"
	cd frontend && npm install

# ── Testing ────────────────────────────────────────────────────────────────────

test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v

test-integration:
	pytest tests/integration/ -v

test-e2e:
	cd frontend && npx playwright test

# ── Code quality ───────────────────────────────────────────────────────────────

lint:
	ruff check ts_admin/ tests/
	ruff format --check ts_admin/ tests/

format:
	ruff format ts_admin/ tests/
	ruff check --fix ts_admin/ tests/

typecheck:
	mypy ts_admin/

# ── Build (Next.js → static → bundle into package) ────────────────────────────

# Build Next.js static export and copy into ts_admin/static/
build:
	@echo "Building Next.js..."
	cd frontend && npm run build
	@echo "Copying static files..."
	rm -rf ts_admin/static/*
	cp -r frontend/out/. ts_admin/static/
	@echo "Build complete — ts_admin/static/ updated."

# ── Release ────────────────────────────────────────────────────────────────────

# Usage: make release v=1.0.0
release:
	@if [ -z "$(v)" ]; then echo "Usage: make release v=1.0.0"; exit 1; fi
	@echo "Releasing v$(v)..."
	make test
	make build
	# Bump version in pyproject.toml
	sed -i '' 's/^version = .*/version = "$(v)"/' pyproject.toml
	git add ts_admin/static/ pyproject.toml
	git commit -m "chore: release v$(v)"
	git tag v$(v)
	pip install build
	python -m build
	@echo "Ready to publish. Run: twine upload dist/*"
