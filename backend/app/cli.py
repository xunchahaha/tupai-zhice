from __future__ import annotations

from .config import PROJECT_ROOT, get_settings
from .db import SessionLocal, create_all
from .services.seed import bootstrap_admin, import_sample_workbook


def main() -> None:
    settings = get_settings()
    create_all()
    with SessionLocal() as db:
        bootstrap_admin(db, settings.bootstrap_admin_username, settings.bootstrap_admin_password)
        result = import_sample_workbook(db, PROJECT_ROOT / "data" / "imports" / "sample.xlsx")
        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
