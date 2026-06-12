---
description: "Workspace instructions for the RPA Boletín Minero-Energético project. Use when working on the automated newsletter system for mining-energy news from Latin America."
---

# RPA Boletín Minero-Energético Workspace Instructions

This workspace contains an RPA system that automates the collection, scoring, translation, and distribution of mining-energy news from Chile, Peru, and Argentina. The system runs twice weekly and uses Claude AI for semantic scoring and translation.

## Quick Start for New Contributors

1. **Environment Setup**: Create a virtualenv at project root, install the package in editable mode if desired, and configure `.env` with API keys and database credentials.
2. **Database**: PostgreSQL schema auto-creates on first run. Ensure DB is accessible before starting.
3. **Run Modes**:
   - `python -m boletin.main --preview`: Generate HTML preview without sending emails.
   - `python -m boletin.main --run-now`: Execute full pipeline once.
   - `python -m boletin.main`: Production scheduler.
4. **Validation**: Check logs in `logs/boletin_YYYY-MM-DD.log`.

## Current Project Structure

- Code lives in `src/boletin/`
- Main documentation lives in `docs/`
- Manual selector/login tests live in `tests/`
- Debug/export scripts live in `scripts/`
- Canonical business rules document: `docs/negocio/Reglas_de_Negocio.docx`

## Architecture Overview

- **Entry point**: `src/boletin/main.py`
- **Pipeline orchestration**: `src/boletin/services/pipeline_service.py`
- **Database-driven configuration**: sources, scoring and quotas stored in PostgreSQL
- **Bilingual output**: HTML email with ES/EN sections
- **Operational docs**: see `docs/operacion/README_sistema.md`
- **Data docs**: see `docs/datos/modelo_datos.md` and `docs/datos/modelo_relacional.md`

## Development Workflow

- No build step.
- Prefer validating through preview mode, logs and targeted scripts/tests.
- Configuration changes should go to PostgreSQL tables instead of hardcoding values.
- Avoid relying on old paths like `boletin/README.md`, `boletin/SKILL.md` or `boletin/MEMORY.md`; those belong to a previous structure.

## Key Files

- `src/boletin/main.py`: Orchestrator and scheduler.
- `src/boletin/db.py`: Backward-compatible DB facade.
- `src/boletin/scraper.py`: RSS and scraping.
- `src/boletin/scorer.py`: Local + AI scoring.
- `src/boletin/translator.py`: English translation.
- `src/boletin/emailer.py`: HTML generation and SMTP.
- `src/boletin/retrier.py`: Centralized error handling.
- `src/boletin/templates/boletin.html`: Jinja2 template.

For business rules, see `docs/negocio/Reglas_de_Negocio.docx`.
