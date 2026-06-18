# modules/

Each file in this directory is a feature module. To add a new feature:

1. Create a file (e.g. `redactions.py`)
2. Define a router: `router = APIRouter(tags=["redactions"])`
3. Add your endpoints to the router
4. Register it in `app/main.py`:
   ```python
   from app.modules.redactions import router as redactions_router
   app.include_router(redactions_router, prefix="/redactions")
   ```

Keep modules focused on a single resource or domain concept. Shared utilities belong in `app/` (e.g. `app/dependencies.py`), not inside a module file.
