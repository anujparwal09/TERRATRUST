# TerraTrust-AR

This workspace contains the TerraTrust backend implementation under `backend/`.
The v3.1 project documents in the repository root are the source of truth for
backend architecture, API contracts, schema, and deployment assumptions.

Start with `backend/SETUP.md` for environment setup, schema creation, contract
deployment, and test commands. The main runtime entrypoint is `backend/main.py`.

Key implementation areas:

- FastAPI routers in `backend/routers/`
- Shared auth/db layer in `backend/app/`
- Science and blockchain services in `backend/services/`
- Celery background tasks in `backend/tasks/`
- Solidity contract and Hardhat tooling in `backend/contracts/`

