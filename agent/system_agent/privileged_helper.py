#!/usr/bin/env python3
"""Root helper that accepts one validated operation through standard input."""

import asyncio
import os
import sys

from pydantic import ValidationError

from system_agent import operations
from system_agent.dispatcher import dispatch_operation
from system_agent.protocol import OperationRequest, OperationResponse
from system_agent.terminal import terminal_main

MAX_REQUEST_BYTES = 1024 * 1024


def main() -> int:
    """Read, validate, execute, and serialize exactly one agent operation."""
    if sys.argv[1:] == ["--terminal"]:
        return terminal_main()
    if os.geteuid() != 0:
        print("Privileged helper must run as root", file=sys.stderr)
        return 2
    payload = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if not payload or len(payload) > MAX_REQUEST_BYTES:
        print("Invalid helper request size", file=sys.stderr)
        return 2
    try:
        request = OperationRequest.model_validate_json(payload)
        result = asyncio.run(dispatch_operation(request))
    except ValidationError:
        print("Invalid helper request", file=sys.stderr)
        return 2
    except operations.CommandError:
        print("Privileged command failed", file=sys.stderr)
        return 2
    except OSError:
        print("Host operation failed", file=sys.stderr)
        return 2
    except (TypeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sys.stdout.write(
        OperationResponse(operation=request.operation, result=result).model_dump_json()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
