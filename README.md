# ReconcileAI

Automated Expense & Invoice Auditor.

## Overview
ReconcileAI is a system to ingest invoice images, extract structured data, normalize it, and reconcile it against Purchase Orders deterministically.

## Architecture
- **Frontend**: Next.js, React, Tailwind CSS, Motion
- **Backend**: FastAPI, Python 3.11+, PostgreSQL
- **Database**: PostgreSQL (via Docker)
- **AI**: OpenAI Vision models

## Setup

1. **Database**
   ```bash
   docker-compose up -d
   ```

2. **Backend**
   ```bash
   cd backend
   python -m venv .venv
   source .venv/Scripts/activate # Windows
   pip install -r requirements.txt
   cp ../.env.example .env
   uvicorn app.main:app --reload
   ```

3. **Frontend**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
