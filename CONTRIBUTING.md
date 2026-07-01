# Contributing to Agent Trace

Thank you for your interest in contributing! This guide covers the basics.

## Development Setup

```bash
git clone https://github.com/Apageoflove/agent-trace.git
cd agent-trace
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web]"
pytest tests/ -v
```

## Project Structure

```
src/agent_trace/
  otel/           OTel GenAI span emitter (M1)
  storage/        SQLite backend + abstract interface (M2)
  detectors/      Cycle / Deadlock / Bloat / Anomaly (M3-M5, M7)
  web/            FastAPI + frontend (M6)
  tests/            148 tests, 100% gate per module
examples/         Demo scripts
benchmarks/       Accuracy benchmark scripts
```

## Accuracy Gate (100%)

Every module MUST pass 100% accuracy before merge:

- M1 OTel: span field coverage 100%
- M2 Storage: CRUD 100% + <10ms query
- M3 Cycle: F1 100% (50-graph benchmark)
- M4 Deadlock: precision/recall 100% (20-scenario benchmark)
- M5 Bloat: alert trigger 100% + MAE ≤15%
- M6 Web: 4-endpoint latency <500ms
- M7 Anomaly: F1 100% (100-scenario benchmark)

Run the full gate:

```bash
pytest tests/ -v
```

## Pull Request Checklist

- [ ] All tests pass (`pytest tests/ -v`)
- [ ] No new warnings
- [ ] New features include edge-case tests
- [ ] Code follows existing style (type hints, dataclasses, frozen=True)
- [ ] No `as any`, `@ts-ignore`, or type suppressions
- [ ] Comments in Chinese with English technical terms (per project convention)

## Adding a New Detector

1. Create `src/agent_trace/detectors/your_detector.py`
2. Implement a frozen dataclass for the alert event
3. Implement the detector class with `on_alert` callback support
4. Add to `src/agent_trace/detectors/__init__.py`
5. Write `tests/test_your_detector.py` with benchmark (F1 100% gate)
6. Update PLAN.md if adding a new module

## Release Process

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md`
3. Tag `git tag v0.X.0`
4. Build: `python -m build`
5. Publish to PyPI (maintainers only)

## Code of Conduct

Be respectful. Be constructive. Focus on the technology.

## License

Apache 2.0 — see [LICENSE](LICENSE).
