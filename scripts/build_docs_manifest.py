from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


root = Path(__file__).resolve().parents[1]
docs_root = root / "docs" / "对接资料"
entries = []
for path in sorted(docs_root.rglob("*")):
    if not path.is_file() or path.name == "manifest.json":
        continue
    content = path.read_bytes()
    entries.append(
        {
            "path": path.relative_to(docs_root).as_posix(),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    )

manifest = {
    "generated_at": datetime.now(UTC).isoformat(),
    "root": "docs/对接资料",
    "files": entries,
}
(docs_root / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"manifest files={len(entries)}")

