SHELL := /bin/bash
UV ?= uv
export UV_PROJECT_ENVIRONMENT := $(CURDIR)/app/.venv

.PHONY: bootstrap check lint type test coverage regressions engine package clean

bootstrap:
	$(UV) sync --frozen
	cd pi-source && npm ci --ignore-scripts

check:
	bash scripts/quality.sh

lint:
	$(UV) run ruff check app scripts tests
	$(UV) run shellcheck engine/build.sh packaging/build.sh packaging/setup-signing.sh tests/run_all_regressions.sh scripts/*.sh

type:
	$(UV) run pyright

test:
	QT_QPA_PLATFORM=offscreen $(UV) run pytest

coverage:
	QT_QPA_PLATFORM=offscreen $(UV) run pytest --cov --cov-report=term-missing --cov-report=xml

regressions:
	bash tests/run_all_regressions.sh

engine:
	bash engine/build.sh

package:
	bash packaging/build.sh

clean:
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov packaging/build packaging/dist engine/haochen-engine
