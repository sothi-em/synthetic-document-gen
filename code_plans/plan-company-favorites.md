# Plan: Company Favorites

Planning session — feature plan for starring companies and browsing a
favorites list in the Companies tab.

## Goal

- In the Companies tab table, let the user **star / unstar** companies while
  browsing and searching.
- Provide a **favorites list view** (show only starred companies).
- Persist the favorites in the backend **user settings** (TinyDB
  `user_settings` collection), so the list survives restarts and is shared
  across browser sessions.

## Design decisions

1. **Storage shape**: a new user-settings group, key `"favorites"`, holding a
   JSON value `{"company_ids": [int, ...]}`. Using the existing
   `document_query.get_setting` / `set_setting` primitives means no new
   collection and no migration.
2. **Read-time flag**: `list_companies()` (and `get_company()`) attach a
   `favorite: bool` to each company summary/detail so the frontend never
   needs a separate favorites round-trip.
3. **Write endpoint**: `POST /api/companies/{id}/favorite` with body
   `{"favorite": true|false}` — idempotent set, returns the new state.
   Toggle in the UI = read current state, POST the opposite.
4. **Favorites filter is client-side**: the panel already caches the full
   company list and filters search/industry locally, so the "Favorites only"
   view is a local filter against the `favorite` flag (no new API param).
5. **Stale-id cleanup**: `delete_company()` prunes the removed id from the
   favorites list; `favorite` lookups treat unknown ids as non-favorites,
   so the feature is robust to partial data.

## Backend changes

### `document_gen/document_query.py`

- Add constant `FAVORITES_SETTINGS_KEY = "favorites"`.
- Add helpers (all under the existing `_LOCK`):
  - `get_favorite_company_ids() -> set[int]` — read the setting; return an
    empty set when the setting is missing or malformed.
  - `set_favorite(company_id: int, favorite: bool) -> bool` — read-modify-
    write the `company_ids` list, return the new state.
- `list_companies()`: attach `"favorite": doc_id in favorite_ids` to each
  summary dict (fetch the id set once, outside the per-doc loop).
- `get_company()`: attach `"favorite"` to the returned dict.
- `delete_company()`: after removal, prune the id from the favorites list
  (no-op when absent).

### `document_gen/server.py`

- `GET /api/companies` — no signature change; responses now include the
  `favorite` field via `list_companies`.
- `GET /api/companies/{company_id}` — same, via `get_company`.
- New endpoint:
  ```
  POST /api/companies/{company_id}/favorite
  body: {"favorite": bool}
  returns: {"favorite": bool}
  ```
  404 when the company does not exist. Pydantic request model
  `FavoriteRequest(favorite: bool)` next to the other request bodies.

## Frontend changes

### `web/src/lib/api.ts`

- `CompanySummary`: add `favorite: boolean`.
- `CompanyDetail`: add `favorite: boolean`.
- `api.setFavorite: (id: number, favorite: boolean) =>
  request<{ favorite: boolean }>(`/api/companies/${id}/favorite`, {
    method: "POST", body: JSON.stringify({ favorite })
  })`.

### `web/src/components/companies-panel.tsx`

- **Star column**: new first table column with a `Star` (lucide) button per
  row — filled/amber when favorite, outline otherwise.
  - `onClick` calls `e.stopPropagation()` (row click opens the detail card).
  - Optimistic update: flip the `favorite` flag in the local `companies`
    state immediately; on API error, revert and surface the existing
    `error` banner.
- **Favorites filter**: a toggle in the toolbar (e.g. an outlined
  `Star`-icon button "Favorites" next to the industry select) that, when
  active, adds `company.favorite` to the local `filtered` memo. The count
  line (`N companies (of M)`) keeps working since it compares against the
  full cache.
- **Detail card**: a star toggle button beside the company name in the
  Details card header so the selection can also be changed from there
  (updates both the detail and the cached list).
- After a generation run adds new companies, `loadCompanies()` refreshes
  the cache as before; no favorites-specific handling needed.

## Tests

### `tests/test_document_query.py`

- `set_favorite` adds / removes an id; repeated set is idempotent.
- `get_favorite_company_ids` returns empty set with no setting and with a
  malformed setting value.
- `list_companies` summaries carry the correct `favorite` flags.
- `get_company` carries the `favorite` flag.
- `delete_company` prunes the id from the favorites list.
- (Follow existing pattern: temp `TINYDB_PATH` + `reset_db()`.)

### `tests/test_server.py`

- `POST /api/companies/{id}/favorite` toggles state and returns it;
  404 for unknown company id; 422 for a non-bool body.
- `GET /api/companies` summaries include `favorite`.

## Implementation steps

1. `document_query.py`: favorites helpers + `favorite` flags + delete
   pruning; add unit tests.
2. `server.py`: `POST /api/companies/{id}/favorite` + tests.
3. `web/src/lib/api.ts`: types + `setFavorite`.
4. `companies-panel.tsx`: star column, favorites filter, detail-card star.
5. `cd web && pnpm build && pnpm lint`; `uv run pytest`; `uv run black .`.

## Out of scope

- Sharing/syncing favorites across multiple users (single-user setting).
- Filtering documents or other tabs by favorite company.
- Sorting favorites to the top (the explicit "Favorites" view covers it).
