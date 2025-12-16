# ForStudents – Past Paper Library

Campus-only past paper sharing with uploads, admin approvals, gated downloads (free vs paid), feedback, and optional S3 storage.

## Quickstart
1) Backend
- `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Create `.env` (see template) and run: `python app.py`
- Start command in production: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`

2) Frontend
- `cd frontend && npm install`
- `npm run dev` (set `VITE_API_BASE` to your backend URL)
- Production build: `npm run build` (output `dist/`)

3) Features / flow
- Register/login (JWT). Only emails matching `ADMIN_EMAIL` become admin.
- Upload PDF past papers/solutions. Admin approves/rejects before publishing.
- Free users can unlock a limited number of documents; paid users get full access.
- Feedback form writes to DB and emails admin/submitter (requires SMTP envs).
- Optional S3 offload for uploads; otherwise stored locally (ephemeral on PaaS).
- Optional AI study helper: generate practice questions for a document by calling the Notes service.

## Key config (env)
- Core: `SECRET_KEY`, `JWT_SECRET_KEY`, `FREE_DOC_LIMIT`, `FRONTEND_URL`
- Payments: `SIMULATE_PAYMENTS=true` (or Stripe keys to charge)
- SMTP (for emails): `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_FROM`, `ADMIN_EMAIL`
- S3 (optional): `S3_BUCKET`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- DB: `DATABASE_URL` (sqlite by default; use Postgres in prod)
- Notes API (optional): `NOTES_API_BASE` (URL of the Notes app), `NOTES_API_KEY` (shared secret header `X-Api-Key`)

## Content protection considerations
- Access control: downloads require auth; free users get limited unlocked docs.
- Rate/abuse: add IP/user download rate limits and alerting for unusual spikes.
- Watermarking: render PDFs with a light, signed watermark per user/session.
- Link hardening: short-lived, signed URLs for S3 downloads.
- Audit: log downloader user_id/doc_id/ip and surface to admin.
- Takedown: keep a path for removal requests and rejections.

## Frontend stack
- Vite + React single page (`frontend/src/App.jsx`), configurable `VITE_API_BASE`.
- Dark UI with plan cards, upload form, locked cards, admin approval queue, feedback form.
- Includes buttons on each document card to view/generate practice questions (paid users or admins).

## Backend stack
- Flask + SQLAlchemy + JWT auth in `backend/app.py`
- Documents, Users (role/plan), Feedback, DocumentAccess tracking
- Optional S3 storage, Stripe wiring (manual upgrade + webhook placeholders)
- New endpoints:
  - `POST /api/docs/<id>/questions` → sends the PDF to the Notes API, caches results
  - `GET /api/docs/<id>/questions` → returns cached questions (respects free/paid limits)

## Deploy tips
- Render backend: Root `backend`, Start `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- Vercel/Netlify frontend: Root `frontend`, Build `npm run build`, Output `dist`, env `VITE_API_BASE=<backend>`
- Use Postgres + S3 in production; sqlite + local storage are ephemeral.

## How the two services talk
1) Run the Notes app somewhere reachable and set `NOTES_API_BASE` to it (e.g., `https://notes-app.example.com`).  
2) Set the same secret in both apps: `NOTES_API_KEY=<shared-secret>`.  
3) In ForStudents, open a document card and click “Generate questions” (paid/admin). The backend streams the PDF to the Notes API, stores the returned Q&A JSON, and the frontend renders them under the card.  
4) “View questions” pulls the cached Q&A without re-hitting the AI API.  
