---
description: "Workspace instructions for the RPA Boletín Minero-Energético project. Use when: working on the automated newsletter system for mining-energy news from Latin America."
---

# RPA Boletín Minero-Energético Workspace Instructions

This workspace contains an RPA system that automates the collection, scoring, translation, and distribution of mining-energy news from Chile, Peru, and Argentina. The system runs twice weekly via APScheduler and uses Claude AI for semantic scoring and translation.

## Quick Start for New Contributors

1. **Environment Setup**: Follow the installation steps in [README.md](boletin/README.md#instalación). Create venv at project root, install dependencies, and configure `.env` with API keys and database credentials.

2. **Database**: PostgreSQL schema auto-creates on first run. Ensure DB is accessible before starting.

3. **Run Modes**:
   - `python main.py --preview`: Generate HTML preview without sending emails (safe for testing).
   - `python main.py --run-now`: Execute full pipeline once (respects process lock).
   - `python main.py`: Production scheduler (Tue/Thu at configured hour).

4. **Validation**: Check logs in `logs/boletin_YYYY-MM-DD.log`. Use `tail -f` for real-time monitoring.

## Development Workflow

- **No Build Step**: Pure Python project. Changes take effect immediately after restart.
- **No Automated Tests**: Validate via `--preview` mode and log inspection.
- **Configuration Changes**: Update PostgreSQL tables (e.g., `fuentes`, `score_reglas`) instead of modifying code.
- **Error Handling**: All operations include retries. Monitor for grouped error emails.
- **Process Lock**: Prevents concurrent executions. Manual removal of `.lock` file if needed after crashes.

## Architecture Overview

- **Modular Pipeline**: 8-step process orchestrated in `main.py` (scraping → scoring → translation → email).
- **Async Scraping**: Parallel HTTP requests using `httpx` and `asyncio`.
- **AI Batching**: Claude API calls in batches to optimize costs.
- **Database-Driven**: All sources, rules, and quotas stored in PostgreSQL.
- **Bilingual Output**: HTML template with side-by-side ES/EN columns.

See [SKILL.md](boletin/SKILL.md) for detailed module responsibilities and [MEMORY.md](boletin/MEMORY.md) for quick context.

## Coding Conventions

- **File Structure**: Code in `boletin/`, env/venv/logs at root.
- **Environment Variables**: All config via `.env` (no defaults for secrets).
- **Logging**: Daily files with UTC timestamps, structured format.
- **Database**: Transactions for all operations, SHA-256 for deduplication.
- **Error Types**: 4 categories with exponential backoff retries.

## Common Pitfalls

- **Lock Conflicts**: Remove `.lock` manually if process crashes.
- **DB Failures**: Pipeline exits if PostgreSQL unavailable at startup.
- **API Limits**: Monitor Claude usage; batches prevent overuse.
- **Selector Breaks**: Update DB `fuentes` table if site HTML changes.
- **Timezone Issues**: Defaults to America/Santiago; uses `zoneinfo`.

## Key Files

- `main.py`: Orchestrator and scheduler.
- `db.py`: PostgreSQL operations and schema.
- `scraper.py`: RSS and async scraping.
- `scorer.py`: Local + AI scoring.
- `translator.py`: English translation.
- `emailer.py`: HTML generation and SMTP.
- `retrier.py`: Centralized error handling.
- `templates/boletin.html`: Jinja2 template.

For business rules, see `Reglas_de_Negocio.docx`.</content>
<parameter name="filePath">d:\workspace_visual_studio_code\rpa_boletin\.github\copilot-instructions.md