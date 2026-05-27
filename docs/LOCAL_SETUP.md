# Local Setup

## Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 15 (local or docker)

## Quickstart (Makefile)

1. Copy environment file
- cp .env.example .env

2. Install dependencies
- make setup

3. Start backend
- make backend

4. Start frontend
- make frontend

5. Open app/docs
- Frontend: http://localhost:3000
- API docs: http://localhost:8000/docs

## Test and Build

- make test
- make lint

## Packaging and Cleanup

- make package
- make clean

## Docker Option

- cp .env.example .env
- docker compose up --build
