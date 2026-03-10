.PHONY: install ui api wizard list clean lint fmt help

# ── Default ────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  Multi-Source Data Retrieval"
	@echo ""
	@echo "  make install    Install Python dependencies"
	@echo "  make wizard     Run the interactive CLI import wizard"
	@echo "  make ui         Start the Streamlit web UI"
	@echo "  make api        Start the FastAPI REST server (auto-reload)"
	@echo "  make list       List all registered datasets"
	@echo "  make clean      Delete generated databases and pipelines"
	@echo "  make lint       Run ruff linter"
	@echo "  make fmt        Auto-format with ruff"
	@echo ""

# ── Setup ──────────────────────────────────────────────────────────────────────
install:
	pip install -r requirements.txt

# ── Run ────────────────────────────────────────────────────────────────────────
wizard:
	python main.py

ui:
	streamlit run app.py

api:
	uvicorn api.server:build_app --factory \
		--host $${API_HOST:-0.0.0.0} \
		--port $${API_PORT:-8000} \
		--reload

list:
	python main.py list

refresh:
	@test -n "$(name)" || (echo "Usage: make refresh name=<dataset_name>" && exit 1)
	python main.py refresh $(name)

# ── Dev helpers ────────────────────────────────────────────────────────────────
lint:
	ruff check .

fmt:
	ruff format .

# ── Clean ──────────────────────────────────────────────────────────────────────
clean:
	rm -rf databases/ pipelines/
	@echo "Deleted databases/ and pipelines/"
