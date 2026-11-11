"""Owned, output-bounded subprocesses with no captured stderr or interactive input."""

import asyncio
import os
import signal

from dayboard.core.models import ErrorCode
from dayboard.core.providers import ProviderUnavailable

MAX_OUTPUT_BYTES = 262_144


# This primitive enforces the supplied process budget internally with asyncio.timeout.
async def run_bounded(
    command: list[str],
    *,
    timeout: float,  # noqa: ASYNC109
    env: dict[str, str] | None = None,
    allowed_exit_codes: tuple[int, ...] = (0,),
) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=MAX_OUTPUT_BYTES,
        env=env,
        start_new_session=os.name == "posix",
    )
    try:
        async with asyncio.timeout(timeout):
            assert process.stdout is not None
            output = bytearray()
            while chunk := await process.stdout.read(16_384):
                output.extend(chunk)
                if len(output) > MAX_OUTPUT_BYTES:
                    raise ValueError("Process response exceeded the size limit.")
            if await process.wait() not in allowed_exit_codes:
                raise ProviderUnavailable(ErrorCode.UNAVAILABLE)
            return bytes(output)
    finally:
        # Descendants can retain stdout after the direct child exits. Each POSIX command
        # owns a new session, so group cleanup cannot target the caller's shell/session.
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        if process.returncode is None:
            await process.wait()
