"""
Delegation service FastAPI application.

Separate FastAPI app that runs on port 9090 on big nodes to handle
delegation requests from small nodes.
"""

import os
import argparse
from contextlib import asynccontextmanager

from fastapi import FastAPI
import uvicorn

from pow.service.delegation.server import DelegationManager
from pow.service.delegation.routes import router
from common.logger import create_logger

logger = create_logger(__name__)

# Default configuration
DEFAULT_PORT = 9090
DEFAULT_MAX_SESSIONS = 10


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan handler.

    Initializes DelegationManager on startup and cleans up on shutdown.
    """
    # Startup
    auth_token = app.state.auth_token
    max_sessions = app.state.max_sessions

    logger.info(
        f"Starting delegation service: "
        f"port={app.state.port}, max_sessions={max_sessions}"
    )

    # Initialize delegation manager
    app.state.delegation_manager = DelegationManager(
        auth_token=auth_token,
        max_sessions=max_sessions,
    )

    logger.info("Delegation manager initialized")

    yield

    # Shutdown
    logger.info("Shutting down delegation service")

    # Cleanup: stop all active sessions
    if hasattr(app.state, "delegation_manager"):
        manager: DelegationManager = app.state.delegation_manager
        status = manager.get_status()

        logger.info(
            f"Cleaning up {status['total_sessions']} sessions "
            f"({status['active_sessions']} active)"
        )

        # Stop all sessions
        for session_id in list(manager.sessions.keys()):
            try:
                session = manager.sessions.get(session_id)
                if session:
                    session.stop()
                    logger.info(f"Stopped session {session_id}")
            except Exception as e:
                logger.error(f"Error stopping session {session_id}: {e}")

    logger.info("Delegation service shutdown complete")


def create_app(
    auth_token: str,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    port: int = DEFAULT_PORT,
) -> FastAPI:
    """
    Create delegation FastAPI application.

    Args:
        auth_token: Required auth token for all delegation requests
        max_sessions: Maximum number of concurrent sessions
        port: Port to run on (stored in app.state for logging)

    Returns:
        FastAPI application instance
    """
    app = FastAPI(
        title="Delegation Service",
        description="PoC computation delegation service for big nodes",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Store configuration in app.state
    app.state.auth_token = auth_token
    app.state.max_sessions = max_sessions
    app.state.port = port

    # Include delegation routes
    app.include_router(router)

    logger.info(
        f"Delegation app created: "
        f"max_sessions={max_sessions}, port={port}"
    )

    return app


def main():
    """
    Main entry point for delegation service.

    Reads configuration from environment variables or command-line arguments
    and starts the FastAPI server.
    """
    parser = argparse.ArgumentParser(
        description="Delegation service for PoC computation"
    )
    parser.add_argument(
        "--auth-token",
        type=str,
        default=os.getenv("DELEGATION_AUTH_TOKEN"),
        help="Authentication token (env: DELEGATION_AUTH_TOKEN)",
        required=False,
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=int(os.getenv("DELEGATION_MAX_SESSIONS", DEFAULT_MAX_SESSIONS)),
        help=f"Maximum concurrent sessions (env: DELEGATION_MAX_SESSIONS, default: {DEFAULT_MAX_SESSIONS})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("DELEGATION_PORT", DEFAULT_PORT)),
        help=f"Port to run on (env: DELEGATION_PORT, default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("DELEGATION_HOST", "0.0.0.0"),
        help="Host to bind to (env: DELEGATION_HOST, default: 0.0.0.0)",
    )

    args = parser.parse_args()

    # Validate auth token
    if not args.auth_token:
        logger.error(
            "Auth token required! Set via --auth-token or DELEGATION_AUTH_TOKEN env var"
        )
        parser.print_help()
        return 1

    logger.info(
        f"Starting delegation service: "
        f"host={args.host}, port={args.port}, max_sessions={args.max_sessions}"
    )

    # Create app
    app = create_app(
        auth_token=args.auth_token,
        max_sessions=args.max_sessions,
        port=args.port,
    )

    # Run with uvicorn
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
    )

    return 0


if __name__ == "__main__":
    exit(main())
