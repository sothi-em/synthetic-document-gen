# document-gen — web UI

Frontend for the `document-gen` server: Vite + React + TypeScript,
Tailwind CSS v4, shadcn/ui, managed with [pnpm](https://pnpm.io).

For project overview, backend setup, and configuration, see the
[README in the repository root](../README.md).

## Commands

```powershell
pnpm install     # install dependencies
pnpm dev         # dev server on http://localhost:5173 (proxies /api to :8000)
pnpm build       # type-check (tsc -b) + production build to web/dist
pnpm lint        # oxlint
```

`pnpm dev` expects the API server running on port 8000:

```powershell
uv run document-gen serve   # from the repo root
```

`pnpm build` outputs to `web/dist/`, which the FastAPI app serves as static
files at the site root (the API lives under `/api`).
