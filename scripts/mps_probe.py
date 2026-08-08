"""Stage 0 environment probe; this is not a model feasibility test."""

from __future__ import annotations

import json

import torch


def supports(dtype: torch.dtype) -> dict[str, object]:
    try:
        value = torch.ones((8, 8), device="mps", dtype=dtype)
        result = (value @ value).float().mean().item()
        return {"available": True, "result": result}
    except Exception as error:  # Probe reports unsupported combinations rather than hiding them.
        return {"available": False, "error": str(error)}


def main() -> None:
    print(json.dumps({
        "mps_built": torch.backends.mps.is_built(),
        "mps_available": torch.backends.mps.is_available(),
        "fp16": supports(torch.float16) if torch.backends.mps.is_available() else {"available": False},
        "bf16": supports(torch.bfloat16) if torch.backends.mps.is_available() else {"available": False},
    }, sort_keys=True))


if __name__ == "__main__":
    main()
