.PHONY: test install lint clean

install:
	python3 -m pip install -e ".[dev]"

test:
	python3 -m pytest -q

clean:
	rm -rf build dist src/*.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
