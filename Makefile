folder:=django_permanent


# Print this help message
help:
	@echo
	@awk '/^#/ {c=substr($$0,3); next} c && /^([a-zA-Z].+):/{ print "  \033[32m" $$1 "\033[0m",c }{c=0}' $(MAKEFILE_LIST) |\
        sort |\
        column -s: -t |\
        less -R

# Ruffing python files
ruff-check:
	@echo "--- 🐕 Ruffing 🐕 ---"
	uv run ruff check ${folder}

# Format python files
ruff-format:
	@echo "--- 🎩 Blacking 🎩 ---"
	uv run ruff format ${folder} --check

# Typecheck python files
mypy:
	@echo "--- ⚡ Mypying ⚡ ---"
	uv run mypy ${folder}

# Run all linters
lint: ruff-check ruff-format mypy


# Run all tests
test:
	@echo "--- 💃 Testing 💃 ---"
	uv run pytest --cov

# Test and lint in CI
ci: lint test



