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

# Cut a GitHub Release with the built wheel attached. This is the install path
# the one-line installer (install.sh) reads from — no PyPI account required.
#
# Usage: make release-github v=0.1.0
release-github:
	@if [ -z "$(v)" ]; then echo "Usage: make release-github v=0.1.0"; exit 1; fi
	@command -v gh >/dev/null || { echo "gh CLI not found — see https://cli.github.com"; exit 1; }
	@echo "Cutting v$(v)..."
	make build
	@test -f ts_admin/static/index.html || { echo "No UI in ts_admin/static/ — build failed"; exit 1; }
	sed -i '' 's/^version = .*/version = "$(v)"/' pyproject.toml
	rm -rf dist/
	pip install --quiet build
	python -m build --wheel
	@python -c "import glob, sys, zipfile; w=glob.glob('dist/*.whl')[0]; n=zipfile.ZipFile(w).namelist(); sys.exit('ERROR: %s contains no UI' % w) if 'ts_admin/static/index.html' not in n else print('%s: %d static files bundled' % (w, sum('ts_admin/static/' in x for x in n)))"
	git add pyproject.toml
	git commit -m "chore: release v$(v)"
	git tag v$(v)
	git push origin HEAD --tags
	gh release create v$(v) dist/*.whl --title "v$(v)" --generate-notes
	@echo ""
	@echo "Released. Users can now install with:"
	@echo "  curl -LsSf https://raw.githubusercontent.com/kunalghate/thoughtspot-admin-toolkit/main/install.sh | sh"

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
